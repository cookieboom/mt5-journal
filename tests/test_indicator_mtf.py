"""tf() mapping: an HTF bar [T, T+H) shows on chart bar [t, t+L) only once it
CLOSED, T+H <= t+L — never the forming HTF bar (the MTF lookahead trap)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from journal.domain.indicators.engine import map_closed

from indicator_helpers import MINUTE

M5, H1 = 5 * MINUTE, 60 * MINUTE


def htf(hours: list[int]) -> pd.Series:
    return pd.Series([float(h) for h in hours], index=[h * H1 for h in hours], dtype="float64")


def chart(start_h: float, end_h: float) -> pd.Index:
    return pd.Index(np.arange(int(start_h * H1), int(end_h * H1), M5), dtype="int64")


def at(out: pd.Series, hours: float, minutes: int = 0) -> float:
    return float(out.loc[int(hours * H1) + minutes * MINUTE])


def test_htf_bar_shows_from_the_chart_bar_that_closes_with_it():
    out = map_closed(htf([0, 1]), H1, chart(0, 2), M5)
    assert math.isnan(at(out, 0, 50))          # H1 bar 0 still forming
    assert at(out, 0, 55) == 0.0               # closes 01:00 with it: exact boundary
    assert at(out, 1, 50) == 0.0               # bar 1 not closed yet
    assert at(out, 1, 55) == 1.0


def test_forming_chart_bar_never_sees_the_htf_bar_closing_at_its_close():
    idx = chart(0, 1)
    out = map_closed(htf([0]), H1, idx, M5, forming_msc=int(idx[-1]))
    assert math.isnan(at(out, 0, 55))


def test_carries_forward_across_a_market_closure():
    idx = chart(0, 3).append(chart(51, 52))    # weekend: no chart bars either
    out = map_closed(htf([0, 1, 2, 51]), H1, idx, M5)
    assert at(out, 51, 0) == 2.0 and at(out, 51, 50) == 2.0
    assert at(out, 51, 55) == 51.0


def test_unknown_once_more_than_two_htf_bars_are_missing():
    out = map_closed(htf([0, 1]), H1, chart(0, 7), M5)
    assert at(out, 3, 55) == 1.0               # 2h, 3h missing: still carried
    assert math.isnan(at(out, 4, 55))          # 2h, 3h, 4h missing: stale


def test_empty_htf_is_all_unknown():
    out = map_closed(htf([]), H1, chart(0, 1), M5)
    assert out.isna().all() and len(out) == 12
