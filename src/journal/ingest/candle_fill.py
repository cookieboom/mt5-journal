"""The one module that fetches candles from the bridge. Used by `journal live`
(to drain the request queue) and by `journal candles-warm`. NEVER imported by
web/ — that is what keeps the M9 bridge boundary intact.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..adapter.base import MT5Client
from ..domain.resample import bucket_start
from ..store import candles_store as cs
from ..store import candle_queue as _queue


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def fill_range(client: MT5Client, conn: sqlite3.Connection, symbol: str,
               timeframe: str, from_ms: int, to_ms: int, now_msc: int) -> int:
    """Fetch only the UNCOVERED sub-ranges of [from_ms, to_ms] from the bridge,
    insert the bars, and record coverage (even for ranges that return zero bars,
    so a genuinely-empty span is never re-fetched). Idempotent. Returns bars_new.
    One commit at the end.

    Two phases, on purpose: fetch every gap from the bridge FIRST (no write
    transaction open), then do all the local writes in one short transaction. The
    inline version held SQLite's single WAL writer slot across the per-gap
    network round-trip — so a multi-gap backfill in `journal live` starved the
    web's `request_candles` INSERT past its 5 s busy_timeout and it 500'd with
    "database is locked". Never hold the write lock across a bridge call.

    `to_ms` can reach into the still-forming bucket (the live chart's forward-
    loader requests up to Date.now()) — copy_rates_range then answers with that
    bucket's partial-so-far OHLC, not its final value. Unlike `serve_watches`
    (which routes such a bar to the forming table), this path has no forming
    table to route it to, so any bar at/after `now_msc`'s bucket is dropped
    outright rather than frozen into `candles` — INSERT OR IGNORE would never
    let the real closed bar overwrite it once `journal live` fetches it later."""
    cur_bucket = bucket_start(now_msc, timeframe)
    covered = cs.read_coverage(conn, symbol, timeframe)
    # Phase 1 — network only, no write lock held.
    fetched: list[tuple[int, int, list]] = []
    for lo, hi in cs.missing_ranges(covered, (from_ms, to_ms)):
        bars = client.copy_rates_range(symbol, timeframe, _ms_to_dt(lo), _ms_to_dt(hi))
        fetched.append((lo, hi, bars))
    # Phase 2 — local writes only, one short transaction.
    bars_new = 0
    for lo, hi, bars in fetched:
        stored: list[int] = []
        for c in bars:
            if c.time_msc is None or c.time_msc >= cur_bucket:
                continue                     # still forming — not a closed bar yet
            bars_new += cs.insert_candle(conn, symbol, timeframe, c)
            stored.append(c.time_msc)
        cs.record_fetch(conn, symbol, timeframe, lo, hi, stored, now_msc)
    conn.commit()
    return bars_new


def fulfill_request(client: MT5Client, conn: sqlite3.Connection, req: sqlite3.Row,
                    now_msc: int) -> int:
    """Run a claimed request through fill_range; mark done (or failed + re-raise).
    Returns bars_new."""
    try:
        bars = fill_range(client, conn, req["symbol"], req["timeframe"],
                          req["from_msc"], req["to_msc"], now_msc)
    except Exception as e:
        _queue.mark_failed(conn, int(req["id"]), str(e))
        raise
    _queue.mark_done(conn, int(req["id"]), bars)
    return bars
