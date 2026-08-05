"""lab_models persistence. The ONLY module under lab/ that touches sqlite.

Write-lock discipline (the lesson from deals.sync and fill_range): callers fit
first and call `save_models` afterwards, so no cursor is open across a fit. The
functions here are all short-transaction."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..store.db import now_ms
from .train import TrainedModel


class ArtifactMissing(Exception):
    """The row exists but cache/models/<id>.joblib is gone. Recoverable: the
    config is in the row, so retraining rebuilds the artifact byte-for-byte."""


def save_models(conn: sqlite3.Connection, *, symbol: str, timeframe: str,
                config: dict, models: list[TrainedModel], train_from_ms: int,
                train_to_ms: int, cache_dir: Path,
                activate_new: bool = True) -> list[int]:
    import joblib

    models_dir = Path(cache_dir) / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    created = now_ms()
    ids: list[int] = []

    for model in models:
        cur = conn.execute(
            """INSERT INTO lab_models
                   (created_ms, symbol, timeframe, stage, regime, kind,
                    config_json, metrics_json, train_from_ms, train_to_ms,
                    n_rows, pooled, artifact_path, active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (created, symbol, timeframe, model.stage, model.regime, model.kind,
             json.dumps({**config, "features": list(model.features)}),
             json.dumps(model.metrics), train_from_ms, train_to_ms,
             model.n_rows, int(model.pooled), ""),
        )
        model_id = int(cur.lastrowid)
        path = models_dir / f"{model_id}.joblib"
        joblib.dump(model.estimator, path)
        conn.execute("UPDATE lab_models SET artifact_path = ? WHERE id = ?",
                     (str(path), model_id))
        ids.append(model_id)
    conn.commit()

    if activate_new:
        # Activate the LightGBM row of each group by default; the UI can switch
        # to logreg afterwards. One transaction per group keeps the partial
        # unique index satisfied at every commit point.
        for model, model_id in zip(models, ids):
            if model.kind == "lgbm":
                activate(conn, model_id)
    return ids


def activate(conn: sqlite3.Connection, model_id: int) -> None:
    """Make one model the active one for its group, clearing the previous
    holder in the same transaction so the partial unique index never rejects a
    legitimate switch."""
    row = conn.execute(
        "SELECT symbol, timeframe, stage, regime FROM lab_models WHERE id = ?",
        (model_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no lab model with id {model_id}")
    conn.execute(
        """UPDATE lab_models SET active = 0
            WHERE symbol = ? AND timeframe = ? AND stage = ?
              AND COALESCE(regime, '') = COALESCE(?, '') AND active = 1""",
        (row["symbol"], row["timeframe"], row["stage"], row["regime"]),
    )
    conn.execute("UPDATE lab_models SET active = 1 WHERE id = ?", (model_id,))
    conn.commit()


def list_models(conn: sqlite3.Connection, symbol: str | None = None,
                timeframe: str | None = None) -> list[dict]:
    sql = "SELECT * FROM lab_models"
    where, args = [], []
    if symbol:
        where.append("symbol = ?")
        args.append(symbol)
    if timeframe:
        where.append("timeframe = ?")
        args.append(timeframe)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_ms DESC, id DESC"
    return [_row_to_dict(r) for r in conn.execute(sql, args)]


def load_active(conn: sqlite3.Connection, symbol: str, timeframe: str,
                stage: str, regime: str | None,
                cache_dir: Path) -> tuple[dict, Any] | None:
    import joblib

    row = conn.execute(
        """SELECT * FROM lab_models
            WHERE symbol = ? AND timeframe = ? AND stage = ?
              AND COALESCE(regime, '') = COALESCE(?, '') AND active = 1""",
        (symbol, timeframe, stage, regime),
    ).fetchone()
    if row is None:
        return None
    path = Path(row["artifact_path"])
    if not path.exists():
        raise ArtifactMissing(
            f"lab model {row['id']} has no artifact at {path}. Retrain from /lab."
        )
    return _row_to_dict(row), joblib.load(path)


def _row_to_dict(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["config"] = json.loads(out.pop("config_json"))
    out["metrics"] = json.loads(out.pop("metrics_json"))
    out["active"] = bool(out["active"])
    out["pooled"] = bool(out["pooled"])
    return out
