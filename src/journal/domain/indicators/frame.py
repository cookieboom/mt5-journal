"""Candles -> the bar frame every builtin and the engine read."""
from __future__ import annotations

import pandas as pd

from ...adapter.base import Candle

COLUMNS = ("open", "high", "low", "close", "volume", "spread")


def to_frame(bars: list[Candle]) -> pd.DataFrame:
    """Float columns indexed by `time_msc`, ascending, one row per bar (last
    wins on a duplicate — a re-fetched bar is the corrected one). `volume` is
    `tick_volume`; it and `spread` stay NaN where the store says NULL
    (rule 4: unknown, never zero)."""
    df = pd.DataFrame(
        [(b.time_msc, b.open, b.high, b.low, b.close, b.tick_volume, b.spread)
         for b in bars],
        columns=["time_msc", *COLUMNS],
    )
    df = df.astype({"time_msc": "int64", **{c: "float64" for c in COLUMNS}})
    df = df.sort_values("time_msc", kind="stable").drop_duplicates("time_msc", keep="last")
    return df.set_index("time_msc")
