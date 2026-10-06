"""Which timeframe a trade is looked at in, and how much context surrounds it.

Pure arithmetic, no I/O. Shared by reconstruction (MAE/MFE read candles in this
window), candle ingest (fetches exactly this window) and the PNG renderer (draws
it) — so it lives in `domain/`, below all three.
"""

from __future__ import annotations

from ..adapter.base import TIMEFRAMES
from .resample import timeframe_ms

# Finest TF where the trade spans <= this many bars. Measured 2026-07-17
# (docs/mt5-deal-model.md §7): median trade is 7 M1 bars, p75 20, max 685 --
# M15 draws the MEDIAN trade as a single candle, which is not a chart. This cap
# only escalates the tail (durations past ~1h) off M1; in practice M1/M5/M15 are
# the only timeframes this account's history ever picks.
MAX_TRADE_BARS = 60

# Fixed context on each side, in bars of the CHOSEN tf. Combined with the
# <=MAX_TRADE_BARS cap on the trade itself, every window lands in
# [1 + 2*15, 60 + 2*15] = [31, 90] bars -- inside the ~20-90 readable band,
# with no proportional math needed.
PAD_BARS = 15


def choose_timeframe(duration_s: int) -> str:
    """Finest TF where the trade spans <= MAX_TRADE_BARS bars, floor D1."""
    for tf in TIMEFRAMES:
        if duration_s * 1000 <= timeframe_ms(tf) * MAX_TRADE_BARS:
            return tf
    return TIMEFRAMES[-1]


def window_for(
    open_msc: int, close_msc: int, tf: str, pad_bars: int = PAD_BARS,
) -> tuple[int, int]:
    """+/- `pad_bars` bars of context around [open_msc, close_msc] at `tf`
    granularity. Epoch-ms, SERVER time (no zone conversion here)."""
    pad_ms = pad_bars * timeframe_ms(tf)
    return open_msc - pad_ms, close_msc + pad_ms
