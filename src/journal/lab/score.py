"""Scoring bars with the active models.

Every failure is a STATUS, never an exception that escapes: this output is
rendered on /live beside the order buttons, and a blank panel with a reason is
honest where a stale number is not.

Feature vectors are built from each model's OWN recorded `features` list
(`row["config"]["features"]`, which `lab.store.save_models` sets to
`TrainedModel.features` verbatim) rather than reconstructed locally — that
list already ends in `SIDE_CODE_COLUMN` in the exact order the model was fit
on, and `SIDE_CODE` is imported from `lab.train`, not restated, so the side
encoding can never drift out of sync with what training used (see
`train.py`'s module docstring)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..adapter.base import Candle
from ..store.db import now_ms
from .evaluate import suppressed
from .features import bars_to_frame, build_features
from .labels import REGIMES
from .store import ArtifactMissing, load_active
from .train import SIDE_CODE, SIDE_CODE_COLUMN


@dataclass(frozen=True)
class BarScore:
    time_msc: int
    regime: str
    regime_proba: dict[str, float]
    p_tp_long: float | None
    p_tp_short: float | None


@dataclass(frozen=True)
class ScoreReport:
    symbol: str
    timeframe: str
    bars: list[BarScore]
    model_age_ms: int | None
    expectancy_r: float | None
    expectancy_n: int | None
    pooled: bool
    status: str                 # ok | no_model | artifact_missing | no_bars
                                 # | stale_features


def score_bars(conn: sqlite3.Connection, symbol: str, timeframe: str,
               bars: list[Candle], cache_dir: Path) -> ScoreReport:
    def _empty(status: str) -> ScoreReport:
        return ScoreReport(symbol, timeframe, [], None, None, None, False, status)

    try:
        loaded = load_active(conn, symbol, timeframe, "regime", None, cache_dir)
    except ArtifactMissing:
        return _empty("artifact_missing")
    if loaded is None:
        return _empty("no_model")
    regime_row, regime_model = loaded

    regime_features = list(regime_row["config"]["features"])
    price_features = [f for f in regime_features if f != SIDE_CODE_COLUMN]

    df = build_features(bars_to_frame(bars))
    if df.empty:
        return _empty("no_bars")
    if _missing_price_features(df, regime_features):
        return _empty("stale_features")
    usable = df.dropna(subset=price_features)
    if usable.empty:
        return _empty("no_bars")

    x_regime = _matrix(usable, regime_features, side=None)
    predicted = regime_model.predict(x_regime)
    proba = _class_proba(regime_model, x_regime)

    timing: dict[str | None, tuple[dict, object]] = {}
    try:
        for regime in (*REGIMES, None):
            got = load_active(conn, symbol, timeframe, "timing", regime, cache_dir)
            if got is not None:
                timing[regime] = got
    except ArtifactMissing:
        return _empty("artifact_missing")
    if not timing:
        return _empty("no_model")
    if any(_missing_price_features(usable, row["config"]["features"])
           for row, _ in timing.values()):
        return _empty("stale_features")

    pooled = None in timing
    long_arr = _timing_proba(timing, predicted, usable, "long", pooled)
    short_arr = _timing_proba(timing, predicted, usable, "short", pooled)

    # Staleness is reported worst-case: the oldest of every model actually
    # consulted for this report (the regime model plus every timing model
    # loaded), not just whichever one scored the last bar.
    model_age_ms = now_ms() - min(
        regime_row["created_ms"],
        *(row["created_ms"] for row, _ in timing.values()),
    )

    # Provenance for expectancy_r: whichever timing model scored the LAST bar
    # — pooled if present, else the model for that bar's predicted regime,
    # else whatever is loaded (a run that trained only some regimes is
    # possible, so this falls back rather than KeyError).
    latest_regime = str(predicted[-1])
    chosen_row, _ = timing.get(None) or timing.get(latest_regime) or next(iter(timing.values()))

    scored = [
        BarScore(
            time_msc=int(t),
            regime=str(predicted[i]),
            regime_proba=proba[i],
            p_tp_long=_none_if_nan(long_arr[i]),
            p_tp_short=_none_if_nan(short_arr[i]),
        )
        for i, t in enumerate(usable.index)
    ]
    # CLAUDE.md §8: expectancy_r ships with its n and is suppressed below
    # MIN_BUCKET_N — a thin-sample expectancy next to an order button on
    # /live is exactly the "noise with a decimal point" the rule guards
    # against. `n_taken` (not `n`) is the right count: it's the number of
    # rows the model would actually have entered, which is what
    # expectancy_r itself was averaged over.
    expectancy_n = chosen_row["metrics"].get("n_taken")
    expectancy_r = suppressed(
        chosen_row["metrics"].get("expectancy_r"), expectancy_n or 0
    )
    return ScoreReport(
        symbol=symbol, timeframe=timeframe, bars=scored,
        model_age_ms=model_age_ms,
        expectancy_r=expectancy_r,  # out-of-sample, suppressed if thin
        expectancy_n=expectancy_n,
        pooled=pooled, status="ok",
    )


def _missing_price_features(df: pd.DataFrame, features: list[str]) -> set[str]:
    """Which of a model's recorded feature columns (excluding the synthetic
    side_code column, which is never a df column — `_matrix` builds it) are
    absent from `df`. Non-empty means the model was fit on a feature schema
    this data no longer produces: `features.py` changed, or `usable_columns`
    dropped this column for this run. That is a retrain signal, not a bug to
    crash on."""
    return {f for f in features if f != SIDE_CODE_COLUMN and f not in df.columns}


def _matrix(df: pd.DataFrame, features: list[str], side: str | None) -> np.ndarray:
    columns = []
    for name in features:
        if name == SIDE_CODE_COLUMN:
            value = float(SIDE_CODE[side]) if side is not None else 0.0
            columns.append(np.full(len(df), value))
        else:
            columns.append(df[name].to_numpy(dtype="float64"))
    return np.column_stack(columns)


def _class_proba(model, x: np.ndarray) -> list[dict[str, float]]:
    raw = model.predict_proba(x)
    classes = list(getattr(model, "classes_", REGIMES))
    return [
        {r: float(row[classes.index(r)]) if r in classes else 0.0 for r in REGIMES}
        for row in raw
    ]


def _timing_proba(timing: dict, predicted: np.ndarray, df: pd.DataFrame,
                  side: str, pooled: bool) -> np.ndarray:
    """One probability per row. With per-regime models each row is scored by
    the model for ITS predicted regime, so this walks the regimes rather than
    calling predict once. A regime with no active timing model leaves its
    rows NaN, which `_none_if_nan` turns into an honest `None`."""
    out = np.full(len(df), np.nan)
    if pooled:
        row_features = list(timing[None][0]["config"]["features"])
        _, model = timing[None]
        out[:] = model.predict_proba(_matrix(df, row_features, side))[:, 1]
        return out
    for regime, (row, model) in timing.items():
        mask = predicted == regime
        if not mask.any():
            continue
        row_features = list(row["config"]["features"])
        out[mask] = model.predict_proba(
            _matrix(df[mask], row_features, side)
        )[:, 1]
    return out


def _none_if_nan(value: float) -> float | None:
    return None if np.isnan(value) else float(value)
