"""Dataset assembly and fitting. Two models are fit for every stage on the same
rows — logistic regression and LightGBM — and both are returned. If the boosted
model does not beat the glass box, the caller has the numbers to say so.

No sqlite here: `train_all` takes a DataFrame and returns objects. `lab.store`
does the persistence, which is what keeps the fit out of the WAL writer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .evaluate import aggregate, fold_metrics, purged_folds
from .labels import REGIMES, SIDES, LabelConfig, barrier_labels, regime_labels

_SIDE_CODE = {"long": 1, "short": 0}


@dataclass(frozen=True)
class TrainConfig:
    label: LabelConfig
    features: tuple[str, ...]
    point: float
    n_folds: int = 5
    seed: int = 7
    threshold: float = 0.5
    default_spread_points: float = 0.0
    pooled_min_rows: int = 500


@dataclass(frozen=True)
class TrainedModel:
    stage: str                 # 'regime' | 'timing'
    regime: str | None         # None for stage='regime' and for pooled timing
    kind: str                  # 'logreg' | 'lgbm'
    estimator: Any
    metrics: dict
    n_rows: int
    features: tuple[str, ...]
    pooled: bool = False


def build_dataset(df: pd.DataFrame, cfg: TrainConfig) -> pd.DataFrame:
    """One row per (bar, side). `side` joins the feature list here rather than
    in `features.py` because it belongs to the label, not to the bar."""
    regimes = regime_labels(df, cfg.label)
    frames = []
    for side in SIDES:
        lab = barrier_labels(df, cfg.label, side, point=cfg.point,
                             default_spread_points=cfg.default_spread_points)
        block = df[list(cfg.features)].copy()
        block.index = block.index.rename(None)  # else "time_msc" column below clashes
        block["side"] = side
        block["regime"] = regimes
        block["outcome"] = lab["outcome"]
        block["y"] = (lab["outcome"] == "tp_first").astype("float64")
        block.loc[lab["outcome"].isna(), "y"] = np.nan
        block["r_net"] = lab["r_net"]
        block["time_msc"] = df.index
        frames.append(block)

    data = pd.concat(frames)
    data = data.dropna(subset=["y", "regime", "r_net", *cfg.features])
    # Sort by time so the walk-forward split stays chronological; both sides of
    # a bar sit adjacent and therefore always land in the same fold.
    return data.sort_values(["time_msc", "side"]).reset_index(drop=True)


def train_all(df: pd.DataFrame, cfg: TrainConfig) -> list[TrainedModel]:
    data = build_dataset(df, cfg)
    columns = [*cfg.features, "side_code"]
    data = data.assign(side_code=data["side"].map(_SIDE_CODE).astype("float64"))
    if len(data) < 100:
        raise ValueError(
            f"not enough labelled rows to train: {len(data)}. Fetch more candles "
            f"or lower n_bars."
        )

    out: list[TrainedModel] = []
    out.extend(_fit_stage(data, columns, cfg, stage="regime",
                          target=data["regime"], regime=None, pooled=False))

    groups: list[tuple[str | None, pd.DataFrame]] = []
    if all((data["regime"] == r).sum() >= cfg.pooled_min_rows for r in REGIMES):
        groups = [(r, data[data["regime"] == r]) for r in REGIMES]
        pooled = False
    else:
        groups = [(None, data)]
        pooled = True

    for regime, block in groups:
        out.extend(_fit_stage(block, columns, cfg, stage="timing",
                              target=block["y"], regime=regime, pooled=pooled))
    return out


def _fit_stage(data: pd.DataFrame, columns: list[str], cfg: TrainConfig, *,
               stage: str, target: pd.Series, regime: str | None,
               pooled: bool) -> list[TrainedModel]:
    x = data[columns].to_numpy(dtype="float64")
    y = target.to_numpy()
    r_net = data["r_net"].to_numpy(dtype="float64")
    folds = purged_folds(len(data), cfg.n_folds, cfg.label.n_bars * len(SIDES))
    if not folds:
        raise ValueError(
            f"not enough labelled rows for {cfg.n_folds} purged folds: {len(data)}"
        )

    models: list[TrainedModel] = []
    for kind in ("logreg", "lgbm"):
        per_fold = []
        for train_idx, test_idx in folds:
            est = _new_estimator(kind, cfg.seed)
            if len(np.unique(y[train_idx])) < 2:
                continue
            est.fit(x[train_idx], y[train_idx])
            per_fold.append(
                _score(est, stage, x[test_idx], y[test_idx], r_net[test_idx], cfg)
            )
        if not per_fold:
            raise ValueError(f"{stage}/{kind}: every fold held a single class")

        final = _new_estimator(kind, cfg.seed)
        final.fit(x, y)
        models.append(TrainedModel(
            stage=stage, regime=regime, kind=kind, estimator=final,
            metrics=aggregate(per_fold), n_rows=len(data),
            features=tuple(columns), pooled=pooled,
        ))
    return models


def _score(est, stage: str, x, y, r_net, cfg: TrainConfig) -> dict:
    if stage == "regime":
        pred = est.predict(x)
        hit = (pred == y).astype("float64")
        # A regime model has no R attached; score it as accuracy by reusing the
        # binary metric with a certain "probability" of 1 for its own call.
        out = fold_metrics(hit, np.ones(len(hit)), np.zeros(len(hit)),
                           threshold=cfg.threshold)
        out["confusion"] = _confusion(y, pred)
        return out
    proba = est.predict_proba(x)[:, 1]
    return fold_metrics(y.astype("int64"), proba, r_net, threshold=cfg.threshold)


def _confusion(y_true, y_pred) -> dict:
    out: dict[str, dict[str, int]] = {a: {b: 0 for b in REGIMES} for a in REGIMES}
    for a, b in zip(y_true, y_pred):
        if a in out and b in out[a]:
            out[a][b] += 1
    return out


def _new_estimator(kind: str, seed: int):
    if kind == "logreg":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1_000, random_state=seed),
        )
    from lightgbm import LGBMClassifier

    return LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=31,
        random_state=seed, verbose=-1, deterministic=True,
    )
