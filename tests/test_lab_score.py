"""Scoring. The states that matter are the degraded ones — no model, missing
artifact, not enough bars — because /live renders them next to order buttons
and must never show a stale or invented number."""
from __future__ import annotations

import json

import numpy as np
import pytest

from journal.adapter.base import Candle
from journal.lab.features import bars_to_frame, build_features
from journal.lab.labels import REGIMES, LabelConfig
from journal.lab.score import score_bars
from journal.lab.store import save_models
from journal.lab.train import SIDE_CODE_COLUMN, TrainConfig, TrainedModel, train_all
from journal.store.db import connect

MINUTE = 60_000
FEATURES = ("ret_1", "ret_5", "atr_rel", "hour_utc")


# Module-level (joblib pickles these by reference) constant-output estimators.
# Real fits are slow and their predictions are not controllable; the questions
# below — WHICH stored model scored a bar, and whose expectancy the report
# quotes — need the answer to be readable off the number itself.
class _RegimeStub:
    def __init__(self, regime: str) -> None:
        self.regime = regime
        self.classes_ = list(REGIMES)

    def predict(self, x):
        return np.array([self.regime] * len(x))

    def predict_proba(self, x):
        out = np.zeros((len(x), len(self.classes_)))
        out[:, self.classes_.index(self.regime)] = 1.0
        return out


class _TimingStub:
    def __init__(self, p: float) -> None:
        self.p = p

    def predict_proba(self, x):
        col = np.full(len(x), self.p)
        return np.column_stack([1.0 - col, col])


def _stub_model(stage, regime, estimator, *, expectancy=0.5, baseline=0.01,
                n=400, n_taken=300, pooled=False) -> TrainedModel:
    return TrainedModel(
        stage=stage, regime=regime, kind="lgbm", estimator=estimator,
        metrics={"n": n, "n_taken": n_taken, "expectancy_r": expectancy,
                 "baseline_expectancy_r": baseline, "folds": []},
        n_rows=n, features=(*FEATURES, SIDE_CODE_COLUMN), pooled=pooled,
    )


def _save_stubs(conn, cache, models):
    save_models(conn, symbol="XAUUSDc", timeframe="M1",
                config={"n_bars": 8, "seed": 7}, models=models,
                train_from_ms=0, train_to_ms=1200 * MINUTE, cache_dir=cache)


