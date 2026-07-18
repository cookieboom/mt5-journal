"""Deals -> trades. The hard milestone (M2), extended by the poller (M4) and
by excursion (M5).

A "trade" is not an MT5 object; it is the human idea *I entered here, I exited
there*, reconstructed by grouping deals on `position_id` (docs/mt5-deal-model.md §1).
This module is where that grouping lives, and where every trap that turns into a
silently-wrong number is paid for:

  - Trap 1  positive whitelist on BUY/SELL, explicit reject of a NULL position_id.
  - Trap 2/3 VWAP entry/exit over partial fills and closes; close_time = last OUT.
  - Trap 4/5 INOUT / OUT_BY raise NotImplementedError (impossible / never-seen on
             this hedging account) — never fall through the OUT path and corrupt VWAP.
  - Trap 6  sl_initial from the opening order; 0.0 and missing both mean NULL, not 0.
             M4 adds a second source (`sl_tp_snapshots`, the poller) that CAN
             positively confirm "no SL was ever set" as a real 0.0 (CLAUDE.md
             rule 4) — but that 0.0 must never reach `risk_amount()` as a price.
             See `_real_sl_price`.
  - Trap 8  an OUT with no IN is an orphan — skip and warn, never guess the entry.
  - Trap 9  net_profit sums profit+commission+swap+fee over EVERY deal in the group.
  - Trap 11 risk needs contract specs (domain/risk.py), not price distance.

`reconstruct()` is pure: hand it deals + an order map + a spec map (+ optionally a
poller-snapshot map) and it returns `Trade`s, no DB, no bridge (CLAUDE.md rule 7).
`rebuild()` is the DB orchestrator: it reads the append-only `_raw` tables, calls
`reconstruct()`, then DELETEs and re-INSERTs `trades` — never UPDATE (rule 2, §4
step 5). Enums come from the adapter; `domain/` holds no MT5 magic integer (rule 12).

M5 adds `_fill_excursions()`: a POST-`reconstruct()` step in `rebuild()`, not a
new `reconstruct()` parameter. MAE/MFE needs each trade's *already-computed*
open/close/duration to scope its candle query — that only exists once
`reconstruct()`'s pure loop has produced it, so this can't be threaded through
the same way M4's `snapshots` was. `Trade` is a mutable dataclass; `rebuild()`
sets `mae`/`mfe`/`mae_r`/`mfe_r` in place before the INSERT loop. See
`_fill_excursions` and `domain/excursion.py` for why the query MUST be scoped
per-trade (symbol + that trade's own timeframe + its own window) rather than a
bulk cross-trade scan: the central `candles` table pools every trade's window
on a symbol, and a global scan can silently pick up a different, disjoint
trade's cluster, or (on this hedging account) a coarser overlapping trade's
wider bar.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field

from ..adapter.base import Deal, DealEntry, DealType, Order
from ..render.chart import choose_timeframe, window_for
from ..store.db import now_ms, one_account_login
from .excursion import compute_excursion
from .risk import risk_amount
from .symbols import to_base
from .tags import compute_auto_tags

log = logging.getLogger(__name__)

# Float comparison slack for the volume-balance check (CLAUDE.md rule 5 — never ==).
_VOL_TOL = 1e-9


@dataclass(frozen=True)
class SymbolSpec:
    """The subset of `symbol_specs` reconstruction needs. `tick_value` is in ACCOUNT
    currency, not `currency_profit` (Trap 14)."""

    symbol: str
    symbol_base: str
    tick_size: float | None = None
    tick_value: float | None = None
    contract_size: float | None = None


@dataclass(frozen=True)
class SlTpSnapshot:
    """One poller observation of a live position's SL/TP (`sl_tp_snapshots`, M4).
    `observed_msc` is TRUE UTC — `now_ms()`, the poller's own wall clock — UNLIKE
    deal/order times, which are broker SERVER time (Trap 7). Never compare the two
    without offset-correcting first (see `_resolve_poller_price`'s docstring)."""

    observed_msc: int
    sl: float | None = None
    tp: float | None = None
    volume: float | None = None


@dataclass
class Trade:
    """One reconstructed trade. Mirrors the populated columns of the `trades` table.
    Money fields are in `accounts.currency` (USC) — never formatted with '$' (Trap 13).
    `sl_final`/`tp_final` are the M4 poller's job; `mae*`/`mfe*` are M5's — NULL here."""

    account_login: int | None
    position_id: int
    symbol: str
    symbol_base: str
    direction: str            # 'buy' | 'sell'
    status: str               # 'closed' | 'open' | 'partially_open'
    open_time_msc: int
    volume: float
    open_price: float
    net_profit: float
    commission: float
    swap: float
    profit_gross: float
    deal_count: int
    segment: int = 0          # always 0 on hedging (Trap 4)
    close_time_msc: int | None = None
    duration_s: int | None = None
    close_price: float | None = None
    sl_initial: float | None = None
    tp_initial: float | None = None
    sl_final: float | None = None      # M4 poller
    tp_final: float | None = None      # M4 poller
    sl_source: str | None = None       # 'order' | 'poller' | 'unknown'
    risk_amount: float | None = None
    r_multiple: float | None = None
    mae: float | None = None           # M5
    mfe: float | None = None           # M5
    mae_r: float | None = None         # M5
    mfe_r: float | None = None         # M5
    close_reason: int | None = None
    magic: int | None = None
    rebuilt_at: int | None = None


@dataclass(frozen=True)
class RebuildReport:
    account_login: int | None
    n_trades: int = 0
    n_closed: int = 0
    n_open: int = 0
    n_partial: int = 0
    n_with_sl: int = 0
    n_with_r: int = 0
    n_with_mae: int = 0
    skipped_orphans: int = 0


# --------------------------------------------------------------- pure core


def _is_trade_deal(d: Deal) -> bool:
    """Trap 1, as a POSITIVE whitelist with explicit NULL rejects. `position_id` is a
    bare truthiness test on purpose: `!= 0` would let a malformed `None` through, and
    every dataclass field defaults to None. `time_msc` must be present too — every
    downstream time computation (open_time, ordering) trusts it, and a `None` would
    otherwise silently sort as epoch-0 (1970). `deals_raw.time_msc` is NOT NULL, so
    this only rejects hand-built deals; the store never trips it."""
    return (
        d.type in (DealType.BUY, DealType.SELL)
        and bool(d.position_id)
        and d.time_msc is not None
    )


def _vwap(deals: list[Deal]) -> float:
    num = sum(d.price * d.volume for d in deals)
    den = sum(d.volume for d in deals)
    return num / den  # den > 0: a group only reaches here with real fills


def _sl_from_order(order: Order | None) -> float | None:
    """A price is a real level only when the order exists AND carries a non-zero SL.
    0.0 means 'not set on this order' (Trap 6) — ambiguous on its own (the trader
    may have added an SL seconds later; the order can't tell), so this returns
    NULL, never 0. `reconstruct()` asks the M4 poller next; only ITS data can
    positively confirm absence."""
    if order is None or order.sl is None or abs(order.sl) < _VOL_TOL:
        return None
    return order.sl


def _tp_from_order(order: Order | None) -> float | None:
    if order is None or order.tp is None or abs(order.tp) < _VOL_TOL:
        return None
    return order.tp


def _resolve_poller_price(
    snaps: list[SlTpSnapshot], field: str
) -> tuple[float | None, str | None]:
    """The earliest KNOWN state of `field` ('sl' or 'tp') from a chronological
    list of poller observations (M4). Priority:

      1. earliest NONZERO value — a real level, however/whenever it was set
         (Trap 6: "the actual first SL... regardless of how it was set").
      2. `0.0` — every observation showed none set, AND at least one exists.
         A positive confirmation (CLAUDE.md rule 4: "0 means none set"), not a
         guess — unlike the order-derived 0.0 in `_sl_from_order`, which is
         ambiguous because an order is only ONE snapshot, taken at entry.
      3. `(None, None)` — no poller coverage at all for this position.

    Known limitation, accepted rather than solved (see `reconstruct` docstring):
    if the poller's FIRST sample for a position arrives well after entry and
    happens to show 0, branch 2 still fires — there is no proximity-to-open_time
    guard. Building one correctly needs the server/UTC offset (`observed_msc` is
    poller wall-clock UTC; `open_time_msc` is broker SERVER time, Trap 7) and
    isn't worth the complexity for a case that degrades to `unknown`-equivalent
    (see `_real_sl_price`), never to a wrong statistic. If a trade you KNOW had
    an SL ever shows `sl_source='poller', sl_initial=0.0`, that's the signal to
    build the offset-corrected guard."""
    nonzero = [
        getattr(s, field)
        for s in snaps
        if getattr(s, field) is not None and abs(getattr(s, field)) > _VOL_TOL
    ]
    if nonzero:
        return nonzero[0], "poller"
    if snaps:
        return 0.0, "poller"
    return None, None


def _real_sl_price(sl: float | None) -> float | None:
    """The SL as a PRICE for risk math — NOT the same question as "what does
    trades.sl_initial store". A stored `0.0` means 'no SL was ever set' (rule 4
    / Trap 6), never a price near zero. Feeding it to `risk_amount()` would treat
    0 as a literal price level and return a huge, wrong number
    (`|open_price - 0| / tick_size * tick_value * volume`) on the first
    confirmed-no-SL trade the M4 poller produces. A real breakeven-at-entry SL
    is a nonzero price and passes through unchanged, so the known-zero-risk case
    (Trap 6's table) is untouched. Every value `_sl_from_order` could already
    produce (`None` or a real nonzero price) maps to itself here — this only
    starts doing new work once the poller can legitimately write a confirmed
    0.0, which no order-only path could before M4."""
    return sl if (sl is not None and abs(sl) > _VOL_TOL) else None


def reconstruct(
    deals: list[Deal],
    orders: dict[int, Order],
    specs: dict[str, SymbolSpec],
    account_login: int | None = None,
    snapshots: dict[int, list[SlTpSnapshot]] | None = None,
) -> list[Trade]:
    """Group trade deals by `position_id` and fold each group into a `Trade`.

    `orders` maps order ticket -> Order (source of sl_initial); `specs` maps symbol ->
    SymbolSpec (source of risk); `snapshots` maps position_id -> chronological poller
    observations (M4, `sl_tp_snapshots` — consulted only when `orders` gives nothing,
    see `_resolve_poller_price`). `snapshots` defaults to empty so every M2 call site
    that predates the poller keeps working unchanged. Orphans are skipped with a
    warning; INOUT/OUT_BY raise. Deterministic: deals within a group are ordered by
    (time_msc, ticket)."""
    groups: dict[int, list[Deal]] = {}
    for d in deals:
        if _is_trade_deal(d):
            groups.setdefault(d.position_id, []).append(d)

    trades: list[Trade] = []
    for pid in sorted(groups):
        # time_msc is guaranteed present by _is_trade_deal, so no `or 0` fallback —
        # that would have masked a bad deal as a 1970 timestamp (the silent error this
        # whole document exists to prevent). ticket breaks ties within the same ms.
        group = sorted(groups[pid], key=lambda d: (d.time_msc, d.ticket or 0))

        # Traps 4 & 5 — caught BEFORE the IN/OUT split. An INOUT/OUT_BY deal belongs
        # to neither `ins` nor `outs`, so letting it fall through would silently drop
        # it from the VWAP and emit a plausible-looking, wrong trade.
        for d in group:
            if d.entry == DealEntry.INOUT:
                raise NotImplementedError(
                    f"DEAL_ENTRY_INOUT on position_id={pid}: impossible on a hedging "
                    "account (Trap 4). If this fired, the account model is wrong."
                )
            if d.entry == DealEntry.OUT_BY:
                raise NotImplementedError(
                    f"DEAL_ENTRY_OUT_BY on position_id={pid}: never seen in this "
                    "account's history (Trap 5). Come back and implement it here."
                )

        ins = [d for d in group if d.entry == DealEntry.IN]
        outs = [d for d in group if d.entry == DealEntry.OUT]

        if not ins:  # Trap 8: OUT with no IN — window orphan. Do not guess the entry.
            log.warning(
                "orphan position_id=%s: %d OUT deal(s), no IN — skipped (Trap 8)",
                pid, len(outs),
            )
            continue

        symbol = ins[0].symbol
        spec = specs.get(symbol)

        vol_in = sum(d.volume for d in ins)
        vol_out = sum(d.volume for d in outs)
        if not outs:
            status = "open"
        elif abs(vol_in - vol_out) < _VOL_TOL:
            status = "closed"
        else:
            status = "partially_open"

        open_time = min(d.time_msc for d in ins)
        close_time = max((d.time_msc for d in outs), default=None)
        duration_s = (close_time - open_time) // 1000 if close_time is not None else None

        # sl_initial from the EARLIEST IN deal's order (group is time-ordered).
        in_orders = {d.order for d in ins if d.order}
        if len(in_orders) > 1:
            log.warning(
                "position_id=%s has IN deals across %d distinct orders %s; using the "
                "earliest. Unexpected on hedging — investigate.",
                pid, len(in_orders), sorted(in_orders),
            )
        open_order = orders.get(ins[0].order)
        sl_initial = _sl_from_order(open_order)
        tp_initial = _tp_from_order(open_order)
        sl_source = "order" if sl_initial is not None else None

        # M4: the order gave nothing (None) -> ask the poller. It CAN positively
        # confirm "no SL ever" as a real 0.0 (rule 4), where the order alone could
        # only ever be ambiguous. SL and TP resolve INDEPENDENTLY — a position can
        # have a real SL and no TP, or vice versa.
        snaps = (snapshots or {}).get(pid, [])
        if sl_initial is None:
            sl_initial, sl_source = _resolve_poller_price(snaps, "sl")
        if tp_initial is None:
            tp_initial, _ = _resolve_poller_price(snaps, "tp")
        if sl_source is None:
            sl_source = "unknown"

        open_price = _vwap(ins)
        volume = vol_in
        risk = (
            risk_amount(
                open_price, _real_sl_price(sl_initial),
                spec.tick_size if spec else None,
                spec.tick_value if spec else None,
                volume,
            )
            if spec is not None
            else None
        )

        # Trap 9: sum every cash component over the WHOLE group, not just the OUT deal.
        commission = sum(d.commission or 0.0 for d in group)
        swap = sum(d.swap or 0.0 for d in group)
        profit_gross = sum(d.profit or 0.0 for d in group)
        net_profit = profit_gross + commission + swap + sum(d.fee or 0.0 for d in group)

        # R only for a fully-closed trade: an open/partial trade's realised P&L is
        # incomplete, so net_profit/risk would be a lie (docs §5). risk itself is a
        # property of entry-vs-SL and stays known regardless.
        # Gate on `risk` being TRUTHY, not `is not None` (Trap 6 table): an SL exactly
        # at entry gives a KNOWN risk of 0.0 — risk_amount stays 0.0 (a real fact), but
        # R is undefined, so r_multiple is NULL. `risk is not None` would pass for 0.0
        # and raise ZeroDivisionError on the first breakeven-at-entry trade.
        r_multiple = (
            net_profit / risk
            if (status == "closed" and risk)
            else None
        )

        trades.append(Trade(
            account_login=account_login,
            position_id=pid,
            symbol=symbol,
            symbol_base=to_base(symbol),
            direction="buy" if ins[0].type == DealType.BUY else "sell",
            status=status,
            open_time_msc=open_time,
            close_time_msc=close_time,
            duration_s=duration_s,
            volume=volume,
            open_price=open_price,
            close_price=_vwap(outs) if outs else None,
            sl_initial=sl_initial,
            tp_initial=tp_initial,
            sl_source=sl_source,
            commission=commission,
            swap=swap,
            profit_gross=profit_gross,
            net_profit=net_profit,
            risk_amount=risk,
            r_multiple=r_multiple,
            close_reason=outs[-1].reason if outs else None,
            magic=ins[0].magic,
            deal_count=len(group),
        ))

    return trades


# ------------------------------------------------------------ DB orchestrator


# Only the typed columns are read back — NEVER raw_json (amendment 2). raw_json is a
# one-way archive of MT5's FULL dict; `Deal(**json.loads(raw_json))` would both put
# MT5 field names back into domain/ (rule 12) and TypeError the day MT5 adds a field
# — the very event raw_json exists to survive. Typed columns are the stable read path.


def _load_deals(conn: sqlite3.Connection, login: int) -> list[Deal]:
    rows = conn.execute(
        "SELECT ticket, order_ticket, position_id, symbol, type, entry, reason, "
        "magic, volume, price, commission, swap, profit, fee, time_msc "
        "FROM deals_raw WHERE account_login = ?",
        (login,),
    ).fetchall()
    return [
        Deal(
            ticket=r["ticket"], order=r["order_ticket"], position_id=r["position_id"],
            symbol=r["symbol"], type=r["type"], entry=r["entry"], reason=r["reason"],
            magic=r["magic"], volume=r["volume"], price=r["price"],
            commission=r["commission"], swap=r["swap"], profit=r["profit"],
            fee=r["fee"], time_msc=r["time_msc"],
        )
        for r in rows
    ]


def _load_orders(conn: sqlite3.Connection, login: int) -> dict[int, Order]:
    rows = conn.execute(
        "SELECT ticket, position_id, symbol, type, sl, tp, price_open "
        "FROM orders_raw WHERE account_login = ?",
        (login,),
    ).fetchall()
    return {
        r["ticket"]: Order(
            ticket=r["ticket"], position_id=r["position_id"], symbol=r["symbol"],
            type=r["type"], sl=r["sl"], tp=r["tp"], price_open=r["price_open"],
        )
        for r in rows
    }


def _load_specs(conn: sqlite3.Connection) -> dict[str, SymbolSpec]:
    rows = conn.execute(
        "SELECT symbol, symbol_base, tick_size, tick_value, contract_size "
        "FROM symbol_specs"
    ).fetchall()
    return {
        r["symbol"]: SymbolSpec(
            symbol=r["symbol"], symbol_base=r["symbol_base"],
            tick_size=r["tick_size"], tick_value=r["tick_value"],
            contract_size=r["contract_size"],
        )
        for r in rows
    }


def _load_sl_snapshots(conn: sqlite3.Connection, login: int) -> dict[int, list[SlTpSnapshot]]:
    """Every M4 poller observation, grouped by position_id, in chronological order
    (the SQL ORDER BY guarantees this — `_resolve_poller_price` needs the WHOLE
    ordered list to scan past leading zeros to the first real price, not just the
    single earliest row). `sl_tp_snapshots` is append-only and read-only from here
    — reconstruction never writes it."""
    rows = conn.execute(
        "SELECT position_id, observed_msc, sl, tp, volume FROM sl_tp_snapshots "
        "WHERE account_login = ? ORDER BY position_id, observed_msc",
        (login,),
    ).fetchall()
    out: dict[int, list[SlTpSnapshot]] = {}
    for r in rows:
        out.setdefault(r["position_id"], []).append(
            SlTpSnapshot(
                observed_msc=r["observed_msc"], sl=r["sl"], tp=r["tp"],
                volume=r["volume"],
            )
        )
    return out


def _fill_excursions(conn: sqlite3.Connection, trades: list[Trade]) -> int:
    """Mutates each CLOSED trade's mae/mfe/mae_r/mfe_r in place (M5). Scoped
    PER-TRADE: symbol, this trade's own `choose_timeframe(duration_s)` (the
    same TF `journal candles` fetched it at), and its own
    `window_for(open, close, tf)` bounds. NEVER a bulk cross-trade scan — the
    central `candles` table pools every trade's window on a symbol
    (schema.sql: "Dedupes across trades on the same symbol/day"), and this
    account is hedging (CLAUDE.md line 26): two overlapping trades of
    different durations can sit at different timeframes. A query scoped to
    THIS trade's own window at its own TF cannot pick up a different trade's
    disjoint cluster or a coarser overlapping trade's wider bar; a global
    "nearest preceding row anywhere" scan could.

    mae_r/mfe_r are PURE PRICE RATIOS, not money: risk_amount's tick_size/
    tick_value/volume all cancel in mae_money/risk_amount, leaving
    mae_r = mae / |open_price - real_sl|. No money conversion, no dependency
    on domain/risk.py. Guards the SAME ZeroDivisionError shape r_multiple
    already guards (Trap 6/M2.1): an SL exactly at entry gives a KNOWN zero
    risk_distance, not an unknown one -- gate on it being TRUTHY.

    Returns the count of trades that got a real (non-NULL) mae/mfe, for
    RebuildReport."""
    n_with_mae = 0
    for t in trades:
        if t.status != "closed":
            continue
        tf = choose_timeframe(t.duration_s)
        from_msc, to_msc = window_for(t.open_time_msc, t.close_time_msc, tf)
        rows = conn.execute(
            "SELECT time_msc, low, high FROM candles "
            "WHERE symbol = ? AND timeframe = ? AND time_msc BETWEEN ? AND ? "
            "ORDER BY time_msc",
            (t.symbol, tf, from_msc, to_msc),
        ).fetchall()
        mae, mfe = compute_excursion(
            [(r["time_msc"], r["low"], r["high"]) for r in rows],
            t.open_time_msc, t.close_time_msc, t.open_price, t.direction,
        )
        t.mae, t.mfe = mae, mfe
        if mae is not None:
            n_with_mae += 1

        real_sl = _real_sl_price(t.sl_initial)
        risk_distance = abs(t.open_price - real_sl) if real_sl is not None else None
        if risk_distance:  # truthy: not None AND not 0.0 (Trap 6 shape)
            if mae is not None:
                t.mae_r = mae / risk_distance
            if mfe is not None:
                t.mfe_r = mfe / risk_distance

    return n_with_mae


def _outlier_thresholds(net_profits: list[float]) -> tuple[float, float]:
    """(big_win_threshold, big_loss_threshold) = the top-/bottom-decile net_profit
    of the closed trades. Nearest-rank on the sorted values: the 90th-percentile
    value is the big-win floor, the 10th-percentile value the big-loss ceiling.
    Only ever called with >= _MIN_N values (see `_fill_auto_tags`), so the indices
    are always in range and a decile is a meaningful cut, not noise."""
    vals = sorted(net_profits)
    n = len(vals)
    lo = vals[int(round(0.1 * (n - 1)))]
    hi = vals[int(round(0.9 * (n - 1)))]
    return hi, lo


def _fill_auto_tags(conn: sqlite3.Connection, trades: list[Trade]) -> None:
    """Regenerate the `source='auto'` tag rows for every CLOSED trade (M6), a
    POST-`reconstruct()` step mirroring `_fill_excursions`: it runs inside the
    same `rebuild()` transaction, before the final commit.

    MANUAL-SAFE: the DELETE carries `source = 'auto'` so a user's `source='manual'`
    tags are never touched — dropping that filter would wipe the human layer on
    every rebuild (the very thing the position_id key exists to protect). Tags key
    on (account_login, position_id, segment), NEVER trades.id.

    §9-GATED: outlier thresholds are the account's decile net_profit, computed
    only when there are >= _MIN_N closed trades; below that, `None`/`None` are
    passed so no `big-win`/`big-loss` is applied against a sample too small to
    define an outlier. `compute_auto_tags` stays pure — thresholds flow IN."""
    from ..analytics.report import _MIN_N

    login = one_account_login(conn)
    closed = [t for t in trades if t.status == "closed"]
    if len(closed) >= _MIN_N:
        big_win, big_loss = _outlier_thresholds([t.net_profit for t in closed])
    else:
        big_win = big_loss = None

    conn.execute(
        "DELETE FROM tags WHERE account_login = ? AND source = 'auto'", (login,)
    )
    for t in closed:
        for tag in compute_auto_tags(
            t, big_win_threshold=big_win, big_loss_threshold=big_loss
        ):
            conn.execute(
                "INSERT OR IGNORE INTO tags "
                "(account_login, position_id, segment, tag, source) "
                "VALUES (?, ?, ?, ?, 'auto')",
                (t.account_login, t.position_id, t.segment, tag),
            )


def rebuild(conn: sqlite3.Connection) -> RebuildReport:
    """DELETE + re-INSERT `trades` for the account from the append-only `_raw` tables.
    NEVER UPDATE (rule 2): `trades` is fully derived and must be reproducible. One
    commit at the end. Reads typed columns only, never raw_json (amendment 2).

    MAE/MFE (M5) depends on `candles`, which `journal candles` only fetches for
    trades that already exist in `trades` -- so on a fresh account the natural
    order is `sync -> rebuild -> candles -> rebuild` (rebuild TWICE): the first
    run has nothing to compute excursion from yet, `candles` then fetches each
    closed trade's window, and a second `rebuild` picks the new coverage up.
    Safe to do -- rebuild is idempotent (M2.1-tested) -- and unavoidable even in
    steady state, since a newly-closed trade must be in `trades` before its
    window can be fetched at all."""
    login = one_account_login(conn)
    deals = _load_deals(conn, login)
    orders = _load_orders(conn, login)
    specs = _load_specs(conn)
    snapshots = _load_sl_snapshots(conn, login)

    trades = reconstruct(deals, orders, specs, account_login=login, snapshots=snapshots)
    n_with_mae = _fill_excursions(conn, trades)
    ts = now_ms()

    conn.execute("DELETE FROM trades WHERE account_login = ?", (login,))
    for t in trades:
        conn.execute(
            """
            INSERT INTO trades
                (account_login, position_id, segment, symbol, symbol_base, direction,
                 status, open_time_msc, close_time_msc, duration_s, volume, open_price,
                 close_price, sl_initial, tp_initial, sl_final, tp_final, sl_source,
                 commission, swap, profit_gross, net_profit, risk_amount, r_multiple,
                 mae, mfe, mae_r, mfe_r, close_reason, magic, deal_count, rebuilt_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.account_login, t.position_id, t.segment, t.symbol, t.symbol_base,
                t.direction, t.status, t.open_time_msc, t.close_time_msc, t.duration_s,
                t.volume, t.open_price, t.close_price, t.sl_initial, t.tp_initial,
                t.sl_final, t.tp_final, t.sl_source, t.commission, t.swap,
                t.profit_gross, t.net_profit, t.risk_amount, t.r_multiple, t.mae,
                t.mfe, t.mae_r, t.mfe_r, t.close_reason, t.magic, t.deal_count, ts,
            ),
        )
    # M6: regenerate auto tags in the SAME transaction (manual tags untouched).
    _fill_auto_tags(conn, trades)
    conn.commit()

    return RebuildReport(
        account_login=login,
        n_trades=len(trades),
        n_closed=sum(1 for t in trades if t.status == "closed"),
        n_open=sum(1 for t in trades if t.status == "open"),
        n_partial=sum(1 for t in trades if t.status == "partially_open"),
        n_with_sl=sum(1 for t in trades if t.sl_initial is not None),
        n_with_r=sum(1 for t in trades if t.r_multiple is not None),
        n_with_mae=n_with_mae,
    )
