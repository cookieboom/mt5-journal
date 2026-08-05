"""Scoring. The states that matter are the degraded ones — no model, missing
artifact, not enough bars — because /live renders them next to order buttons
and must never show a stale or invented number."""
from __future__ import annotations

import json

import numpy as np
import pytest

from journal.adapter.base import Candle
from journal.lab.features import bars_to_frame, build_features
from journal.lab.labels import LabelConfig
from journal.lab.score import score_bars
from journal.lab.store import save_models
from journal.lab.train import TrainConfig, train_all
from journal.store.db import connect

MINUTE = 60_000
FEATURES = ("ret_1", "ret_5", "atr_rel", "hour_utc")


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
