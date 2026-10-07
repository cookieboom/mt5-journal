"""Every shipped script parses, evaluates, and is prefix-invariant."""
from __future__ import annotations

import pytest

from indicator_helpers import MINUTE, same, walk_candles
from journal.domain.indicators.engine import evaluate
from journal.domain.indicators.frame import to_frame
from journal.domain.indicators.lang import lookback, parse, resolve_consts
from journal.domain.indicators.library import library
from journal.domain.resample import resample_m1, timeframe_ms

BARS = walk_candles(4000, volume_gaps=True)     # 66 whole hours: an H1 EMA 50 draws


def test_library_is_not_empty_and_named():
    lib = library()
    assert "lib:ema" in lib and lib["lib:ema"].name == "EMA"
    assert lib["lib:ema_cross"].name == "EMA cross"


@pytest.mark.parametrize("sid", sorted(library()))
def test_no_lookahead_library_script(sid):
    p = parse(library()[sid].source)
    assert lookback(p, resolve_consts(p, {}), MINUTE) >= 0
    f = to_frame(BARS)
    htf = {x: to_frame(resample_m1(BARS, x)) for x in p.htfs}
    full = evaluate(p, f, {}, MINUTE, htf=htf)
    assert any(s.notna().any() for s in full.plots)         # draws something
    for k in (5, 700, 1500, 3700):
        close = int(f.index[k - 1]) + MINUTE                # HTF bars closed by then
        cut = {x: h[h.index + timeframe_ms(x) <= close] for x, h in htf.items()}
        part = evaluate(p, f.iloc[:k], {}, MINUTE, htf=cut)
        for a, b in zip(part.plots + part.signals, full.plots + full.signals, strict=True):
            assert same(a, b.iloc[:k])
