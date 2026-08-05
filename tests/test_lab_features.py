"""Feature matrix. The first test is the one that matters: a feature value at
bar t must not move when bars after t change. Everything else in the lab is
worthless if that fails."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from journal.adapter.base import Candle
from journal.lab.features import (
    PRICE_FEATURES,
    bars_to_frame,
    build_features,
    usable_columns,
)

MINUTE = 60_000


def _bars(closes: list[float], *, spread: int | None = 20,
          volume: int | None = 100) -> list[Candle]:
    """Synthetic bars: open == previous close, a symmetric 0.5 wick each side."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(
            time_msc=i * MINUTE,
            open=prev,
            high=max(prev, c) + 0.5,
            low=min(prev, c) - 0.5,
            close=c,
            tick_volume=volume,
            spread=spread,
            real_volume=0,
        ))
        prev = c
    return out


def test_no_lookahead_features_at_t_ignore_future_bars():
    closes = [100.0 + i for i in range(80)]
    base = build_features(bars_to_frame(_bars(closes)))

    tampered = list(closes)
    for i in range(60, 80):
        tampered[i] = 5_000.0          # violently different future
    after = build_features(bars_to_frame(_bars(tampered)))

    left = base.loc[: 59 * MINUTE, list(PRICE_FEATURES)]
    right = after.loc[: 59 * MINUTE, list(PRICE_FEATURES)]
    pd.testing.assert_frame_equal(left, right)


def test_bars_to_frame_sorts_and_dedupes():
    bars = _bars([100.0, 101.0, 102.0])
    shuffled = [bars[2], bars[0], bars[1], bars[1]]
    df = bars_to_frame(shuffled)
    assert list(df.index) == [0, MINUTE, 2 * MINUTE]


def test_hour_and_dow_come_from_time_msc_utc():
    # 2026-01-01T00:00:00Z is a Thursday -> dow == 3 (Monday = 0)
    epoch = 1_767_225_600_000
    bars = [Candle(time_msc=epoch + i * 3_600_000, open=1.0, high=1.5, low=0.5,
                   close=1.0, tick_volume=10, spread=5, real_volume=0)
            for i in range(3)]
    df = build_features(bars_to_frame(bars))
    assert list(df["hour_utc"]) == [0, 1, 2]
    assert set(df["dow"]) == {3}


def test_body_and_wick_ratios_are_null_on_a_flat_bar():
    flat = Candle(time_msc=0, open=100.0, high=100.0, low=100.0, close=100.0,
                  tick_volume=10, spread=5, real_volume=0)
    df = build_features(bars_to_frame([flat]))
    assert np.isnan(df.loc[0, "body_ratio"])
    assert np.isnan(df.loc[0, "upper_wick"])
    assert np.isnan(df.loc[0, "lower_wick"])


def test_atr_rel_is_positive_and_uses_previous_close():
    df = build_features(bars_to_frame(_bars([100.0 + i for i in range(40)])))
    tail = df["atr_rel"].dropna()
    assert len(tail) > 0
    assert (tail > 0).all()


def test_usable_columns_drops_a_mostly_unknown_source_column():
    bars = _bars([100.0 + i for i in range(40)], spread=None)
    df = build_features(bars_to_frame(bars))
    kept, dropped = usable_columns(df, ["ret_1", "spread"])
    assert kept == ["ret_1"]
    assert dropped["spread"] == pytest.approx(1.0)


def test_usable_columns_keeps_a_column_with_few_unknowns():
    bars = _bars([100.0 + i for i in range(40)])
    df = build_features(bars_to_frame(bars))
    df.loc[0, "spread"] = np.nan          # 1 of 40 == 2.5%, under the 5% bar
    kept, dropped = usable_columns(df, ["ret_1", "spread"])
    assert kept == ["ret_1", "spread"]
    assert dropped == {}
