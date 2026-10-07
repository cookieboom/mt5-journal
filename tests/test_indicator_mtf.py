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


# --- evaluate(): tf() wired into the engine ----------------------------------

import pytest  # noqa: E402

from journal.domain.indicators import builtins as B  # noqa: E402
from journal.domain.indicators.engine import evaluate  # noqa: E402
from journal.domain.indicators.frame import to_frame  # noqa: E402
from journal.domain.indicators.lang import ScriptError, parse  # noqa: E402
from journal.domain.resample import resample_m1  # noqa: E402

from indicator_helpers import same, walk_candles  # noqa: E402

M1 = walk_candles(3000)                     # 50 whole hours
F5, F60 = to_frame(resample_m1(M1, "M5")), to_frame(resample_m1(M1, "H1"))

MIXED = """
x = tf("H1", ema(close, 10))
plot(x)
plot(sma(close, 5) - tf("H1", rsi(close, 14)))
signal("long", crossover(close, x))
"""


def test_tf_equals_the_htf_builtin_mapped():
    r = evaluate(parse('plot(tf("H1", ema(close, 10)))'), F5, {}, M5, htf={"H1": F60})
    want = map_closed(B.ema(None, F60["close"], 10), H1, F5.index, M5)
    assert same(r.plots[0], want) and r.plots[0].notna().sum() > 400


def test_tf_composes_with_chart_series():
    r = evaluate(parse(MIXED), F5, {}, M5, htf={"H1": F60})
    rsi = map_closed(B.rsi(None, F60["close"], 14), H1, F5.index, M5)
    assert same(r.plots[1], B.sma(None, F5["close"], 5) - rsi)


def test_tf_is_prefix_invariant():
    """The no-lookahead guard for MTF: cut the chart at k and the HTF at what had
    closed by then; every value up to k must equal the full run's."""
    full = evaluate(parse(MIXED), F5, {}, M5, htf={"H1": F60})
    for k in np.random.default_rng(3).integers(1, len(F5), 12):
        close = int(F5.index[k - 1]) + M5
        cut = F60[F60.index + H1 <= close]
        part = evaluate(parse(MIXED), F5.iloc[:k], {}, M5, htf={"H1": cut})
        for a, b in zip(part.plots + part.signals, full.plots + full.signals, strict=True):
            assert same(a, b.iloc[:k])


def test_forming_chart_bar_reaches_the_engine():
    last = int(F5.index[11])                   # 00:55, closes with H1 bar 0
    r = evaluate(parse('plot(tf("H1", close))'), F5.iloc[:12], {}, M5,
                 htf={"H1": F60.iloc[:1]}, forming_msc=last)
    assert math.isnan(r.plots[0].iloc[11])


def test_tf_lower_than_chart_is_a_script_error():
    with pytest.raises(ScriptError, match="higher timeframes"):
        evaluate(parse('plot(tf("M5", close))'), F60, {}, H1, htf={"M5": F5})


def test_tf_without_htf_bars_is_unknown():
    r = evaluate(parse('plot(tf("H1", close))'), F5, {}, M5)
    assert r.plots[0].isna().all()
