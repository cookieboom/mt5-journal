"""lab_models persistence. The ONLY module under lab/ that touches sqlite.

Write-lock discipline (the lesson from deals.sync and fill_range): callers fit
first and call `save_models` afterwards, so no cursor is open across a fit.
`save_models` itself is two phases: every `joblib.dump()` — plausibly slow,
one per stage/regime/kind — runs BEFORE any transaction opens, writing to a
temp filename outside the DB entirely; the transaction that follows only
inserts rows and renames files already sitting on disk into place, then
commits once. Every other function here is a single short transaction."""
from __future__ import annotations

import json
import sqlite3
import uuid
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

    # Phase 1 — no connection work. Every artifact lands under a throwaway
    # temp name; the row (and the id its filename will use) doesn't exist yet,
    # so nothing here can hold, or wait on, the write lock. A dump failure
    # here leaves no transaction open and no row to roll back.
    staged: list[tuple[TrainedModel, Path]] = []
    for model in models:
        tmp_path = models_dir / f".tmp-{uuid.uuid4().hex}.joblib"
        joblib.dump(model.estimator, tmp_path)
        staged.append((model, tmp_path))

    # Phase 2 — one short transaction: insert every row, rename each staged
    # artifact into its final <id>.joblib (a filesystem rename, not a dump —
    # cheap regardless of model size), commit once.
    ids: list[int] = []
    for model, tmp_path in staged:
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
        final_path = models_dir / f"{model_id}.joblib"
        tmp_path.rename(final_path)
        conn.execute("UPDATE lab_models SET artifact_path = ? WHERE id = ?",
                     (str(final_path), model_id))
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
    legitimate switch.

    A timing model also clears the COMPLEMENTARY family. Pooled timing
    (`regime IS NULL`) and the three per-regime models are alternatives —
    `train_all` produces one shape or the other, never both — but the partial
    unique index keys on `COALESCE(regime,'')`, so they are four independent
    groups to it. Left alone, a superseded pooled row stays active after a
    per-regime retrain and `lab.score._timing_proba` takes its pooled branch
    for every bar while /lab's table shows the per-regime rows as active. The
    invariant belongs here, at the one place `active` is ever set, rather than
    being re-derived at every read."""
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
    if row["stage"] == "timing":
        complement = ("regime IS NOT NULL" if row["regime"] is None
                      else "regime IS NULL")
        conn.execute(
            f"""UPDATE lab_models SET active = 0
                 WHERE symbol = ? AND timeframe = ? AND stage = 'timing'
                   AND {complement} AND active = 1""",
            (row["symbol"], row["timeframe"]),
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
