"""Labels. Two rules are load-bearing and both are tested here: entry is
`open[t+1]` (close[t] is not tradeable), and when one bar touches both barriers
the stop wins — the same pessimism the replay engine already applies."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from journal.lab.labels import LabelConfig, barrier_labels, regime_labels

MINUTE = 60_000


def _frame(rows: list[tuple[float, float, float, float]], atr: float = 1.0) -> pd.DataFrame:
    """rows are (open, high, low, close); a constant ATR keeps R arithmetic
    readable — R = k_atr * atr."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.index = pd.Index([i * MINUTE for i in range(len(rows))], name="time_msc")
    df["atr"] = atr
    return df


def test_entry_is_the_next_bar_open_not_this_bar_close():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (110.0, 110.0, 110.0, 110.0),
        (110.0, 110.0, 110.0, 110.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "entry"] == pytest.approx(110.0)


def test_stop_wins_when_one_bar_touches_both_barriers():
    # entry 100, R = 1 -> SL 99, TP 102. Bar 1 spans 98..103: both are touched.
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 103.0, 98.0, 100.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "sl_first"
    assert out.loc[0, "r_gross"] == pytest.approx(-1.0)


def test_target_first_pays_the_reward_ratio():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 102.5, 99.5, 102.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "tp_first"
    assert out.loc[0, "r_gross"] == pytest.approx(2.0)


def test_timeout_scores_the_realised_move_in_r():
    # entry 100, R = 1, nothing touches 99 or 102 within 2 bars, ends at 100.5
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.2),
        (100.2, 101.0, 99.5, 100.5),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "timeout"
    assert out.loc[0, "r_gross"] == pytest.approx(0.5)


def test_short_side_mirrors_the_long_side():
    # entry 100, R = 1 -> SL 101, TP 98. Price falls to 97.5: target first.
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 97.5, 98.0),
        (98.0, 98.0, 98.0, 98.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "short", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "tp_first"
    assert out.loc[0, "r_gross"] == pytest.approx(2.0)


def test_spread_is_deducted_from_the_net_result():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 102.5, 99.5, 102.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    df["spread"] = 50            # 50 points * 0.01 = 0.5 price = 0.5 R
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "r_gross"] == pytest.approx(2.0)
    assert out.loc[0, "r_net"] == pytest.approx(1.5)


def test_unknown_spread_falls_back_to_the_supplied_default():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 102.5, 99.5, 102.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    df["spread"] = np.nan
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "long", point=0.01,
                         default_spread_points=30)
    assert out.loc[0, "r_net"] == pytest.approx(2.0 - 0.3)


def test_last_n_bars_have_no_label():
    df = _frame([(100.0, 100.5, 99.5, 100.0)] * 6)
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out["outcome"].iloc[-2:].isna().all()
    assert out["outcome"].iloc[:-2].notna().all()


def test_regime_labels_call_a_straight_line_a_trend():
    df = _frame([(100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i) for i in range(30)])
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[0] == "trend_up"

    falling = _frame([(100.0 - i, 100.0 - i, 100.0 - i, 100.0 - i) for i in range(30)])
    assert regime_labels(falling, LabelConfig(n_bars=10)).iloc[0] == "trend_down"


def test_regime_labels_call_a_sawtooth_a_range():
    closes = [100.0 + (1.0 if i % 2 else -1.0) for i in range(30)]
    df = _frame([(c, c, c, c) for c in closes])
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[0] == "range"


def test_regime_labels_call_a_flat_window_a_range():
    df = _frame([(100.0, 100.0, 100.0, 100.0)] * 30)
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[0] == "range"


def test_regime_labels_leave_the_trailing_window_unlabelled():
    df = _frame([(100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i) for i in range(15)])
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[-10:].isna().all()
    assert out.iloc[:-10].notna().all()
