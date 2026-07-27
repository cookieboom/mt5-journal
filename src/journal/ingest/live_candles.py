"""The bridge-touching realtime-candle serve step for `journal live`. Like
candle_fill.py it may call the bridge, so web/ must NEVER import it.

Each active watch: fetch the last few bars, keep the bar whose bucket contains
`now` as the forming bar (overwrite live_candles), and promote every older,
now-closed bar into the append-only `candles` table (+ coverage).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..adapter.base import MT5Client
from ..domain.resample import bucket_start, timeframe_ms
from ..store import candles_store as cs
from ..store import live_store as ls


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def serve_watches(client: MT5Client, conn: sqlite3.Connection, now_msc: int,
                  *, lookback_bars: int = 3) -> int:
    """Serve every active watch once. Returns how many forming bars were written."""
    written = 0
    for symbol, tf in ls.active_watches(conn, now_msc):
        size = timeframe_ms(tf)
        frm = now_msc - (lookback_bars + 1) * size
        bars = client.copy_rates_range(symbol, tf, _ms_to_dt(frm), _ms_to_dt(now_msc))
        cur_bucket = bucket_start(now_msc, tf)
        for c in bars:
            if c.time_msc is None:
                continue
            if c.time_msc >= cur_bucket:
                ls.upsert_forming(conn, symbol, tf, c, now_msc)   # forming
                written += 1
            else:
                cs.insert_candle(conn, symbol, tf, c)             # closed → promote
        # Record coverage over the CLOSED span we just fetched, unconditionally —
        # even a zero-bar fetch (e.g. a watch left open across a weekend/holiday)
        # must be remembered, so a genuinely-empty closed slice is never re-fetched.
        cs.record_coverage(conn, symbol, tf, frm, cur_bucket - 1)
        conn.commit()
    return written
