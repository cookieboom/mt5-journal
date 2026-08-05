"""Ingest raw deals/orders/specs and verify the balance invariant.

`sync(client, conn)` pulls MT5's history into the `_raw` tables and is the
archival heart of the project: the broker deletes history (Trap 16), so once a
deal lands here it must never leave. Writes are `INSERT OR IGNORE` — append-only,
no UPDATE, no DELETE, ever.

It asks the bridge only for the window since the last watermark; `full=True`
(what `journal sync` uses) asks from 2000. Narrowing the window can never lose a
stored deal — deals_raw is append-only, so the window decides only what is
OFFERED — but it does bound what the archive detector can see, hence the split.
Every bridge call happens before the write transaction opens; see `sync`.

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

from ..adapter.base import Account, DealType, MT5Client
from ..domain.symbols import to_base
from ..store.db import now_ms, one_account_login

# First backfill takes everything (Trap 8: use datetime(2000,1,1) as `from`), and
# `sync(full=True)` goes back here on demand.
_EPOCH_FROM = datetime(2000, 1, 1, tzinfo=timezone.utc)

# Every later sync asks only for [watermark - _LOOKBACK_MS, now]. A full pull from
# 2000 was measured at ~3m45s per run on this bridge — the dominant cost of the
# on-close ingest freeze, and paid again on every position close. The lookback is
# deliberately generous (a week, not a minute): deals settle late, swaps and
# corrections land after the fact, and re-offering a deal we already hold costs
# exactly one ignored INSERT. deals_raw is append-only, so a window only decides
# what is OFFERED — nothing already stored can be lost by narrowing it.
_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000

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
    # Start of the history window this sync actually asked the bridge for. 0 on a
    # full pull. Reported because "why did that sync take four minutes" is
    # answerable from it and from nothing else.
    history_from_msc: int = 0
    # Tickets this journal holds in deals_raw that the broker no longer returns —
    # the real archive detector (Trap 16 / §6). Non-empty is NOT an error: it is
    # proof the journal is the only surviving copy of those deals. The balance
    # invariant cannot see this (archiving moves no money), so `sync` reports it.
    archived_tickets: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class VerifyResult:
    account_login: int | None
    # Identity 1 (§6): sum(deal cash) - sum(recon) == balance. Proves ingest dropped
    # or duplicated nothing.
    deals_cash: float
    reconciled: float
    balance: float
    residual: float          # identity-1 residual
    passed1: bool
    # Identity 2 (§6): the PARTITION check. Proves reconstruct() lost or double-counted
    # nothing — sum(trades.net_profit, ALL statuses) + sum(non-trade deal cash) - recon.
    trades_net: float
    nontrade_cash: float
    trade_deals_count: int   # BUY/SELL deals with a position_id in deals_raw
    trades_count: int
    residual2: float | None  # identity-2 residual; None when it cannot be evaluated
    # 'ok'      identity 2 balances
    # 'fail'    it does not — OR trades is empty while trade deals exist (amendment 1:
    #           the catastrophic reconstruct()->[] case, failed as loudly as possible)
    # 'not_run' nothing to reconstruct yet (no trades AND no trade deals) — not a failure
    id2_state: str
    passed: bool             # OVERALL: identity 1 holds AND identity 2 did not fail


# --------------------------------------------------------------------- sync


def sync(
    client: MT5Client, conn: sqlite3.Connection, *, full: bool = False
) -> SyncReport:
    """Pull account/deals/orders/specs into the store. Idempotent: re-running only
    inserts deals/orders not already captured. One commit at the end.

    Two phases, on purpose — the same shape, and the same reason, as
    `candle_fill.fill_range`: every bridge call happens FIRST, with no write
    transaction open, and only then does one short transaction do the local
    writes. The interleaved version held SQLite's single WAL writer slot across
    five-plus bridge round-trips, so a `journal serve` in another process blocked
    on every INSERT for the whole ~4-minute pull. Never hold the write lock across
    a bridge call. Phase 1 may only read.

    `full=True` asks the bridge for all history from 2000 — the manual
    `journal sync`, and the only mode in which the archive detector can see the
    whole journal. The default asks only for the window since the last watermark,
    which is what the on-close pipeline in `journal live` uses.
    """
    ts = now_ms()

    # ------------------------------------------------------------------ phase 1
    # Bridge and read-only SQL. Nothing here may write.
    acct = client.account_info()
    if acct is None:
        raise RuntimeError(
            "account_info() returned None — bridge up but not logged in?"
        )
    login = acct.login

    from_dt, from_ms = (_EPOCH_FROM, 0) if full else _history_window(conn, login)
    now_utc = datetime.now(timezone.utc)
    deals = client.history_deals_get(from_dt, now_utc)
    orders = client.history_orders_get(from_dt, now_utc)

    # Spec every symbol that shows up on a trade deal (non-trade deals carry '').
    # BTCUSDc/EURUSDc included — gold's specs do NOT transfer (trap 11/14). A
    # window with no deals in it re-specs nothing: specs already stored stay, and
    # they only go stale where trading is actually happening.
    symbols = sorted({d.symbol for d in deals if d.symbol})
    specs = _fetch_symbol_specs(client, symbols)

    offset_s = _measure_offset(client)  # Trap 7: measured, never hardcoded

    # Archive detector (Trap 16): tickets we HOLD minus tickets the broker RETURNED.
    # Safe to read BEFORE ingest: deals_raw is append-only, so held-after is exactly
    # held-before ∪ returned, and subtracting `returned` gives the same survivors
    # either way — this run's own deals cancel out regardless. What remains is deals
    # from earlier syncs the broker has since deleted. The balance invariant is blind
    # to this — archiving moves no money — so it lives here, not in verify.
    archived = _detect_archived(conn, login, deals, from_ms)

    # ------------------------------------------------------------------ phase 2
    # Local writes only, one short transaction.
    _upsert_account(conn, acct, ts)
    deals_new, deals_wm = _ingest_deals(conn, deals, login, ts)
    orders_new, orders_wm = _ingest_orders(conn, orders, login, ts)
    specced = _write_symbol_specs(conn, specs, ts)
    _upsert_sync_state(conn, login, "deals", deals_wm, offset_s, ts)
    _upsert_sync_state(conn, login, "orders", orders_wm, offset_s, ts)

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
        history_from_msc=from_ms,
        archived_tickets=archived,
    )


def _history_window(conn, login) -> tuple[datetime, int]:
    """(date_from for the bridge, its epoch-ms twin) — the last watermark minus
    `_LOOKBACK_MS`. Falls back to the full history whenever either stream has no
    usable watermark yet, so a first sync, a restored backup, and a half-written
    `sync_state` all self-heal into a complete pull rather than a silent hole.

    Anchored to the WATERMARK, never to `now`: a week of `journal live` downtime
    widens the window by a week instead of skipping it. That is the whole reason
    the anchor is not `now - lookback`."""
    marks = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT stream, last_synced_msc FROM sync_state "
            "WHERE account_login = ? AND stream IN ('deals', 'orders')",
            (login,),
        )
    }
    got = [marks.get("deals"), marks.get("orders")]
    if any(m is None or m <= 0 for m in got):
        return _EPOCH_FROM, 0
    from_ms = min(got) - _LOOKBACK_MS
    return datetime.fromtimestamp(from_ms / 1000, tz=timezone.utc), from_ms


def _detect_archived(conn, login, returned_deals, from_ms: int) -> list[int]:
    """{ticket held in deals_raw, inside the window} - {ticket returned this sync}.
    Sorted for a stable report.

    The window scoping is not an optimisation: a windowed sync never ASKS for older
    deals, so their absence from the answer says nothing at all. Comparing against
    the whole journal would report the entire pre-window history as archived on
    every live sync. Only `sync(full=True)` sees everything, and only there can the
    detector speak about everything. Deals with a NULL `time_msc` sort as 0, so they
    are visible on a full pull (from_ms=0) and never flagged by a windowed one."""
    held = {
        r[0]
        for r in conn.execute(
            "SELECT ticket FROM deals_raw "
            "WHERE account_login = ? AND COALESCE(time_msc, 0) >= ?",
            (login, from_ms),
        )
    }
    returned = {d.ticket for d in returned_deals if d.ticket is not None}
    return sorted(held - returned)


def _upsert_account(conn: sqlite3.Connection, acct: Account, ts: int) -> None:
    # accounts is a mutable snapshot (not _raw), so upsert is fine. `first_seen_at`
    # is preserved on conflict; everything else refreshes. is_demo from trade_mode
    # (0=demo 1=contest 2=real). balance/equity are captured HERE, by the same sync
    # that captured the deals, so `verify` can check the §6 invariant with no bridge
    # (both halves come from time T).
    is_demo = 1 if acct.trade_mode == 0 else 0
    equity = acct.equity
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


def _fetch_symbol_specs(client: MT5Client, symbols) -> list[tuple]:
    """Phase 1 half of spec ingest: bridge calls only, no writes. Symbols the
    bridge cannot describe are dropped here rather than stored half-known."""
    out = []
    for sym in symbols:
        info = client.symbol_info(sym)
        if info is not None:
            out.append((sym, info))
    return out


def _write_symbol_specs(conn, specs, ts) -> list[str]:
    """Specs are refetched (brokers change them), so this upserts rather than
    ignores. `tick_value` is in ACCOUNT currency, not currency_profit (trap 14)."""
    specced: list[str] = []
    for sym, info in specs:
        conn.execute(
            """
            INSERT INTO symbol_specs
                (symbol, symbol_base, digits, point, tick_size, tick_value,
                 contract_size, currency_profit, fetched_at,
                 volume_min, volume_max, volume_step, stops_level,
                 freeze_level, trade_mode, filling_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                symbol_base     = excluded.symbol_base,
                digits          = excluded.digits,
                point           = excluded.point,
                tick_size       = excluded.tick_size,
                tick_value      = excluded.tick_value,
                contract_size   = excluded.contract_size,
                currency_profit = excluded.currency_profit,
                fetched_at      = excluded.fetched_at,
                volume_min      = excluded.volume_min,
                volume_max      = excluded.volume_max,
                volume_step     = excluded.volume_step,
                stops_level     = excluded.stops_level,
                freeze_level    = excluded.freeze_level,
                trade_mode      = excluded.trade_mode,
                filling_mode    = excluded.filling_mode
            """,
            (
                sym, to_base(sym), info.digits, info.point, info.trade_tick_size,
                info.trade_tick_value, info.trade_contract_size,
                info.currency_profit, ts,
                # M9: the order-validation group. A spec stored before M9 has
                # these as NULL until the next sync overwrites them — unknown,
                # not zero, and `domain/commands.py` refuses to validate against
                # an unknown spec rather than assuming a permissive default.
                info.volume_min, info.volume_max, info.volume_step,
                info.trade_stops_level, info.trade_freeze_level,
                info.trade_mode, info.filling_mode,
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
            -- Monotonic, never backwards. A windowed sync that happens to return
            -- nothing carries watermark=NULL; letting that overwrite a real mark
            -- would make the NEXT sync fall back to the full 2000-onwards pull —
            -- the exact cost this window exists to avoid. MAX() is NULL if either
            -- side is, hence the COALESCE chain: max when both are known, the
            -- known one when only one is, NULL only when neither ever was.
            last_synced_msc     = COALESCE(
                                      MAX(excluded.last_synced_msc, sync_state.last_synced_msc),
                                      excluded.last_synced_msc,
                                      sync_state.last_synced_msc),
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
    """The §6 balance invariant — BOTH identities. Pure SQL against the store — takes
    NO client, needs no bridge (Trap 16: this journal, not MT5, is the durable record;
    verify must run against a backup, in CI, or with the broker down). READ-ONLY:
    SELECTs only, writes nothing, and NEVER auto-inserts a reconciliation.

        identity 1: sum(deal cash) - sum(recon) == balance          (ingest integrity)
        identity 2: sum(trades.net_profit, ALL statuses)
                    + sum(non-trade deal cash) - sum(recon) == balance   (reconstruction)

    Identity 1 passing while identity 2 fails localises the bug to `reconstruct.py`,
    not ingest — which is exactly why both are computed and reported separately.

    `balance` is the SNAPSHOT `sync` captured alongside the deals (accounts.balance),
    NOT a live read: both halves of each invariant must come from the same instant, or
    a position closing between sync and verify makes the residual garbage.

    Archiving is invisible here (it moves no money, so the residual never budges) — the
    archive detector lives in `sync`, not this function. Do not claim otherwise."""
    login = one_account_login(conn)  # single-source guard (store/db.py)
    (balance,) = conn.execute(
        "SELECT balance FROM accounts WHERE login = ?", (login,)
    ).fetchone()

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

    # Identity 2. A "trade deal" is exactly reconstruct()'s Trap-1 whitelist: a BUY/SELL
    # deal with a non-zero position_id. The BUY/SELL values come from OUR enum, so no
    # MT5 magic integer lands in ingest (rule 12). The non-trade complement must match
    # that filter precisely, or the partition would leak.
    buy, sell = int(DealType.BUY), int(DealType.SELL)
    (trade_deals_count,) = conn.execute(
        "SELECT COUNT(*) FROM deals_raw "
        "WHERE account_login = ? AND type IN (?, ?) AND position_id != 0",
        (login, buy, sell),
    ).fetchone()
    (nontrade_cash,) = conn.execute(
        "SELECT COALESCE(SUM(profit + commission + swap + fee), 0.0) FROM deals_raw "
        "WHERE account_login = ? AND NOT (type IN (?, ?) AND position_id != 0)",
        (login, buy, sell),
    ).fetchone()
    (trades_count,) = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_login = ?", (login,),
    ).fetchone()
    (trades_net,) = conn.execute(
        "SELECT COALESCE(SUM(net_profit), 0.0) FROM trades WHERE account_login = ?",
        (login,),
    ).fetchone()

    # Amendment 1: distinguish "nothing to reconstruct" from "reconstruction produced
    # nothing while deals exist" — the latter is the worst failure and must never hide
    # behind a bland "not run".
    if trades_count == 0 and trade_deals_count == 0:
        id2_state, residual2 = "not_run", None
    elif trades_count == 0 and trade_deals_count > 0:
        id2_state, residual2 = "fail", None
    else:
        residual2 = trades_net + nontrade_cash - reconciled - balance
        id2_state = "ok" if abs(residual2) < _TOLERANCE else "fail"

    passed1 = abs(residual) < _TOLERANCE
    return VerifyResult(
        account_login=login,
        deals_cash=deals_cash,
        reconciled=reconciled,
        balance=balance,
        residual=residual,
        passed1=passed1,
        trades_net=trades_net,
        nontrade_cash=nontrade_cash,
        trade_deals_count=trade_deals_count,
        trades_count=trades_count,
        residual2=residual2,
        id2_state=id2_state,
        passed=passed1 and id2_state != "fail",
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
