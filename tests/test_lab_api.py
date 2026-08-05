"""Lab HTTP surface. Uses the same TestClient fixture style as
tests/test_storage_api.py — client/db_path there — plus a raw `conn` fixture
(same style as tests/test_lab_store.py) opened on the SAME db file so a test
can seed candles/symbol_specs directly and see them through the app's own
per-request connection (WAL, both point at one file)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from journal.adapter.base import Candle
from journal.store.candles_store import insert_candle
from journal.store.db import connect
from journal.web.app import create_app

HOUR = 3_600_000
# insert_candle's Trap-15 floor guard (candles_store._MSC_FLOOR = 10**12)
# rejects a bare `i * HOUR` — that is exactly the "seconds leaked through"
# shape it exists to catch. Real epoch ms it is, spacing unchanged.
BASE_MSC = 1_700_000_000_000


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "journal.db"
    connect(p).close()
    return p


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path, tmp_path) -> TestClient:
    # cache_dir isolated under tmp_path, not the repo's real cache/models/ —
    # training writes real .joblib artifacts, and the sibling lab test files
    # (test_lab_store.py, test_lab_score.py) already isolate the same way.
    return TestClient(create_app(str(db_path), cache_dir=str(tmp_path / "cache")))


def _seed_candles(conn, n=1500, symbol="XAUUSDc", timeframe="H1"):
    # `lab_api._point_for` refuses to train without symbol_specs.point — an
    # unknown point silently rescales every spread cost, so it is not guessed.
    conn.execute(
        """INSERT OR REPLACE INTO symbol_specs
               (symbol, symbol_base, digits, point, tick_size, tick_value,
                contract_size, currency_profit, fetched_at)
           VALUES (?, ?, 3, 0.001, 0.001, 0.1, 1.0, 'USD', 0)""",
        (symbol, symbol.rstrip("c")),
    )
    rng = np.random.default_rng(0)
    price = 2000.0
    for i in range(n):
        open_ = price
        price += float(rng.normal(0, 3.0))
        insert_candle(conn, symbol, timeframe, Candle(
            time_msc=BASE_MSC + i * HOUR, open=open_, high=max(open_, price) + 1.0,
            low=min(open_, price) - 1.0, close=price, tick_volume=500,
            spread=25, real_volume=0))
    conn.commit()


def _train_body(**kw):
    body = {
        "symbol": "XAUUSDc", "timeframe": "H1", "n_bars": 8, "k_atr": 1.0,
        "rr": 2.0, "er_threshold": 0.35,
        "features": ["ret_1", "ret_5", "atr_rel", "hour_utc"],
        "n_folds": 3, "threshold": 0.5, "default_spread_points": 0.0,
        # Force the pooled (single-group) timing path regardless of how this
        # rng seed happens to split across the 3 regimes — otherwise
        # test_activate_switches_the_active_model's single-active-model
        # assumption is only true by chance (train_all goes per-regime, with
        # its own independently-active slot per regime, whenever every
        # regime bucket clears the default pooled_min_rows=500).
        "pooled_min_rows": 1_000_000,
    }
    body.update(kw)
    return body


def test_train_returns_models_and_persists_them(client, conn):
    _seed_candles(conn)
    r = client.post("/api/lab/train", json=_train_body())
    assert r.status_code == 200
    body = r.json()
    assert body["model_ids"]
    assert {m["stage"] for m in body["models"]} == {"regime", "timing"}
    assert {m["kind"] for m in body["models"]} == {"logreg", "lgbm"}

    listed = client.get("/api/lab/models?symbol=XAUUSDc&timeframe=H1").json()
    assert len(listed["models"]) == len(body["model_ids"])


def test_train_reports_a_feature_it_had_to_drop(client, conn):
    _seed_candles(conn)
    conn.execute("UPDATE candles SET spread = NULL")
    conn.commit()
    body = client.post("/api/lab/train",
                       json=_train_body(features=["ret_1", "spread"])).json()
    assert "spread" in body["dropped_features"]


def test_train_rejects_an_unknown_feature_name(client, conn):
    _seed_candles(conn)
    r = client.post("/api/lab/train", json=_train_body(features=["ret_1", "moon"]))
    assert r.status_code == 400
    assert "moon" in r.json()["detail"]


def test_train_refuses_when_there_are_not_enough_bars(client, conn):
    _seed_candles(conn, n=60)
    r = client.post("/api/lab/train", json=_train_body())
    assert r.status_code == 400
    assert "not enough" in r.json()["detail"].lower()


def test_activate_switches_the_active_model(client, conn):
    _seed_candles(conn)
    body = client.post("/api/lab/train", json=_train_body()).json()
    logreg = [m for m in body["models"]
              if m["stage"] == "timing" and m["kind"] == "logreg"][0]
    assert client.post(f"/api/lab/models/{logreg['id']}/activate").status_code == 200
    listed = client.get("/api/lab/models?symbol=XAUUSDc&timeframe=H1").json()
    active = [m for m in listed["models"]
              if m["active"] and m["stage"] == "timing"]
    assert [m["id"] for m in active] == [logreg["id"]]


def test_score_reports_no_model_before_training(client, conn):
    _seed_candles(conn)
    body = client.get("/api/lab/score?symbol=XAUUSDc&timeframe=H1").json()
    assert body["status"] == "no_model"


def test_score_returns_the_latest_bar_after_training(client, conn):
    _seed_candles(conn)
    client.post("/api/lab/train", json=_train_body())
    body = client.get("/api/lab/score?symbol=XAUUSDc&timeframe=H1&bars=200").json()
    assert body["status"] == "ok"
    assert body["bars"]
    assert body["model_age_ms"] >= 0


def test_train_refuses_a_symbol_with_no_point_spec(client, conn):
    _seed_candles(conn)
    conn.execute("DELETE FROM symbol_specs WHERE symbol = 'XAUUSDc'")
    conn.commit()
    r = client.post("/api/lab/train", json=_train_body())
    assert r.status_code == 400
    assert "symbol_specs" in r.json()["detail"]


def test_regimes_endpoint_covers_the_requested_window(client, conn):
    _seed_candles(conn)
    client.post("/api/lab/train", json=_train_body())
    window_to = BASE_MSC + 400 * HOUR
    body = client.get(
        f"/api/lab/regimes?symbol=XAUUSDc&timeframe=H1&from_ms={BASE_MSC}&to_ms={window_to}"
    ).json()
    assert body["status"] == "ok"
    assert all(BASE_MSC <= b["time_msc"] <= window_to for b in body["bars"])


def test_regime_model_metrics_are_not_surfaced_as_real_rates(client, conn):
    """train.py's `_score` fills a regime fold's auc/expectancy_r/
    baseline_expectancy_r/calibration with placeholder constants (0.5 / 0.0 /
    0.0 / a degenerate one-bucket curve, since proba=ones collapses it)
    because a 3-class classifier has no R or probability attached — see
    train.py's module docstring and `_score`. The HTTP layer must not hand
    those four to a client at face value; win_rate there is really accuracy
    and must be labelled as such, not left to be read as a trading win rate.
    The scrub must not over-reach: the timing stage's calibration is real and
    must survive untouched."""
    _seed_candles(conn)
    body = client.post("/api/lab/train", json=_train_body()).json()
    regime_models = [m for m in body["models"] if m["stage"] == "regime"]
    timing_models = [m for m in body["models"] if m["stage"] == "timing"]
    assert regime_models
    assert timing_models
    for m in regime_models:
        metrics = m["metrics"]
        assert "auc" not in metrics
        assert "expectancy_r" not in metrics
        assert "baseline_expectancy_r" not in metrics
        assert "calibration" not in metrics
        assert "win_rate" not in metrics
        assert "accuracy" in metrics
        for fold in metrics["folds"]:
            assert "auc" not in fold
            assert "expectancy_r" not in fold
            assert "baseline_expectancy_r" not in fold
            assert "calibration" not in fold
            assert "win_rate" not in fold
            assert "accuracy" in fold
    for m in timing_models:
        metrics = m["metrics"]
        assert "calibration" in metrics
        assert "win_rate" in metrics
