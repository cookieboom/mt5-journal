"""Engine: a checked program evaluated over a bar frame."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from journal.domain.indicators import builtins as B
from journal.domain.indicators.engine import evaluate
from journal.domain.indicators.frame import to_frame
from journal.domain.indicators.lang import ScriptError, parse
from indicator_helpers import MINUTE, every_series, same, walk

EMA_CROSS = """
fast_len = input(5, min=1, max=500)
slow_len = input(12, min=1, max=500)
fast = ema(close, fast_len)
slow = ema(close, slow_len)
plot(fast)
plot(slow)
signal("long", crossover(fast, slow))
"""


def frame(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    base = {c: [1.0] * n for c in ("open", "high", "low", "close", "volume", "spread")}
    base.update({k: [float(x) for x in v] for k, v in cols.items()})
    return pd.DataFrame(base, index=pd.Index([i * MINUTE for i in range(n)], name="time_msc"))


def test_plots_match_the_builtins_they_call():
    f = walk(300)
    r = evaluate(parse(EMA_CROSS), f, {}, MINUTE)
    assert same(r.plots[0], B.ema(f, f["close"], 5))
    assert same(r.plots[1], B.ema(f, f["close"], 12))
    assert same(r.signals[0], B.crossover(f, r.plots[0], r.plots[1]))


def test_inputs_override_defaults():
    f = walk(300)
    r = evaluate(parse(EMA_CROSS), f, {"fast_len": 9}, MINUTE)
    assert same(r.plots[0], B.ema(f, f["close"], 9))


def test_history_index_is_bars_ago():
    r = evaluate(parse("plot(close[2])"), frame(close=[1, 2, 3, 4]), {}, MINUTE)
    assert math.isnan(r.plots[0].iloc[1]) and list(r.plots[0].iloc[2:]) == [1.0, 2.0]


def test_derived_series_and_arithmetic():
    f = frame(high=[4, 6], low=[2, 2], close=[3, 3])
    r = evaluate(parse("plot(hl2 * 2 - 1)"), f, {}, MINUTE)
    assert list(r.plots[0]) == [5.0, 7.0]


def test_division_by_zero_is_unknown_not_infinite():
    r = evaluate(parse("plot(close / (close - close))"), frame(close=[1, 2]), {}, MINUTE)
    assert r.plots[0].isna().all()


def test_comparison_with_unknown_is_unknown():
    f = frame(volume=[float("nan"), 50, 5])
    r = evaluate(parse('signal("long", volume > 10)'), f, {}, MINUTE)
    assert math.isnan(r.signals[0].iloc[0])
    assert list(r.signals[0].iloc[1:]) == [1.0, 0.0]


def test_not_of_unknown_stays_unknown():
    f = frame(volume=[float("nan"), 50])
    r = evaluate(parse("plot(not (volume > 10))"), f, {}, MINUTE)
    assert math.isnan(r.plots[0].iloc[0]) and r.plots[0].iloc[1] == 0.0


def test_and_or_are_kleene():
    f = frame(volume=[float("nan"), float("nan")], close=[1, 1])
    r = evaluate(parse("plot(volume > 0 and close > 5)\nplot(volume > 0 or close > 0)"),
                 f, {}, MINUTE)
    assert list(r.plots[0]) == [0.0, 0.0]     # false AND unknown = false
    assert list(r.plots[1]) == [1.0, 1.0]     # true OR unknown = true


def test_chained_comparison():
    r = evaluate(parse("plot(1 < close < 3)"), frame(close=[0, 2, 4]), {}, MINUTE)
    assert list(r.plots[0]) == [0.0, 1.0, 0.0]


def test_constants_broadcast_to_series():
    r = evaluate(parse("k = 2\nplot(k)\nplot(max(close, 3))"), frame(close=[1, 5]), {}, MINUTE)
    assert list(r.plots[0]) == [2.0, 2.0]
    assert list(r.plots[1]) == [3.0, 5.0]


def test_bool_input_drives_where():
    p = parse("on = input(True)\nplot(where(on, close, open))")
    f = frame(close=[9, 9], open=[1, 1])
    assert list(evaluate(p, f, {}, MINUTE).plots[0]) == [9.0, 9.0]
    assert list(evaluate(p, f, {"on": False}, MINUTE).plots[0]) == [1.0, 1.0]


def test_hlines_resolve_to_numbers():
    r = evaluate(parse("lvl = input(70)\nplot(close)\nhline(lvl)\nhline(100 - lvl)"),
                 frame(close=[1]), {"lvl": 80}, MINUTE)
    assert r.hlines == [80.0, 20.0]


def test_window_out_of_range_after_inputs_is_a_script_error_with_line():
    p = parse("n = input(5)\nplot(sma(close, n - 10))")
    with pytest.raises(ScriptError) as e:
        evaluate(p, frame(close=[1]), {}, MINUTE)
    assert e.value.line == 2


def test_empty_frame_gives_empty_series():
    r = evaluate(parse(EMA_CROSS), to_frame([]), {}, MINUTE)
    assert len(r.plots[0]) == 0 and len(r.signals[0]) == 0


def test_whole_script_is_prefix_invariant():
    f = walk(1500, volume_gaps=True)
    src = EMA_CROSS + ("\nplot(vwap() - sma(hlc3, 20)[3])"
                       "\nsignal('short', rsi(close, 14) > 70 and adx(14) > 20,"
                       " stop=high[1] + atr(14), target=bb_lower(close, 20, 2))"
                       "\nexit('long', crossunder(close, sma(close, 30)))")
    full = evaluate(parse(src), f, {}, MINUTE)
    assert len(every_series(full)) == 8
    for k in (3, 400, 1441):
        part = evaluate(parse(src), f.iloc[:k], {}, MINUTE)
        for a, b in zip(every_series(part), every_series(full), strict=True):
            assert same(a, b.iloc[:k])


def test_time_budget(monkeypatch):
    from journal.domain.indicators import engine
    monkeypatch.setattr(engine, "BUDGET_S", -1.0)
    with pytest.raises(ScriptError, match="budget"):
        evaluate(parse(EMA_CROSS), walk(50), {}, MINUTE)


def test_infinite_power_is_unknown():
    r = evaluate(parse("plot(close ** 1000)"), frame(close=[10]), {}, MINUTE)
    assert np.isnan(r.plots[0].iloc[0])


def test_stops_targets_and_exits_are_evaluated():
    src = ('signal("long", close > open, stop=low - 1, target_r=2)\n'
           'signal("short", close < open, target=high + 1)\n'
           'exit("long", close < 3)')
    r = evaluate(parse(src), frame(close=[2, 5, 1], open=[1, 1, 1], low=[0, 4, 1],
                                   high=[3, 6, 2]), {}, MINUTE)
    assert list(r.stops[0]) == [-1, 3, 0] and r.stops[1] is None
    assert r.targets[0] is None and list(r.targets[1]) == [4, 7, 3]
    assert list(r.exits[0]) == [1.0, 0.0, 1.0]


def test_exit_is_three_valued():
    r = evaluate(parse('signal("long", close > 0)\nexit("long", close > sma(close, 2))'),
                 frame(close=[1, 2, 1]), {}, MINUTE)
    assert math.isnan(r.exits[0].iloc[0]) and list(r.exits[0].iloc[1:]) == [1.0, 0.0]