def _walk(n: int, seed: int = 0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    price, bars = 2000.0, []
    for i in range(n):
        open_ = price
        price += float(rng.normal(0, 1.5))
        bars.append(Candle(time_msc=i * MINUTE, open=open_,
                           high=max(open_, price) + 0.4,
                           low=min(open_, price) - 0.4, close=price,
                           tick_volume=100, spread=20, real_volume=0))
    return bars


@pytest.fixture()
def trained(tmp_path):
    conn = connect(tmp_path / "journal.db")
    bars = _walk(1200)
    cfg = TrainConfig(label=LabelConfig(n_bars=8), features=FEATURES,
                      point=0.001, n_folds=3, pooled_min_rows=10**6)
    models = train_all(build_features(bars_to_frame(bars)), cfg)
    save_models(conn, symbol="XAUUSDc", timeframe="M1",
                config={"n_bars": 8, "seed": 7, "features": list(FEATURES)},
                models=models, train_from_ms=0, train_to_ms=1200 * MINUTE,
                cache_dir=tmp_path / "cache")
    yield conn, bars, tmp_path / "cache"
    conn.close()


def test_scores_every_bar_it_can(trained):
    conn, bars, cache = trained
    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.status == "ok"
    assert report.bars
    for bar in report.bars:
        assert bar.regime in {"trend_up", "trend_down", "range"}
        assert 0.0 <= bar.p_tp_long <= 1.0
        assert 0.0 <= bar.p_tp_short <= 1.0
        assert bar.regime_proba.keys() == {"trend_up", "trend_down", "range"}


def test_report_carries_model_age_and_expectancy(trained):
    conn, bars, cache = trained
    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.model_age_ms is not None and report.model_age_ms >= 0
    assert "expectancy_r" in report.__dict__
    assert report.pooled is True


def test_no_model_is_a_status_not_an_exception(tmp_path):
    conn = connect(tmp_path / "journal.db")
    report = score_bars(conn, "XAUUSDc", "H1", _walk(200), tmp_path / "cache")
    assert report.status == "no_model"
    assert report.bars == []
    conn.close()


def test_missing_artifact_is_a_status_not_an_exception(trained):
    conn, bars, cache = trained
    for path in (cache / "models").glob("*.joblib"):
        path.unlink()
    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.status == "artifact_missing"
    assert report.bars == []


def test_too_few_bars_to_compute_features_is_a_status(trained):
    conn, bars, cache = trained
    report = score_bars(conn, "XAUUSDc", "M1", bars[:5], cache)
    assert report.status == "no_bars"
    assert report.bars == []


def test_stale_feature_schema_is_a_status_not_an_exception(trained):
    """A model trained on a feature `build_features` no longer produces (or
    that `usable_columns` dropped this run) must not raise a bare KeyError
    out of `dropna`/`_matrix` — it needs its own status, distinct from
    no_bars, so /live can prompt a retrain rather than a data fill."""
    conn, bars, cache = trained
    row = conn.execute(
        "SELECT id, config_json FROM lab_models WHERE stage = 'regime' AND active = 1"
    ).fetchone()
    config = json.loads(row["config_json"])
    config["features"] = [*config["features"], "no_longer_computed"]
    conn.execute("UPDATE lab_models SET config_json = ? WHERE id = ?",
                (json.dumps(config), row["id"]))
    conn.commit()

    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.status == "stale_features"
    assert report.bars == []


def test_expectancy_ships_with_its_n_and_is_suppressed_when_thin(trained):
    conn, bars, cache = trained
    row = conn.execute(
        """SELECT id, metrics_json FROM lab_models
            WHERE stage = 'timing' AND regime IS NULL AND active = 1"""
    ).fetchone()
    metrics = json.loads(row["metrics_json"])
    metrics["n_taken"] = 5  # below evaluate.MIN_BUCKET_N (20)
    conn.execute("UPDATE lab_models SET metrics_json = ? WHERE id = ?",
                (json.dumps(metrics), row["id"]))
    conn.commit()

    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.status == "ok"
    assert report.expectancy_n == 5
    assert report.expectancy_r is None


@pytest.fixture()
def stubbed(tmp_path):
    conn = connect(tmp_path / "journal.db")
    yield conn, _walk(400), tmp_path / "cache"
    conn.close()


def test_a_later_per_regime_run_supersedes_the_pooled_timing_model(stubbed):
    """Run 1 trains pooled (thin data), run 2 trains per-regime (more
    candles) — the documented pooled-fallback path. The scored probability
    and the reported expectancy must both come from run 2; a still-active
    pooled row from run 1 would win every bar in `_timing_proba` while /lab's
    table shows run 2's rows as active."""
    conn, bars, cache = stubbed
    _save_stubs(conn, cache, [
        _stub_model("regime", None, _RegimeStub("range")),
        _stub_model("timing", None, _TimingStub(0.9), expectancy=0.99,
                    pooled=True),
    ])
    _save_stubs(conn, cache, [
        _stub_model("regime", None, _RegimeStub("range")),
        *[_stub_model("timing", r, _TimingStub(0.1), expectancy=-0.25)
          for r in REGIMES],
    ])

    report = score_bars(conn, "XAUUSDc", "M1", bars, cache)
    assert report.status == "ok"
    assert report.pooled is False
    assert report.bars[-1].p_tp_long == pytest.approx(0.1)
    assert report.expectancy_r == pytest.approx(-0.25)


def test_expectancy_is_none_when_the_last_bar_regime_has_no_timing_model(stubbed):
    """A run that trained only some regimes must not attribute an unrelated
    regime's expectancy to the badge — the number has to belong to the model
    that actually scored the last bar, or not be shown at all."""
    conn, bars, cache = stubbed
    _save_stubs(conn, cache, [
        _stub_model("regime", None, _RegimeStub("range")),
        _stub_model("timing", "trend_up", _TimingStub(0.8), expectancy=0.77),
    ])

    report = score_bars(conn, "XAUUSDc", "M1", bars, cache)
    assert report.status == "ok"
    assert report.bars[-1].regime == "range"
    assert report.bars[-1].p_tp_long is None
    assert report.expectancy_r is None
    assert report.expectancy_n is None
    assert report.baseline_expectancy_r is None


def test_report_carries_the_baseline_beside_the_expectancy(stubbed):
    """An expectancy with nothing to compare it against reads as an
    unqualified positive R on the surface nearest the order buttons."""
    conn, bars, cache = stubbed
    _save_stubs(conn, cache, [
        _stub_model("regime", None, _RegimeStub("range")),
        _stub_model("timing", None, _TimingStub(0.6), expectancy=0.10,
                    baseline=0.14, pooled=True),
    ])

    report = score_bars(conn, "XAUUSDc", "M1", bars, cache)
    assert report.expectancy_r == pytest.approx(0.10)
    assert report.baseline_expectancy_r == pytest.approx(0.14)
    assert report.baseline_n == 400


def test_the_baseline_is_suppressed_below_twenty_rows(stubbed):
    """CLAUDE.md §8, same treatment expectancy already gets — `n` still ships
    so the UI can say "thin" rather than "none"."""
    conn, bars, cache = stubbed
    _save_stubs(conn, cache, [
        _stub_model("regime", None, _RegimeStub("range")),
        _stub_model("timing", None, _TimingStub(0.6), baseline=0.14, n=9,
                    n_taken=9, pooled=True),
    ])

    report = score_bars(conn, "XAUUSDc", "M1", bars, cache)
    assert report.baseline_expectancy_r is None
    assert report.baseline_n == 9
