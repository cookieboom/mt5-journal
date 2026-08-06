"""Model persistence. `cache/models/` is cache (CLAUDE.md rule 6): losing it
must leave the rows intact and produce a clear retrain signal, not a crash."""
from __future__ import annotations

import json

import pytest

from journal.lab.store import (
    ArtifactMissing,
    activate,
    list_models,
    load_active,
    save_models,
)
from journal.lab.train import TrainedModel
from journal.store.db import connect


class _Stub:
    """Stands in for an estimator; joblib round-trips it fine."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __eq__(self, other) -> bool:
        return isinstance(other, _Stub) and other.tag == self.tag


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _model(stage="timing", regime="trend_up", kind="lgbm") -> TrainedModel:
    return TrainedModel(
        stage=stage, regime=regime, kind=kind, estimator=_Stub(f"{stage}-{kind}"),
        metrics={"n": 500, "expectancy_r": 0.1, "folds": []}, n_rows=500,
        features=("ret_1", "side"), pooled=False,
    )


def _save(conn, tmp_path, models, symbol="XAUUSDc", timeframe="H1"):
    return save_models(
        conn, symbol=symbol, timeframe=timeframe,
        config={"n_bars": 24, "seed": 7}, models=models,
        train_from_ms=1_000, train_to_ms=2_000, cache_dir=tmp_path / "cache",
    )


def test_save_writes_a_row_and_an_artifact(conn, tmp_path):
    ids = _save(conn, tmp_path, [_model()])
    assert len(ids) == 1
    rows = list_models(conn)
    assert rows[0]["symbol"] == "XAUUSDc"
    assert rows[0]["metrics"]["expectancy_r"] == 0.1
    assert rows[0]["config"]["seed"] == 7
    assert (tmp_path / "cache" / "models" / f"{ids[0]}.joblib").exists()


def test_round_trip_returns_the_estimator(conn, tmp_path):
    _save(conn, tmp_path, [_model()])
    row, est = load_active(conn, "XAUUSDc", "H1", "timing", "trend_up",
                           tmp_path / "cache")
    assert est == _Stub("timing-lgbm")
    assert row["kind"] == "lgbm"


def test_only_one_model_is_active_per_group(conn, tmp_path):
    first = _save(conn, tmp_path, [_model(kind="logreg")])[0]
    second = _save(conn, tmp_path, [_model(kind="lgbm")])[0]
    active = [r for r in list_models(conn) if r["active"]]
    assert [r["id"] for r in active] == [second]

    activate(conn, first)
    active = [r for r in list_models(conn) if r["active"]]
    assert [r["id"] for r in active] == [first]


def _active_timing_regimes(conn) -> set:
    return {r["regime"] for r in list_models(conn)
            if r["active"] and r["stage"] == "timing"}


def test_a_later_per_regime_run_deactivates_the_pooled_timing_model(conn, tmp_path):
    """Pooled timing and per-regime timing are ALTERNATIVES, not four
    independent slots. A first thin run trains one pooled model; a second run
    with more candles trains three per-regime ones. If the pooled row stays
    active, `lab.score` keeps scoring every bar with the superseded model
    (its `pooled` branch wins unconditionally) while /lab's table shows the
    three fresh rows as active — two surfaces disagreeing about which model
    is in effect."""
    _save(conn, tmp_path, [_model(regime=None)])
    assert _active_timing_regimes(conn) == {None}

    _save(conn, tmp_path, [_model(regime=r) for r in
                           ("trend_up", "trend_down", "range")])
    assert _active_timing_regimes(conn) == {"trend_up", "trend_down", "range"}


def test_a_later_pooled_run_deactivates_the_per_regime_timing_models(conn, tmp_path):
    """The symmetric case: three stale per-regime rows left active behind a
    fresh pooled model inflate `model_age_ms` (worst-case across every model
    consulted) to the old age and falsely trip LabBadge's staleness styling."""
    _save(conn, tmp_path, [_model(regime=r) for r in
                           ("trend_up", "trend_down", "range")])
    _save(conn, tmp_path, [_model(regime=None)])
    assert _active_timing_regimes(conn) == {None}


def test_activating_a_pooled_row_by_hand_clears_the_per_regime_ones(conn, tmp_path):
    """Same invariant through the other caller — /lab's Activate button."""
    _save(conn, tmp_path, [_model(regime=r) for r in
                           ("trend_up", "trend_down", "range")])
    pooled_id = _save(conn, tmp_path, [_model(regime=None, kind="logreg")])[0]
    activate(conn, pooled_id)
    assert _active_timing_regimes(conn) == {None}


def test_a_pooled_timing_model_and_the_regime_model_do_not_collide(conn, tmp_path):
    """Cross-STAGE independence still holds: the regime classifier and the
    timing model are different stages and are active at the same time."""
    _save(conn, tmp_path, [_model(stage="regime", regime=None),
                           _model(regime=None)])
    active = [(r["stage"], r["regime"]) for r in list_models(conn) if r["active"]]
    assert sorted(active) == [("regime", None), ("timing", None)]


def test_missing_artifact_raises_a_named_error(conn, tmp_path):
    ids = _save(conn, tmp_path, [_model()])
    (tmp_path / "cache" / "models" / f"{ids[0]}.joblib").unlink()
    with pytest.raises(ArtifactMissing):
        load_active(conn, "XAUUSDc", "H1", "timing", "trend_up", tmp_path / "cache")
    assert list_models(conn), "the row survives the artifact"


def test_load_active_returns_none_when_nothing_is_trained(conn, tmp_path):
    assert load_active(conn, "XAUUSDc", "H1", "timing", "range",
                       tmp_path / "cache") is None


def test_artifact_writes_do_not_hold_the_write_lock(conn, tmp_path, monkeypatch):
    """Pins the write-lock discipline (CLAUDE.md: no cursor open across a fit,
    and the scar tissue from deals.sync/fill_range starving the WAL writer
    across slow work). `joblib.dump()` is the slow call in this module — it
    must never run while `conn` has a write transaction open. Fails if
    save_models reverts to insert-then-dump-then-update per model."""
    import joblib

    seen_in_transaction = []
    real_dump = joblib.dump

    def spy_dump(value, filename, *a, **kw):
        seen_in_transaction.append(conn.in_transaction)
        return real_dump(value, filename, *a, **kw)

    monkeypatch.setattr(joblib, "dump", spy_dump)

    models = [
        _model(kind="logreg", regime="trend_up"),
        _model(kind="lgbm", regime="trend_up"),
        _model(kind="lgbm", regime="range"),
    ]
    _save(conn, tmp_path, models)

    assert seen_in_transaction == [False, False, False]


def test_list_models_filters_by_symbol_and_timeframe(conn, tmp_path):
    _save(conn, tmp_path, [_model()], symbol="XAUUSDc", timeframe="H1")
    _save(conn, tmp_path, [_model()], symbol="BTCUSDc", timeframe="M5")
    assert len(list_models(conn, symbol="BTCUSDc")) == 1
    assert len(list_models(conn, symbol="XAUUSDc", timeframe="M5")) == 0
