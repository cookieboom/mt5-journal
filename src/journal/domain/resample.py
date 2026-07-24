"""Pure OHLC aggregation: M1 bars → any coarser timeframe.

No I/O. Bucket boundaries are computed in SERVER time (the stored `time_msc`);
because epoch 0 is 1970-01-01 00:00 UTC and this broker's server clock is UTC
(server_utc_offset_s = 0, re-measured each sync), modulo-bucketing aligns D1 to
UTC midnight and H4 to 00/04/08/12/16/20:00. Never assume the offset elsewhere.
"""
from __future__ import annotations

from ..adapter.base import Candle, TIMEFRAMES

_M1 = 60_000
_TF_MS = {
    "M1": _M1, "M5": 300_000, "M15": 900_000,
    "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000,
}
assert set(_TF_MS) == set(TIMEFRAMES), "resample timeframes drifted from adapter.base.TIMEFRAMES"


def bucket_start(time_msc: int, timeframe: str) -> int:
    if timeframe not in _TF_MS:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(_TF_MS)}")
    size = _TF_MS[timeframe]
    return time_msc - (time_msc % size)


def timeframe_ms(timeframe: str) -> int:
    """Milliseconds per bar for `timeframe` (e.g. 'M5' -> 300_000)."""
    if timeframe not in _TF_MS:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(_TF_MS)}")
    return _TF_MS[timeframe]


def _bucket_fully_covered(bstart: int, size: int, covered: list[tuple[int, int]]) -> bool:
    # Every M1 open in [bstart, bstart+size) must sit inside one covered interval.
    # The last possible M1 open in the bucket is bstart + size - _M1.
    last_open = bstart + size - _M1
    return any(a <= bstart and b >= last_open for a, b in covered)


def resample_m1(
    m1: list[Candle],
    timeframe: str,
    covered: list[tuple[int, int]] | None = None,
) -> list[Candle]:
    """Aggregate M1 `Candle`s into `timeframe`. M1 → M1 is identity (sorted).

    When `covered` is given, a bucket is emitted only if its whole span is
    covered — a partially-covered bucket is dropped rather than emitted with a
    wrong high/low/close (the correctness guard).
    """
    if timeframe not in _TF_MS:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(_TF_MS)}")
    size = _TF_MS[timeframe]
    ordered = sorted(m1, key=lambda c: c.time_msc)
    if timeframe == "M1":
        return ordered

    groups: dict[int, list[Candle]] = {}
    for c in ordered:
        groups.setdefault(bucket_start(c.time_msc, timeframe), []).append(c)

    out: list[Candle] = []
    for bstart in sorted(groups):
        if covered is not None and not _bucket_fully_covered(bstart, size, covered):
            continue
        bars = groups[bstart]
        tv = sum((b.tick_volume or 0) for b in bars)
        rv = sum((b.real_volume or 0) for b in bars)
        out.append(
            Candle(
                time_msc=bstart,
                open=bars[0].open,
                high=max(b.high for b in bars),
                low=min(b.low for b in bars),
                close=bars[-1].close,
                tick_volume=tv,
                spread=None,          # charts don't need it; not meaningful post-merge
                real_volume=rv,
            )
        )
    return out
