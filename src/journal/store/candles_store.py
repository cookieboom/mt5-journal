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
    """Store one bar, keeping whichever version has the most ticks. `time_msc`
    is written straight through — the ×1000 already happened at the adapter
    boundary. Returns 1 if the row was written (inserted, or repaired by a
    fuller version), 0 if what we already had was as good or better.

    Monotone in `tick_volume` rather than plain OR IGNORE, because a bar can
    reach us mid-formation and be stored as if final: the feed itself may still
    be catching up (2026-08-05 — the Mac woke from sleep and the terminal was
    still backfilling history, so 08:05 arrived with 22 of its eventual 302
    ticks). OR IGNORE froze that stub forever. A snapshot with strictly more
    ticks is strictly closer to the closed bar, so it wins; a thinner one that
    arrives late never downgrades what is already stored."""
    if c.time_msc is None or c.time_msc < _MSC_FLOOR:
        raise ValueError(
            f"candle time_msc={c.time_msc!r} for {symbol} {timeframe} is below "
            f"{_MSC_FLOOR} — looks like SECONDS leaked through (Trap 15). Fix the "
            "adapter boundary, not this module; it must never do its own ×1000."
        )
    cur = conn.execute(
        "INSERT INTO candles "
        "(symbol, timeframe, time_msc, open, high, low, close, tick_volume, spread, real_volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol, timeframe, time_msc) DO UPDATE SET "
        "open = excluded.open, high = excluded.high, low = excluded.low, "
        "close = excluded.close, tick_volume = excluded.tick_volume, "
        "spread = excluded.spread, real_volume = excluded.real_volume "
        "WHERE COALESCE(excluded.tick_volume, 0) > COALESCE(candles.tick_volume, 0)",
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


def load_bars(conn: sqlite3.Connection, symbol: str, timeframe: str,
              from_ms: int, to_ms: int) -> list[Candle]:
    """Bars in [from_ms, to_ms], ascending: native rows if stored, else
    aggregated from M1 over BUCKET-ALIGNED bounds (resample_m1's coverage guard
    assumes it is handed every M1 bar for any bucket it may emit), else []. Pure
    DB — no bridge (M9 boundary). The single bar-read shared by the /api/candles
    payload and the Phase D replay step."""
    from ..domain.resample import resample_m1, bucket_start, timeframe_ms

    native = read_candles(conn, symbol, timeframe, from_ms, to_ms)
    if native:
        return [row_to_candle(r) for r in native]
    if timeframe == "M1":
        return []
    lo = bucket_start(from_ms, timeframe)
    hi = bucket_start(to_ms, timeframe) + timeframe_ms(timeframe) - 1
    m1 = read_candles(conn, symbol, "M1", lo, hi)
    if not m1:
        return []
    return resample_m1([row_to_candle(r) for r in m1], timeframe,
                       covered=read_coverage(conn, symbol, "M1"))


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
    if from_ms > to_ms:
        return                                       # reversed range: no-op (mirror missing_ranges)
    merged = _merge(read_coverage(conn, symbol, timeframe) + [(from_ms, to_ms)])
    conn.execute("DELETE FROM candle_coverage WHERE symbol = ? AND timeframe = ?", (symbol, timeframe))
    conn.executemany(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES (?, ?, ?, ?)",
        [(symbol, timeframe, a, b) for a, b in merged],
    )


def forget_coverage(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int) -> list[tuple[int, int]]:
    """Punch [from_ms, to_ms] out of the stored coverage so the fill path offers
    it again. Returns the new set. Bars are untouched — coverage is only a memo
    of what has been fetched, so forgetting too much costs a bridge call, never
    data. Repair hatch for holes sealed before `record_fetch` existed; caller
    commits."""
    kept: list[tuple[int, int]] = []
    for a, b in read_coverage(conn, symbol, timeframe):
        if b < from_ms or a > to_ms:
            kept.append((a, b))
            continue
        if a < from_ms:
            kept.append((a, from_ms - 1))
        if b > to_ms:
            kept.append((to_ms + 1, b))
    conn.execute("DELETE FROM candle_coverage WHERE symbol = ? AND timeframe = ?",
                 (symbol, timeframe))
    conn.executemany(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES (?, ?, ?, ?)",
        [(symbol, timeframe, a, b) for a, b in kept])
    return kept


def record_fetch(conn: sqlite3.Connection, symbol: str, timeframe: str,
                 from_ms: int, to_ms: int, bar_times: list[int], now_msc: int) -> None:
    """Record what one bridge fetch of [from_ms, to_ms] actually established.
    `bar_times` are the open times of the closed bars it returned.

    A response can be TRUNCATED rather than complete: the terminal is still
    backfilling history, or the range simply runs past the present. Claiming the
    whole requested span regardless seals those minutes as fetched —
    `missing_ranges` never offers them again and the chart keeps a permanent
    price gap while the data-health panel reads 100%. Both live holes found on
    2026-08-05 were this: `fill_range` asked [07:48, 08:13] on wake from sleep
    and got bars only to 08:05; `sync_candles` asked to close+PAD_BARS for a
    trade that had just closed and got bars only to the present.

    So: claim to the end of the LAST bar that came back, and never into the
    still-forming bucket. A gap with bars on BOTH sides stays claimed — that is
    the bridge's final word about a span it has data around (a weekend), and
    re-fetching it forever is exactly what coverage exists to prevent.

    ponytail: a response of ZERO bars still claims the whole range, because
    "market closed all weekend" and "terminal not synced yet" look identical from
    here. Narrow it (refuse to claim within N bars of `now_msc`) if a whole-range
    hole ever shows up."""
    from ..domain.resample import bucket_start, timeframe_ms

    hi = min(to_ms, bucket_start(now_msc, timeframe) - 1)
    if bar_times:
        hi = min(hi, max(bar_times) + timeframe_ms(timeframe) - 1)
    if hi >= from_ms:
        record_coverage(conn, symbol, timeframe, from_ms, hi)


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
