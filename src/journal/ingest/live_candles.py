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
        # `now_msc` is stamped at the top of live_cycle and only reaches here
        # after positions_get/poll_once/mirror-write — hundreds of ms with a
        # position open, seconds when the bridge is slow. Across a rollover it
        # therefore still points at the PREVIOUS bucket while the bridge already
        # returns the new bar, and judging "forming" by the clock alone marks the
        # just-closed bar as forming too. The newer bar then overwrites it in
        # live_candles and it is never promoted: the chart's history freezes one
        # bar behind, and the live bar vanishes (mergeForming refuses a two-
        # interval jump) until the next rollover. A bar with a NEWER bar beside
        # it in the same response is closed no matter what the clock says, so
        # only the newest bar can ever be the forming one.
        newest = max((c.time_msc for c in bars if c.time_msc is not None), default=None)
        forming_at = cur_bucket if newest is None else max(cur_bucket, newest)
        if newest is not None and newest < forming_at:
            # A bucket with no ticks in it (a symbol outside its session, the
            # seconds right after a rollover, a sparse response at the live
            # edge): the bridge answered, there is simply no bar to write, and
            # without this `updated_msc` would freeze on a healthy feed —
            # `execute._check_feed_fresh` then refuses every open on the symbol
            # 15 s later. An EMPTY response is deliberately not stamped: that is
            # the bridge going blind, which is what the guard exists to catch.
            ls.touch_forming(conn, symbol, tf, now_msc)
        closed: list = []
        for c in bars:
            if c.time_msc is None:
                continue
            if c.time_msc >= forming_at:
                ls.upsert_forming(conn, symbol, tf, c, now_msc)   # forming
                written += 1
            else:
                cs.insert_candle(conn, symbol, tf, c)             # closed → promote
                closed.append(c)
        if not closed:
            # A watch left open across a weekend/holiday: the bridge has no
            # closed bar for the whole window. That must still be remembered
            # as covered, or the same genuinely-empty slice gets re-fetched
            # every live cycle forever.
            cs.record_coverage(conn, symbol, tf, frm, cur_bucket - 1)
        else:
            # copy_rates_range can legitimately come back SPARSE near the live
            # edge — the broker/bridge hasn't finalized every recent minute
            # yet — without the window being genuinely empty. Recording
            # coverage over the full [frm, cur_bucket-1] span regardless would
            # hide that hole forever: insert_candle's OR IGNORE never gets a
            # second chance once coverage falsely claims the span complete,
            # so candle_queue never re-fetches the missing bar. Only claim the
            # run actually verified contiguous.
            closed.sort(key=lambda c: c.time_msc)
            lo = hi = closed[0].time_msc
            for c in closed[1:]:
                if c.time_msc - hi > size:
                    break
                hi = c.time_msc
            cs.record_coverage(conn, symbol, tf, lo, hi + size - 1)
        conn.commit()
    return written
