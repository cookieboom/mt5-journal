"""Every shipped script parses, evaluates, and is prefix-invariant."""
from __future__ import annotations

import pytest

from indicator_helpers import MINUTE, same, walk
from journal.domain.indicators.engine import evaluate
from journal.domain.indicators.lang import lookback, parse, resolve_consts
from journal.domain.indicators.library import library


def test_library_is_not_empty_and_named():
    lib = library()
    assert "lib:ema" in lib and lib["lib:ema"].name == "EMA"
    assert lib["lib:ema_cross"].name == "EMA cross"


@pytest.mark.parametrize("sid", sorted(library()))
def test_no_lookahead_library_script(sid):
    p = parse(library()[sid].source)
    assert lookback(p, resolve_consts(p, {}), MINUTE) >= 0
    f = walk(2000, volume_gaps=True)
    full = evaluate(p, f, {}, MINUTE)
    assert any(s.notna().any() for s in full.plots)         # draws something
    for k in (5, 700, 1500):
        part = evaluate(p, f.iloc[:k], {}, MINUTE)
        for a, b in zip(part.plots + part.signals, full.plots + full.signals, strict=True):
            assert same(a, b.iloc[:k])
