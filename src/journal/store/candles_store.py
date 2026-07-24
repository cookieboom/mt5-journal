"""The pure-DB candle store: read bars, insert bars, track coverage. NO bridge,
NO MT5 — safe to import from web/ and render/. The bridge-touching fill lives in
ingest/candle_fill.py.

Coverage is a minimal set of disjoint inclusive [from_msc, to_msc] ranges per
(symbol, timeframe): what has actually been fetched, so a genuinely-empty range
(market closed) is remembered and never re-fetched.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import Candle

# Below this, a bar time_msc is SECONDS that leaked through the adapter boundary
# (Trap 15), never a value to convert here. Same tripwire as ingest/candles.py.
_MSC_FLOOR = 10**12


def insert_candle(conn: sqlite3.Connection, symbol: str, timeframe: str, c: Candle) -> int:
    """INSERT OR IGNORE one bar. `time_msc` is written straight through — the
    ×1000 already happened at the adapter boundary. Returns 1 if newly inserted,
    0 if the PK (symbol, timeframe, time_msc) already existed."""
    if c.time_msc is None or c.time_msc < _MSC_FLOOR:
        raise ValueError(
            f"candle time_msc={c.time_msc!r} for {symbol} {timeframe} is below "
            f"{_MSC_FLOOR} — looks like SECONDS leaked through (Trap 15). Fix the "
            "adapter boundary, not this module; it must never do its own ×1000."
        )
    cur = conn.execute(
        "INSERT OR IGNORE INTO candles "
        "(symbol, timeframe, time_msc, open, high, low, close, tick_volume, spread, real_volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, timeframe, c.time_msc, c.open, c.high, c.low, c.close,
         c.tick_volume, c.spread, c.real_volume),
    )
    return cur.rowcount


def read_candles(conn: sqlite3.Connection, symbol: str, timeframe: str,
                 from_ms: int, to_ms: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT time_msc, open, high, low, close, tick_volume, spread, real_volume "
        "FROM candles WHERE symbol = ? AND timeframe = ? AND time_msc BETWEEN ? AND ? "
        "ORDER BY time_msc",
        (symbol, timeframe, from_ms, to_ms),
    ).fetchall()


def row_to_candle(r: sqlite3.Row) -> Candle:
    return Candle(
        time_msc=r["time_msc"], open=r["open"], high=r["high"], low=r["low"],
        close=r["close"], tick_volume=r["tick_volume"], spread=r["spread"],
        real_volume=r["real_volume"],
    )


def read_coverage(conn: sqlite3.Connection, symbol: str, timeframe: str) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT from_msc, to_msc FROM candle_coverage "
        "WHERE symbol = ? AND timeframe = ? ORDER BY from_msc",
        (symbol, timeframe),
    ).fetchall()
    return [(int(r["from_msc"]), int(r["to_msc"])) for r in rows]


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1:              # overlap or adjacent (gap ≤ 1ms)
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def record_coverage(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int) -> None:
    """Merge [from_ms, to_ms] into stored coverage and rewrite the disjoint set.
    Caller commits (fill_range / ingest do one commit)."""
    merged = _merge(read_coverage(conn, symbol, timeframe) + [(from_ms, to_ms)])
    conn.execute("DELETE FROM candle_coverage WHERE symbol = ? AND timeframe = ?", (symbol, timeframe))
    conn.executemany(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES (?, ?, ?, ?)",
        [(symbol, timeframe, a, b) for a, b in merged],
    )


def missing_ranges(covered: list[tuple[int, int]], want: tuple[int, int]) -> list[tuple[int, int]]:
    """Inclusive integer ranges in `want` not covered by `covered`."""
    lo, hi = want
    if lo > hi:
        return []
    result: list[tuple[int, int]] = []
    cursor = lo
    for a, b in sorted(covered):
        if b < cursor:
            continue
        if a > hi:
            break
        if a > cursor:
            result.append((cursor, min(a - 1, hi)))
        cursor = max(cursor, b + 1)
        if cursor > hi:
            break
    if cursor <= hi:
        result.append((cursor, hi))
    return result
