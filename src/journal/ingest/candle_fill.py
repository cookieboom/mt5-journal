"""The one module that fetches candles from the bridge. Used by `journal live`
(to drain the request queue) and by `journal candles-warm`. NEVER imported by
web/ — that is what keeps the M9 bridge boundary intact.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..adapter.base import MT5Client
from ..store import candles_store as cs
from ..store import candle_queue as _queue


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def fill_range(client: MT5Client, conn: sqlite3.Connection, symbol: str,
               timeframe: str, from_ms: int, to_ms: int) -> int:
    """Fetch only the UNCOVERED sub-ranges of [from_ms, to_ms] from the bridge,
    insert the bars, and record coverage (even for ranges that return zero bars,
    so a genuinely-empty span is never re-fetched). Idempotent. Returns bars_new.
    One commit at the end."""
    covered = cs.read_coverage(conn, symbol, timeframe)
    bars_new = 0
    for lo, hi in cs.missing_ranges(covered, (from_ms, to_ms)):
        bars = client.copy_rates_range(symbol, timeframe, _ms_to_dt(lo), _ms_to_dt(hi))
        for c in bars:
            bars_new += cs.insert_candle(conn, symbol, timeframe, c)
        cs.record_coverage(conn, symbol, timeframe, lo, hi)
    conn.commit()
    return bars_new


def fulfill_request(client: MT5Client, conn: sqlite3.Connection, req: sqlite3.Row) -> int:
    """Run a claimed request through fill_range; mark done (or failed + re-raise).
    Returns bars_new."""
    try:
        bars = fill_range(client, conn, req["symbol"], req["timeframe"],
                          req["from_msc"], req["to_msc"])
    except Exception as e:
        _queue.mark_failed(conn, int(req["id"]), str(e))
        raise
    _queue.mark_done(conn, int(req["id"]), bars)
    return bars
