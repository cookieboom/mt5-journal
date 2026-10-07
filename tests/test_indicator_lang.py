"""Script language: a whitelisted subset of Python syntax. Anything outside the
whitelist is rejected with a line/column — never executed. The language half
of the no-lookahead guard lives here too: `x[n]` only with a literal n >= 0."""
from __future__ import annotations

import pytest

from journal.domain.indicators.lang import (
    ScriptError, const_value, lookback, parse, resolve_consts,
)

MINUTE = 60_000

EMA_CROSS = """
fast_len = input(20, min=1, max=500, title="Fast")
slow_len = input(50, min=1, max=500, title="Slow")
fast = ema(close, fast_len)
slow = ema(close, slow_len)
plot(fast, title="EMA fast", color="violet")
plot(slow, title="EMA slow", color="cyan")
signal("long", crossover(fast, slow))
signal("short", crossunder(fast, slow))
"""


def test_parses_the_spec_example():
    p = parse(EMA_CROSS)
    assert [i.name for i in p.inputs] == ["fast_len", "slow_len"]
    assert p.inputs[0].kind == "int" and p.inputs[0].default == 20
    assert p.inputs[0].min == 1 and p.inputs[0].max == 500 and p.inputs[0].title == "Fast"
    assert [pl.title for pl in p.plots] == ["EMA fast", "EMA slow"]
    assert [pl.color for pl in p.plots] == ["violet", "cyan"]
    assert all(pl.pane == "price" for pl in p.plots)
    assert [s.side for s in p.signals] == ["long", "short"]


def test_lookback_follows_inputs_and_composes():
    p = parse(EMA_CROSS)
    assert lookback(p, resolve_consts(p, {}), MINUTE) == 4 * 50 + 1     # crossover adds 1
    assert lookback(p, resolve_consts(p, {"slow_len": 100}), MINUTE) == 401


def test_lookback_sums_along_nesting_and_counts_history_index():
    p = parse("x = sma(sma(close, 10), 5)\nplot(x[3])")
    assert lookback(p, resolve_consts(p, {}), MINUTE) == 18


def test_input_values_are_clamped_and_typed():
    p = parse("n = input(14, min=2, max=50)\nplot(sma(close, n))")
    assert resolve_consts(p, {"n": 999})["n"] == 50
    assert resolve_consts(p, {"n": 7.6})["n"] == 8
    assert resolve_consts(p, {"n": "junk"})["n"] == 14
    assert resolve_consts(p, {})["n"] == 14


def test_constant_assignments_can_feed_windows():
    p = parse("n = 7\nm = n * 2\nplot(sma(close, m))")
    assert lookback(p, resolve_consts(p, {}), MINUTE) == 14


def test_hline_and_named_pane():
    p = parse('plot(rsi(close, 14), pane="rsi")\nhline(70, pane="rsi", title="OB")')
    assert p.plots[0].pane == "rsi"
    assert const_value(p.hlines[0].value, {}) == 70 and p.hlines[0].pane == "rsi"


def test_default_titles_and_colors_are_assigned():
    p = parse("plot(close)\nplot(open)")
    assert [pl.title for pl in p.plots] == ["plot 1", "plot 2"]
    assert p.plots[0].color != p.plots[1].color


def test_bool_input():
    p = parse("on = input(True)\nplot(where(on, close, open))")
    assert p.inputs[0].kind == "bool"


@pytest.mark.parametrize("src, needle", [
    ("import os", "not allowed"),
    ("x = close.shift(-1)\nplot(x)", "not allowed"),
    ("plot(close[-1])", "literal"),
    ("n = 1\nplot(close[n])", "literal"),
    ("plot(__import__('os'))", "unknown function"),
    ("plot(eval('1'))", "unknown function"),
    ("for i in [1]: pass", "not allowed"),
    ("plot(foo)", "undefined"),
    ("close = 1", "reserved"),
    ("ema = 1", "reserved"),
    ("plot(ema(close))", "argument"),
    ("plot(ema(close, 1, 2))", "argument"),
    ("plot(ema(close, close))", "constant"),
    ("plot(ema(close, n=3))", "keyword"),
    ("plot(close, color='#ff0000')", "color"),
    ("plot(close, style='candles')", "style"),
    ('signal("up", close > open)', "side"),
    ("x = input(close)", "input"),
    ("plot(sma(close, 0))", "window"),
    ("plot(sma(close, 6000))", "window"),
    ("plot(close) if 1 else 0", "not allowed"),
    ("plot(lambda: 1)", "not allowed"),
    ("x = [1, 2]", "not allowed"),
    ("plot('text')", "string"),
    ("a, b = 1, 2", "not allowed"),
    ("plot(close)\nplot(close", "syntax"),
])
def test_rejected(src, needle):
    with pytest.raises(ScriptError) as e:
        p = parse(src)
        lookback(p, resolve_consts(p, {}), MINUTE)
    assert needle in str(e.value).lower()


def test_error_carries_line_and_column():
    with pytest.raises(ScriptError) as e:
        parse("x = sma(close, 5)\nplot(nope)")
    assert e.value.line == 2 and e.value.col == 6      # both 1-based


def test_limits():
    with pytest.raises(ScriptError, match="too long"):
        parse("x = 1\n" * 5000)
    with pytest.raises(ScriptError, match="statements"):
        parse("\n".join(f"x{i} = {i}" for i in range(201)))
    with pytest.raises(ScriptError, match="deep"):
        parse("plot(" + "abs(" * 60 + "close" + ")" * 61)


def test_a_script_must_output_something():
    with pytest.raises(ScriptError, match="plot"):
        parse("x = sma(close, 5)")
