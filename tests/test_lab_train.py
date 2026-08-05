"""Training. These tests assert the SHAPE of a run — that both kinds are fit,
that timing splits per regime, that a thin regime falls back to pooled — not
that any model is accurate. Accuracy on synthetic data would prove nothing."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from journal.adapter.base import Candle
from journal.lab.features import bars_to_frame, build_features
from journal.lab.labels import LabelConfig
from journal.lab.train import TrainConfig, build_dataset, train_all

MINUTE = 60_000
FEATURES = ("ret_1", "ret_5", "atr_rel", "hour_utc")


def _walk(n: int, seed: int = 0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    price = 2000.0
    bars = []
    for i in range(n):
        step = float(rng.normal(0, 1.5))
        open_ = price
        price = price + step
        bars.append(Candle(
            time_msc=i * MINUTE,
            open=open_,
            high=max(open_, price) + 0.4,
            low=min(open_, price) - 0.4,
            close=price,
            tick_volume=100 + i % 7,
            spread=20,
            real_volume=0,
        ))
    return bars


def _cfg(**kw) -> TrainConfig:
    base = dict(label=LabelConfig(n_bars=8), features=FEATURES, n_folds=3,
                seed=7, threshold=0.5, point=0.001, default_spread_points=0.0)
    base.update(kw)
    return TrainConfig(**base)


def _frame(n: int = 1200) -> pd.DataFrame:
    return build_features(bars_to_frame(_walk(n)))


def test_dataset_has_two_rows_per_labelled_bar_one_per_side():
    df = _frame(400)
    data = build_dataset(df, _cfg())
    per_bar = data.groupby("time_msc").size()
    assert set(per_bar) == {2}
    assert set(data["side"]) == {"long", "short"}


def test_dataset_drops_unlabelled_and_incomplete_rows():
    df = _frame(400)
    data = build_dataset(df, _cfg())
    assert data["y"].notna().all()
    assert data["regime"].notna().all()
    assert data[list(FEATURES)].notna().all().all()
    # the trailing n_bars can never be labelled
    assert data["time_msc"].max() < df.index.max()


def test_train_all_fits_both_kinds_for_every_stage():
    models = train_all(_frame(), _cfg())
    regime_kinds = {m.kind for m in models if m.stage == "regime"}
    timing_kinds = {m.kind for m in models if m.stage == "timing"}
    assert regime_kinds == {"logreg", "lgbm"}
    assert timing_kinds == {"logreg", "lgbm"}


def test_timing_models_are_split_per_regime_when_data_allows():
    models = train_all(_frame(), _cfg(pooled_min_rows=1))
    regimes = {m.regime for m in models if m.stage == "timing"}
    assert regimes <= {"trend_up", "trend_down", "range"}
    assert None not in regimes


def test_a_thin_regime_falls_back_to_a_pooled_model():
    models = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    timing = [m for m in models if m.stage == "timing"]
    assert timing
    assert all(m.pooled and m.regime is None for m in timing)


def test_metrics_carry_n_and_a_baseline():
    models = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    for m in models:
        assert m.metrics["n"] > 0
        assert "folds" in m.metrics
        if m.stage == "timing":
            assert "baseline_expectancy_r" in m.metrics


def test_training_is_deterministic_for_a_fixed_seed():
    a = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    b = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    lhs = {(m.stage, m.kind, m.regime): m.metrics["n"] for m in a}
    rhs = {(m.stage, m.kind, m.regime): m.metrics["n"] for m in b}
    assert lhs == rhs


def test_too_little_data_raises_rather_than_returning_a_fake_model():
    with pytest.raises(ValueError, match="not enough"):
        train_all(_frame(60), _cfg())
