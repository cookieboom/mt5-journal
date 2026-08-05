"""Ingest OHLC candles for reconstructed trades into the central `candles` store.

Mirrors `ingest/deals.py`: takes an `MT5Client` by parameter, never constructs
`LiveMT5Client` (CLAUDE.md rule 1), so `sync_candles` runs under `FakeMT5Client`
with no bridge. For each CLOSED trade, fetches the render window
(`render.chart.choose_timeframe` / `window_for`) at the trade's own chosen
timeframe via `candle_fill.fill_range`, which consults `candle_coverage` first
and asks the bridge only for the ranges not already stored. A trade whose
window is fully covered costs one SELECT and no bridge call at all.
Overlapping windows from nearby trades on the same symbol just collide
harmlessly on that key; central storage means a bar fetched for one trade is
free for its neighbours (schema.sql: "Dedupes across trades on the same
symbol/day").

RANJAU 1 (docs/mt5-deal-model.md Trap 15): the seconds->ms x1000 conversion
ALREADY happened at the adapter boundary (`live.py` / `fake.py` both do
`int(r["time"]) * 1000`). `candle.time_msc` reaches `candles_store.insert_candle`
with NO further arithmetic -- that store's magnitude check is a TRIPWIRE against
a regression (a second x1000 landing before storage), never a conversion. A
second x1000 turns 1752624000000 into 1752624000000000; the render window query
then matches zero rows and the chart comes back empty with no error.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..adapter.base import MT5Client
from ..render.chart import choose_timeframe, window_for
from ..store import candles_store
from ..store.db import now_ms, one_account_login
from .candle_fill import fill_range

# How many windows ONE invocation may fetch from the bridge. `journal live` runs
# this pipeline inside its serial cycle, so an unbounded backlog (first run after
# coverage was introduced; a restart after days offline) would stall the forming
# bar and the liveness beat for as long as the backlog takes. Five windows is
# roughly twelve seconds against a ~2.5 s round trip IF each window has a single
# gap — a window with fragmented coverage costs one round trip per gap in
# `missing_ranges`, so that estimate is a floor, not a bound. A knob to tune
# against measured bridge latency, not a derived constant. `journal candles`
# passes max_windows=None to prime a backlog in one deliberate, foreground run.
_MAX_FETCH_WINDOWS = 5


@dataclass(frozen=True)
class CandlesReport:
    account_login: int | None = None
    trades_seen: int = 0            # closed trades processed this run
    trades_skipped_open: int = 0    # open/partially_open -- no close_time yet
    bars_new: int = 0               # bars actually inserted (post PK-dedupe)
    windows_fetched: int = 0        # windows `fill_range` was CALLED for this run --
                                     # one call, but one bridge round trip per gap in
                                     # that window's missing_ranges, so this undercounts
                                     # actual round trips whenever coverage is fragmented
    windows_pending: int = 0        # windows left untouched because the cap closed
    symbols: list[str] = field(default_factory=list)


def sync_candles(
    client: MT5Client,
    conn: sqlite3.Connection,
    *,
    max_windows: int | None = _MAX_FETCH_WINDOWS,
) -> CandlesReport:
    """Fetch and store candles for every closed trade's render window. Idempotent
    and additive: coverage is consulted first, so a window already stored costs no
    bridge call. Newest close first, and at most `max_windows` windows are fetched
    per invocation (`None` = no cap) — the rest are reported as
    `windows_pending` and picked up next run."""
    login = one_account_login(conn)

    (total_trades,) = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_login = ?", (login,)
    ).fetchone()
    rows = conn.execute(
        "SELECT symbol, open_time_msc, close_time_msc, duration_s FROM trades "
        "WHERE account_login = ? AND status = 'closed' "
        "ORDER BY close_time_msc DESC",
        (login,),
    ).fetchall()

    trades_seen = 0
    bars_new = 0
    windows_fetched = 0
    windows_pending = 0
    symbols_touched: set[str] = set()
    # A window runs to close + PAD_BARS, so a trade that closed moments ago has a
    # window reaching into the future: the bridge answers with bars only up to
    # the present and `record_fetch` claims no further (2026-08-05 hole — a trade
    # closed 21:34, sync ran 21:43, and the whole-range claim sealed 21:44-21:49
    # as fetched forever).
    now = now_ms()

    for r in rows:
        tf = choose_timeframe(r["duration_s"])
        from_msc, to_msc = window_for(r["open_time_msc"], r["close_time_msc"], tf)
        # Coverage decides whether this window costs a bridge round trip at all.
        # This USED to fetch unconditionally: 123 closed trades on this account
        # meant 123 round trips on every single position close, ~5 minutes, and
        # `journal live` is one serial loop — the forming bar, the liveness beat,
        # and any queued order all sat behind it.
        covered = candles_store.read_coverage(conn, r["symbol"], tf)
        needs_fetch = bool(candles_store.missing_ranges(covered, (from_msc, to_msc)))
        if needs_fetch and max_windows is not None and windows_fetched >= max_windows:
            # Deferred, not skipped: the next invocation picks it up. Counted so
            # the caller can say a backlog remains instead of looking complete.
            windows_pending += 1
            continue
        trades_seen += 1
        symbols_touched.add(r["symbol"])
        if needs_fetch:
            # fill_range re-reads coverage internally. That duplicate SELECT is
            # the price of keeping its signature honest, and it is cheap next to
            # the network call it replaces.
            bars_new += fill_range(
                client, conn, r["symbol"], tf, from_msc, to_msc, now
            )
            windows_fetched += 1

    return CandlesReport(
        account_login=login,
        trades_seen=trades_seen,
        trades_skipped_open=total_trades - len(rows),
        bars_new=bars_new,
        windows_fetched=windows_fetched,
        windows_pending=windows_pending,
        symbols=sorted(symbols_touched),
    )
