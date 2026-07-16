"""Ingest raw deals/orders/specs and verify the balance invariant.

`sync(client, conn)` pulls everything MT5 knows into the `_raw` tables and is the
archival heart of the project: the broker deletes history (Trap 16), so once a
deal lands here it must never leave. Writes are `INSERT OR IGNORE` — append-only,
no UPDATE, no DELETE, ever.

`verify(conn)` is pure SQL and READ-ONLY: it proves nothing was dropped or
double-counted via the §6 balance invariant, against the balance SNAPSHOT `sync`
stored — no bridge, no client. Trap 16 says this journal, not MT5, is the durable
record; an invariant that needed the broker reachable would contradict that.

`sync` takes an `MT5Client` by parameter (never constructs `LiveMT5Client`), so the
whole path runs under `FakeMT5Client` with no bridge (CLAUDE.md rule 1).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..adapter.base import Account, MT5Client
from ..domain.symbols import to_base
from ..store.db import now_ms

# First backfill takes everything (Trap 8: use datetime(2000,1,1) as `from`). This
# broker has ~140 deals total, so a full pull every sync is cheap and self-heals
# any window gap; incremental windowing is a later optimisation, not needed here.
_EPOCH_FROM = datetime(2000, 1, 1, tzinfo=timezone.utc)

# The invariant's slack. NOT a gap-swallowing tolerance (that is exactly the
# anti-pattern §6 forbids) — just float-comparison noise. Real discrepancies get a
# reconciliations row; they never hide under 0.01.
_TOLERANCE = 0.01

# Offset is measured against this symbol's server tick (Trap 7). It exists on this
# server and trades an active session; a stale tick simply yields no offset.
_OFFSET_SYMBOL = "XAUUSDc"


@dataclass(frozen=True)
class SyncReport:
    account_login: int | None = None
    deals_seen: int = 0
    deals_new: int = 0
    deals_existing: int = 0
    orders_seen: int = 0
    orders_new: int = 0
    orders_existing: int = 0
    symbols_specced: list[str] = field(default_factory=list)
    server_utc_offset_s: int | None = None
    offset_measured: bool = False
    deals_watermark_msc: int | None = None
    orders_watermark_msc: int | None = None
    # Tickets this journal holds in deals_raw that the broker no longer returns —
    # the real archive detector (Trap 16 / §6). Non-empty is NOT an error: it is
    # proof the journal is the only surviving copy of those deals. The balance
    # invariant cannot see this (archiving moves no money), so `sync` reports it.
    archived_tickets: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class VerifyResult:
    account_login: int | None
    deals_cash: float
    reconciled: float
    balance: float
    residual: float
    passed: bool


# --------------------------------------------------------------------- sync


def sync(client: MT5Client, conn: sqlite3.Connection) -> SyncReport:
    """Pull account/deals/orders/specs into the store. Idempotent: re-running only
    inserts deals/orders not already captured. One commit at the end."""
    ts = now_ms()

    acct = client.account_info()
    if acct is None:
        raise RuntimeError(
            "account_info() returned None — bridge up but not logged in?"
        )
    login = acct.login
    _upsert_account(conn, acct, ts)

    now_utc = datetime.now(timezone.utc)
    deals = client.history_deals_get(_EPOCH_FROM, now_utc)
    orders = client.history_orders_get(_EPOCH_FROM, now_utc)

    deals_new, deals_wm = _ingest_deals(conn, deals, login, ts)
    orders_new, orders_wm = _ingest_orders(conn, orders, login, ts)

    # Spec every symbol that shows up on a trade deal (non-trade deals carry '').
    # BTCUSDc/EURUSDc included — gold's specs do NOT transfer (trap 11/14).
    symbols = sorted({d.symbol for d in deals if d.symbol})
    specced = _ingest_symbol_specs(conn, client, symbols, ts)

    offset_s = _measure_offset(client)  # Trap 7: measured, never hardcoded
    _upsert_sync_state(conn, login, "deals", deals_wm, offset_s, ts)
    _upsert_sync_state(conn, login, "orders", orders_wm, offset_s, ts)

    # Archive detector (Trap 16): tickets we HOLD minus tickets the broker RETURNED.
    # Read held AFTER ingest so this sync's own deals are on both sides and cancel;
    # what remains is deals from earlier syncs the broker has since deleted. The
    # balance invariant is blind to this — archiving moves no money — so it lives
    # here, not in verify. Empty today; a tripwire for later.
    archived = _detect_archived(conn, login, deals)

    conn.commit()

    return SyncReport(
        account_login=login,
        deals_seen=len(deals),
        deals_new=deals_new,
        deals_existing=len(deals) - deals_new,
        orders_seen=len(orders),
        orders_new=orders_new,
        orders_existing=len(orders) - orders_new,
        symbols_specced=specced,
        server_utc_offset_s=offset_s,
        offset_measured=offset_s is not None,
        deals_watermark_msc=deals_wm,
        orders_watermark_msc=orders_wm,
        archived_tickets=archived,
    )


def _detect_archived(conn, login, returned_deals) -> list[int]:
    """{ticket held in deals_raw} - {ticket returned this sync}. Sorted for a
    stable report. Must run after `_ingest_deals` so freshly-returned deals are in
    both sets and cancel — the survivors are deals the broker deleted (Trap 16)."""
    held = {
        r[0]
        for r in conn.execute(
            "SELECT ticket FROM deals_raw WHERE account_login = ?", (login,)
        )
    }
    returned = {d.ticket for d in returned_deals if d.ticket is not None}
    return sorted(held - returned)


def _upsert_account(conn: sqlite3.Connection, acct: Account, ts: int) -> None:
    # accounts is a mutable snapshot (not _raw), so upsert is fine. `first_seen_at`
    # is preserved on conflict; everything else refreshes. is_demo from trade_mode
    # (0=demo 1=contest 2=real). balance/equity are captured HERE, by the same sync
    # that captured the deals, so `verify` can check the §6 invariant with no bridge
    # (both halves come from time T). `equity` has no typed field on Account — read
    # it from the raw _asdict() dump, the blessed carrier for un-modelled fields.
    is_demo = 1 if acct.trade_mode == 0 else 0
    equity = acct.raw.get("equity")
    conn.execute(
        """
        INSERT INTO accounts
            (login, broker, server, currency, leverage, margin_mode, is_demo,
             balance, equity, first_seen_at, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(login) DO UPDATE SET
            broker         = excluded.broker,
            server         = excluded.server,
            currency       = excluded.currency,
            leverage       = excluded.leverage,
            margin_mode    = excluded.margin_mode,
            is_demo        = excluded.is_demo,
            balance        = excluded.balance,
            equity         = excluded.equity,
            last_synced_at = excluded.last_synced_at
        """,
        (
            acct.login, acct.company, acct.server, acct.currency, acct.leverage,
            acct.margin_mode, is_demo, acct.balance, equity, ts, ts,
        ),
    )


def _ingest_deals(conn, deals, login, ts) -> tuple[int, int | None]:
    """INSERT OR IGNORE every deal — append-only, absolute (Trap 16). Returns
    (rows newly inserted, max time_msc seen). rowcount is 1 on insert, 0 on ignore."""
    new = 0
    watermark: int | None = None
    for d in deals:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO deals_raw
                (account_login, ticket, order_ticket, position_id, symbol, type,
                 entry, reason, magic, volume, price, commission, swap, profit,
                 fee, time_msc, comment, external_id, raw_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                login, d.ticket, d.order, d.position_id, d.symbol, d.type, d.entry,
                d.reason, d.magic, d.volume, d.price, d.commission, d.swap,
                d.profit, d.fee, d.time_msc, d.comment, d.external_id,
                json.dumps(d.raw), ts,
            ),
        )
        new += cur.rowcount
        if d.time_msc is not None and (watermark is None or d.time_msc > watermark):
            watermark = d.time_msc
    return new, watermark


def _ingest_orders(conn, orders, login, ts) -> tuple[int, int | None]:
    """orders_raw is mandatory: the only source of sl_initial (Trap 6). Same
    append-only INSERT OR IGNORE as deals."""
    new = 0
    watermark: int | None = None
    for o in orders:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO orders_raw
                (account_login, ticket, position_id, position_by_id, symbol, type,
                 state, reason, magic, volume_initial, volume_current, price_open,
                 sl, tp, price_stoplimit, time_setup_msc, time_done_msc, comment,
                 external_id, raw_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                login, o.ticket, o.position_id, o.position_by_id, o.symbol, o.type,
                o.state, o.reason, o.magic, o.volume_initial, o.volume_current,
                o.price_open, o.sl, o.tp, o.price_stoplimit, o.time_setup_msc,
                o.time_done_msc, o.comment, o.external_id, json.dumps(o.raw), ts,
            ),
        )
        new += cur.rowcount
        for tmsc in (o.time_done_msc, o.time_setup_msc):
            if tmsc and (watermark is None or tmsc > watermark):
                watermark = tmsc
    return new, watermark


def _ingest_symbol_specs(conn, client: MT5Client, symbols, ts) -> list[str]:
    """Specs are refetched (brokers change them), so this upserts rather than
    ignores. `tick_value` is in ACCOUNT currency, not currency_profit (trap 14)."""
    specced: list[str] = []
    for sym in symbols:
        info = client.symbol_info(sym)
        if info is None:
            continue
        conn.execute(
            """
            INSERT INTO symbol_specs
                (symbol, symbol_base, digits, point, tick_size, tick_value,
                 contract_size, currency_profit, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                symbol_base     = excluded.symbol_base,
                digits          = excluded.digits,
                point           = excluded.point,
                tick_size       = excluded.tick_size,
                tick_value      = excluded.tick_value,
                contract_size   = excluded.contract_size,
                currency_profit = excluded.currency_profit,
                fetched_at      = excluded.fetched_at
            """,
            (
                sym, to_base(sym), info.digits, info.point, info.trade_tick_size,
                info.trade_tick_value, info.trade_contract_size,
                info.currency_profit, ts,
            ),
        )
        specced.append(sym)
    return specced


def _upsert_sync_state(conn, login, stream, watermark, offset_s, ts) -> None:
    conn.execute(
        """
        INSERT INTO sync_state
            (account_login, stream, last_synced_msc, server_utc_offset_s, measured_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(account_login, stream) DO UPDATE SET
            last_synced_msc     = excluded.last_synced_msc,
            -- Trap 6 (NULL != known): a sync with no fresh tick measures offset=NULL
            -- (market closed / Sunday / fake). COALESCE keeps the last real reading
            -- instead of erasing it. `0` is a measured value, not NULL — preserved.
            server_utc_offset_s = COALESCE(excluded.server_utc_offset_s, sync_state.server_utc_offset_s),
            measured_at         = excluded.measured_at
        """,
        (login, stream, watermark, offset_s, ts),
    )


def _measure_offset(client: MT5Client, symbol: str = _OFFSET_SYMBOL) -> int | None:
    """Trap 7: measure the server/UTC offset every sync — never hardcode 0, even
    though it is 0 today, or a DST-shifting server would silently corrupt two weeks
    a year. Returns the offset snapped to 15 min, or None when there is no fresh
    tick to measure against (e.g. under the fake, or market closed). The age/offset
    circularity is accepted as in `doctor`: this account measured 0 vs fresh ticks."""
    tick = client.symbol_info_tick(symbol)
    if tick is None or tick.time is None:
        return None
    return round((tick.time - time.time()) / 900) * 900


# ------------------------------------------------------------------- verify


def verify(conn: sqlite3.Connection) -> VerifyResult:
    """The §6 balance invariant. Pure SQL against the store — takes NO client, needs
    no bridge (Trap 16: this journal, not MT5, is the durable record; verify must run
    against a backup, in CI, or with the broker down). READ-ONLY: SELECTs only, writes
    nothing, and NEVER auto-inserts a reconciliation.

        residual = sum(deal cash) - sum(reconciliations.amount) - balance

    `balance` is the SNAPSHOT `sync` captured alongside the deals (accounts.balance),
    NOT a live read: both halves of the invariant must come from the same instant, or
    a position closing between sync and verify makes the residual garbage.

    Archiving is invisible here (it moves no money, so the residual never budges) — the
    archive detector lives in `sync`, not this function. Do not claim otherwise."""
    row = conn.execute("SELECT login, balance FROM accounts").fetchall()
    if not row:
        raise RuntimeError("no account in the store — run `journal sync` first.")
    if len(row) > 1:
        raise RuntimeError(
            f"multiple accounts present {[r['login'] for r in row]}; "
            "disambiguation not yet supported."
        )
    login = row[0]["login"]
    balance = row[0]["balance"]

    (deals_cash,) = conn.execute(
        "SELECT COALESCE(SUM(profit + commission + swap + fee), 0.0) "
        "FROM deals_raw WHERE account_login = ?",
        (login,),
    ).fetchone()
    (reconciled,) = conn.execute(
        "SELECT COALESCE(SUM(amount), 0.0) FROM reconciliations WHERE account_login = ?",
        (login,),
    ).fetchone()

    residual = deals_cash - reconciled - balance
    return VerifyResult(
        account_login=login,
        deals_cash=deals_cash,
        reconciled=reconciled,
        balance=balance,
        residual=residual,
        passed=abs(residual) < _TOLERANCE,
    )


# ---------------------------------------------------------------- reconcile


def add_reconciliation(
    conn: sqlite3.Connection,
    account_login: int,
    amount: float,
    effective_msc: int | None,
    reason: str,
    evidence: str | None,
) -> int:
    """Insert one named explanation for a residual. Status is 'explained' because a
    reason is being supplied by a human (a machine-detected, unexplained gap would
    be inserted elsewhere with status='unexplained'). Returns the new row id."""
    ts = now_ms()
    cur = conn.execute(
        """
        INSERT INTO reconciliations
            (account_login, amount, effective_msc, status, reason, evidence, created_at)
        VALUES (?, ?, ?, 'explained', ?, ?, ?)
        """,
        (account_login, amount, effective_msc, reason, evidence, ts),
    )
    conn.commit()
    return int(cur.lastrowid)
