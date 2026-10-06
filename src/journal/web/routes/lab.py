"""Regime + entry-timing models. Candle-only, never the bridge (M9 boundary).
`train` is synchronous — one request, one fit — and the write-lock discipline
is inside `lab_api.train` (read, fit with no connection held, one short
write), not concurrency here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .. import deps
from .. import lab_api
from .. import schemas
from ..deps import get_conn

router = APIRouter()


def _lab(fn, *args, **kwargs):
    """LabRequestError is a caller mistake, not a server fault."""
    try:
        return fn(*args, **kwargs)
    except lab_api.LabRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lab/train")
def api_lab_train(body: schemas.LabTrainRequest,
                  conn: sqlite3.Connection = Depends(get_conn),
                  cache_dir: Path = Depends(deps.cache_dir)):
    return _lab(lab_api.train, conn, body.model_dump(), cache_dir)


@router.get("/api/lab/models")
def api_lab_models(symbol: str | None = None, timeframe: str | None = None,
                   conn: sqlite3.Connection = Depends(get_conn)):
    return lab_api.models_payload(conn, symbol, timeframe)


@router.post("/api/lab/models/{model_id}/activate")
def api_lab_activate(model_id: int,
                     conn: sqlite3.Connection = Depends(get_conn)):
    return _lab(lab_api.activate_payload, conn, model_id)


@router.get("/api/lab/score")
def api_lab_score(symbol: str, timeframe: str,
                  bars: int = lab_api.DEFAULT_SCORE_BARS,
                  conn: sqlite3.Connection = Depends(get_conn),
                  cache_dir: Path = Depends(deps.cache_dir)):
    return lab_api.score_payload(conn, symbol, timeframe, bars, cache_dir)


@router.get("/api/lab/regimes")
def api_lab_regimes(symbol: str, timeframe: str, from_ms: int, to_ms: int,
                    conn: sqlite3.Connection = Depends(get_conn),
                    cache_dir: Path = Depends(deps.cache_dir)):
    return lab_api.regimes_payload(conn, symbol, timeframe, from_ms, to_ms,
                                   cache_dir)
