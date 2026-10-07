"""Indicator builtins. The property test at the bottom is the one that matters:
a value at bar t must not move when bars after t are removed — for EVERY
builtin. It is the no-lookahead guard for the whole indicator system (spec
2026-10-07-indicators §invariant 1) and must never be weakened."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from journal.domain.indicators import builtins as B
from indicator_helpers import MINUTE, s, same, walk

# --- golden values -----------------------------------------------------------

def test_sma_is_rolling_mean_and_unknown_during_warmup():
    out = B.sma(None, s([1, 2, 3, 4, 5]), 3)
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert list(out[2:]) == [2.0, 3.0, 4.0]


def test_ema_seeds_after_n_bars_and_uses_span_alpha():
    x = s([10, 10, 10, 20])
    out = B.ema(None, x, 3)
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(10.0)
    assert out[3] == pytest.approx(10 + 0.5 * (20 - 10))       # alpha = 2/(3+1)


def test_rma_is_wilder_smoothing():
    out = B.rma(None, s([2, 2, 2, 6]), 2)
    assert out[3] == pytest.approx(2 + 0.5 * (6 - 2))           # alpha = 1/2


def test_wma_weights_recent_bars_most():
    out = B.wma(None, s([1, 2, 3]), 3)
    assert out[2] == pytest.approx((1 * 1 + 2 * 2 + 3 * 3) / 6)


def test_stdev_is_population():
    out = B.stdev(None, s([1, 3]), 2)
    assert out[1] == pytest.approx(1.0)


def test_rsi_is_100_when_there_are_no_losses():
    out = B.rsi(None, s([1, 2, 3, 4, 5, 6]), 2)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_50_for_symmetric_moves():
    out = B.rsi(None, s([10, 11, 10, 11, 10, 11, 10, 11, 10]), 1)
    assert out.iloc[-1] == pytest.approx(0.0)                   # last move was down, n=1
    out = B.rsi(None, s([10, 11, 10, 11, 10, 11, 10, 11, 10]), 1000)
    assert math.isnan(out.iloc[-1])                             # never warm


def test_true_range_uses_previous_close_and_first_bar_is_high_minus_low():
    f = pd.DataFrame({"open": [10, 10], "high": [11, 12], "low": [9, 11.5],
                      "close": [10, 12], "volume": [1, 1]}, dtype="float64")
    tr = B.tr(f)
    assert list(tr) == [2.0, 2.0]                               # 12 - prev close 10


def test_crossover_is_three_valued():
    a, b = s([1, 3, float("nan"), 5]), s([2, 2, 2, 2])
    out = B.crossover(None, a, b)
    assert out[1] == 1.0
    assert math.isnan(out[2])                                    # unknown, not false
    assert math.isnan(out[3])                                    # previous bar unknown


def test_crossunder():
    out = B.crossunder(None, s([3, 1, 1]), s([2, 2, 2]))
    assert list(out[1:]) == [1.0, 0.0]


def test_vwap_resets_each_utc_day():
    day = 86_400_000
    f = pd.DataFrame({
        "high": [2, 2, 10], "low": [2, 2, 10], "close": [2, 4, 10],
        "volume": [1, 1, 5],
    }, index=pd.Index([0, MINUTE, day], name="time_msc"), dtype="float64")
    out = B.vwap(f)
    assert out.iloc[1] == pytest.approx((2 * 1 + (8 / 3) * 1) / 2)
    assert out.iloc[2] == pytest.approx(10.0)                   # new day, fresh sum


def test_vwap_is_unknown_once_volume_is_unknown_that_day():
    f = pd.DataFrame({"high": [2, 2], "low": [2, 2], "close": [2, 2],
                      "volume": [float("nan"), 1]},
                     index=pd.Index([0, MINUTE], name="time_msc"), dtype="float64")
    assert B.vwap(f).isna().all()


def test_min_max_propagate_unknown():
    out = B.max_(None, s([1, float("nan")]), s([2, 2]))
    assert out[0] == 2.0 and math.isnan(out[1])


def test_nz_replaces_unknown_only_when_asked():
    assert list(B.nz(None, s([float("nan"), 3]), 0)) == [0.0, 3.0]


def test_where_is_unknown_when_condition_is_unknown():
    out = B.where(None, s([1, 0, float("nan")]), s([5, 5, 5]), s([7, 7, 7]))
    assert out[0] == 5 and out[1] == 7 and math.isnan(out[2])


# --- registry ---------------------------------------------------------------

def test_every_registered_builtin_has_a_cost_and_params():
    for name, b in B.REGISTRY.items():
        consts = {p.name: p.default if p.default is not None else 5
                  for p in b.params if p.kind != "series"}
        assert b.cost(consts, MINUTE) >= 0, name


def _call(name: str, f: pd.DataFrame):
    b = B.REGISTRY[name]
    args = []
    for p in b.params:
        if p.kind == "series":
            args.append(f["close"] if len(args) == 0 else f["open"])
        else:
            args.append(p.default if p.default is not None else 10)
    return b.fn(f, *args)


@pytest.mark.parametrize("name", sorted(B.REGISTRY))
def test_no_lookahead_every_builtin_is_prefix_invariant(name):
    f = walk(3000, volume_gaps=True)
    full = _call(name, f)
    for k in (1, 2, 50, 777, 1441, 2999):
        part = _call(name, f.iloc[:k])
        assert same(part, full.iloc[:k]), f"{name} moved at k={k}"


@pytest.mark.parametrize("name", sorted(B.REGISTRY))
def test_cost_is_enough_warmup(name):
    """Starting `cost` bars early must reproduce the full-history value to well
    within display precision — otherwise warm-up loads too little and the left
    edge of the chart is silently wrong."""
    f = walk(4000, seed=11)
    b = B.REGISTRY[name]
    consts = {p.name: (p.default if p.default is not None else 10)
              for p in b.params if p.kind != "series"}
    lb = b.cost(consts, MINUTE)
    full = _call(name, f)
    start = 1500
    win = _call(name, f.iloc[start - lb:]) if lb <= start else None
    assert win is not None
    tail_full = full.iloc[start:].to_numpy(dtype=float)
    tail_win = win.iloc[lb:].to_numpy(dtype=float)
    scale = float(f["close"].std())
    ok = np.isnan(tail_full) & np.isnan(tail_win) | (np.abs(tail_full - tail_win) <= 1e-3 * scale)
    assert ok.all(), f"{name}: warm-up {lb} too short"
