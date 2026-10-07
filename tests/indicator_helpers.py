"""Shared bar fixtures for the indicator tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from journal.adapter.base import Candle
from journal.domain.indicators.frame import to_frame

MINUTE = 60_000


def walk(n: int, seed: int = 7, *, volume_gaps: bool = False) -> pd.DataFrame:
    """Seeded random-walk OHLC, M1, spanning more than one UTC day."""
    rng = np.random.default_rng(seed)
    close = 2000 + np.cumsum(rng.normal(0, 1.5, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, 0.8, (2, n)))
    bars = []
    for i in range(n):
        vol = None if volume_gaps and i % 97 == 5 else int(rng.integers(1, 200))
        bars.append(Candle(
            time_msc=i * MINUTE, open=float(open_[i]),
            high=float(max(open_[i], close[i]) + wick[0][i]),
            low=float(min(open_[i], close[i]) - wick[1][i]),
            close=float(close[i]), tick_volume=vol, spread=20, real_volume=0,
        ))
    return to_frame(bars)


def s(values) -> pd.Series:
    return pd.Series(values, dtype="float64")


def same(a: pd.Series, b: pd.Series) -> bool:
    a, b = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
    both_nan = np.isnan(a) & np.isnan(b)
    return bool(np.all(both_nan | (np.abs(a - b) < 1e-9)))
