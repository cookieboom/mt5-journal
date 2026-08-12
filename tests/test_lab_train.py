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
from journal.lab.train import (
    SIDE_CODE,
    SIDE_CODE_COLUMN,
    SINGLE_THREAD_ROWS,
    TrainConfig,
    _new_estimator,
    build_dataset,
    train_all,
)

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
    # `n` is both seed- and data-independent, so comparing it asserts nothing.
    # `auc`/`expectancy_r` are the numbers a different fit would move — they
    # are what docs/lab-models.md § Reproducing actually promises.
    def fingerprint(models):
        return {(m.stage, m.kind, m.regime):
                (m.metrics["n"], m.metrics["auc"], m.metrics["expectancy_r"])
                for m in models}

    assert fingerprint(a) == fingerprint(b)
    assert any(v[1] is not None or v[2] is not None for v in fingerprint(a).values()), \
        "a fingerprint of all-None would pass regardless of the seed"


def test_too_little_data_raises_rather_than_returning_a_fake_model():
    with pytest.raises(ValueError, match="not enough"):
        train_all(_frame(60), _cfg())


def test_small_fits_are_single_threaded_and_big_ones_are_not():
    """LightGBM's thread pool is a net loss under ~100k rows — see the table on
    SINGLE_THREAD_ROWS. It is a speed choice, not a correctness one, so it gets
    pinned here rather than left to whoever next reads the fit call. A dataset
    this small must not spawn threads; one over the line must."""
    models = train_all(_frame(400), _cfg())
    lgbm = [m for m in models if m.kind == "lgbm"]
    assert lgbm, "no boosted model was fit at all"
    assert all(m.estimator.get_params()["n_jobs"] == 1 for m in lgbm)
    assert _new_estimator("lgbm", 7, SINGLE_THREAD_ROWS - 1).get_params()["n_jobs"] == 1
    assert _new_estimator("lgbm", 7, SINGLE_THREAD_ROWS).get_params()["n_jobs"] == -1


def test_side_code_contract_is_pinned_for_score_py_to_reconstruct():
    """A scorer rebuilds the feature vector from TrainedModel.features by
    appending SIDE_CODE[side] under the SIDE_CODE_COLUMN name. If this
    encoding ever flips, every timing prediction inverts silently unless a
    test pins the exact values and column name — this is that test."""
    assert SIDE_CODE == {"long": 1, "short": 0}
    assert SIDE_CODE_COLUMN == "side_code"
    models = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    for m in models:
        assert m.features[-1] == SIDE_CODE_COLUMN
        assert m.features[:-1] == FEATURES
