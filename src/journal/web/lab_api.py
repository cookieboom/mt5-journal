"""Payload builders for the lab endpoints. Mirrors the existing split: this
module does the work, `app.py` only wires routes to it.

The two-phase shape here is the point. `train()` reads bars in one short call,
lets that transaction end, fits with no cursor open, and only then writes. A
training run must never hold the WAL writer — the same rule that fixed the
on-close ingest freeze."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..lab.features import PRICE_FEATURES, bars_to_frame, build_features, usable_columns
from ..lab.labels import LabelConfig
from ..lab.score import score_bars
from ..lab.store import ArtifactMissing, activate, list_models, save_models
from ..lab.train import TrainConfig, train_all
from ..store.candles_store import load_bars

DEFAULT_SCORE_BARS = 300

# CLAUDE.md rule 8 (§8): metrics computed from fewer than this many samples
# are suppressed rather than shown at face value.
_REGIME_FILLER_KEYS = ("auc", "expectancy_r", "baseline_expectancy_r")


class LabRequestError(ValueError):
    """Caller error: a bad feature name, an unknown symbol, too little data.
    `app.py` turns this into a 400 with the message intact."""


def train(conn: sqlite3.Connection, body: dict, cache_dir: Path) -> dict:
    symbol = str(body["symbol"])
    timeframe = str(body["timeframe"])
    wanted = list(body.get("features") or PRICE_FEATURES)

    unknown = [f for f in wanted if f not in PRICE_FEATURES]
    if unknown:
        raise LabRequestError(f"unknown feature(s): {', '.join(unknown)}")

    point = _point_for(conn, symbol)
    from_ms = int(body.get("from_ms") or 0)
    to_ms = int(body.get("to_ms") or 2**62)

    # Phase 1: read. Short, and finished before anything expensive starts.
    bars = load_bars(conn, symbol, timeframe, from_ms, to_ms)
    if not bars:
        raise LabRequestError(
            f"no candles stored for {symbol} {timeframe}. Fill the range first."
        )

    # Phase 2: compute + fit. No DB handle in use.
    df = build_features(bars_to_frame(bars))
    kept, dropped = usable_columns(df, wanted)
    if not kept:
        raise LabRequestError("every requested feature was unusable on this range")

    label = LabelConfig(
        n_bars=int(body.get("n_bars", 24)),
        k_atr=float(body.get("k_atr", 1.0)),
        rr=float(body.get("rr", 2.0)),
        er_threshold=float(body.get("er_threshold", 0.35)),
    )
    cfg = TrainConfig(
        label=label, features=tuple(kept), point=point,
        n_folds=int(body.get("n_folds", 5)), seed=int(body.get("seed", 7)),
        threshold=float(body.get("threshold", 0.5)),
        default_spread_points=float(body.get("default_spread_points", 0.0)),
        pooled_min_rows=int(body.get("pooled_min_rows", 500)),
    )
    try:
        models = train_all(df, cfg)
    except ValueError as exc:
        raise LabRequestError(str(exc)) from exc

    # Phase 3: write. Short again.
    config = {
        "n_bars": label.n_bars, "k_atr": label.k_atr, "rr": label.rr,
        "er_threshold": label.er_threshold, "n_folds": cfg.n_folds,
        "seed": cfg.seed, "threshold": cfg.threshold,
        "default_spread_points": cfg.default_spread_points,
        "pooled_min_rows": cfg.pooled_min_rows, "point": point,
    }
    ids = save_models(
        conn, symbol=symbol, timeframe=timeframe, config=config, models=models,
        train_from_ms=int(bars[0].time_msc), train_to_ms=int(bars[-1].time_msc),
        cache_dir=cache_dir,
    )
    rows = {r["id"]: r for r in list_models(conn, symbol, timeframe)}
    return {
        "model_ids": ids,
        "models": [_public_model(rows[i]) for i in ids if i in rows],
        "dropped_features": dropped,
        "spread_assumed": "spread" in dropped,
        "n_bars_read": len(bars),
    }


def models_payload(conn: sqlite3.Connection, symbol: str | None,
                   timeframe: str | None) -> dict:
    return {"models": [_public_model(r) for r in list_models(conn, symbol, timeframe)]}


def activate_payload(conn: sqlite3.Connection, model_id: int) -> dict:
    try:
        activate(conn, model_id)
    except ValueError as exc:
        raise LabRequestError(str(exc)) from exc
    return {"ok": True, "id": model_id}


def score_payload(conn: sqlite3.Connection, symbol: str, timeframe: str,
                  n_bars: int, cache_dir: Path) -> dict:
    bars = load_bars(conn, symbol, timeframe, 0, 2**62)
    report = score_bars(conn, symbol, timeframe, bars[-n_bars:], cache_dir)
    return _report_to_dict(report)


def regimes_payload(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int, cache_dir: Path) -> dict:
    # Features need history before `from_ms` (EMA50, ATR14, ret_20), so read
    # from the start and clip the answer back to the window the caller asked for.
    bars = load_bars(conn, symbol, timeframe, 0, to_ms)
    report = score_bars(conn, symbol, timeframe, bars, cache_dir)
    out = _report_to_dict(report)
    out["bars"] = [b for b in out["bars"] if from_ms <= b["time_msc"] <= to_ms]
    return out


def _report_to_dict(report) -> dict:
    return {
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "status": report.status,
        "model_age_ms": report.model_age_ms,
        "expectancy_r": report.expectancy_r,
        "expectancy_n": report.expectancy_n,
        "pooled": report.pooled,
        "bars": [
            {
                "time_msc": b.time_msc,
                "regime": b.regime,
                "regime_proba": b.regime_proba,
                "p_tp_long": b.p_tp_long,
                "p_tp_short": b.p_tp_short,
            }
            for b in report.bars
        ],
    }


def _public_model(row: dict) -> dict:
    """Client-facing view of one `lab_models` row.

    For `stage="regime"`, `train.py::_fit_stage`/`_score` reuses the binary
    `fold_metrics` helper with `proba=ones(...)` and `r_net=zeros(...)` so a
    3-class classifier can be scored through the same machinery as the timing
    model. The consequence (verified in review of Task 5): `auc` is always
    exactly 0.5, `expectancy_r`/`baseline_expectancy_r` are always exactly
    0.0, and `win_rate` is really classification accuracy. Those three are
    filler, not measurements, and are stripped/relabelled here — at every
    fold, not just the aggregate — so nothing downstream can render a
    regime model's "expectancy" or "AUC" as if it meant something."""
    if row.get("stage") != "regime":
        return row
    out = dict(row)
    out["metrics"] = _scrub_regime_metrics(row["metrics"])
    return out


def _scrub_regime_metrics(metrics: dict) -> dict:
    m = dict(metrics)
    for key in _REGIME_FILLER_KEYS:
        m.pop(key, None)
    if "win_rate" in m:
        m["accuracy"] = m.pop("win_rate")
    if "folds" in m:
        m["folds"] = [_scrub_regime_metrics(f) for f in m["folds"]]
    return m


def _point_for(conn: sqlite3.Connection, symbol: str) -> float:
    """`symbol_specs.point` converts the spread column into price. Unknown means
    unknown (rule 4) — refuse rather than guess, because a wrong point silently
    scales every cost number."""
    row = conn.execute("SELECT point FROM symbol_specs WHERE symbol = ?",
                       (symbol,)).fetchone()
    if row is None or row["point"] is None:
        raise LabRequestError(
            f"no symbol_specs.point for {symbol}; run `journal sync` first"
        )
    return float(row["point"])
