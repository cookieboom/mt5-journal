"""Replay/training (Phase D). Pure DB + cached candles; never the bridge.
Results live in training_* tables, untouched by `journal rebuild` (rule 2).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ...store import training_store
from .. import api
from .. import training
from ..deps import get_conn

router = APIRouter()


# --------------------------------------------------------- training (Phase D)
# Replay/training. Pure DB + cached candles; never the bridge (M9 boundary).
# Results live in training_* tables, untouched by `journal rebuild` (rule 2).
@router.post("/api/training/sessions")
def api_training_create(
    symbol: str = Body(...),
    timeframe: str = Body(...),
    range_start_msc: int = Body(...),
    range_end_msc: int = Body(...),
    cursor_start_msc: int | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        out = training.create_session(
            conn, symbol=symbol, timeframe=timeframe,
            range_start_msc=range_start_msc, range_end_msc=range_end_msc,
            cursor_start_msc=cursor_start_msc,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.get("/api/training/sessions")
def api_training_list(status: str | None = None,
                      conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse(api.to_jsonable(training.list_sessions_view(conn, status)))


@router.get("/api/training/sessions/{session_id}")
def api_training_get(session_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    view = training.session_view(conn, session_id)
    if view is None:
        return JSONResponse({"error": f"no training session {session_id}"},
                            status_code=404)
    return JSONResponse(api.to_jsonable(view))


@router.delete("/api/training/sessions/{session_id}")
def api_training_delete(session_id: int,
                        conn: sqlite3.Connection = Depends(get_conn)):
    training_store.delete_session(conn, session_id)
    return JSONResponse({"ok": True})


@router.post("/api/training/sessions/{session_id}/step")
def api_training_step(session_id: int, n: int = Body(1, embed=True),
                      conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = training.step(conn, session_id, n)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.post("/api/training/sessions/{session_id}/positions")
def api_training_open(
    session_id: int,
    direction: str = Body(...),
    volume: float = Body(...),
    sl: float = Body(0.0),
    tp: float = Body(0.0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        pos = training.open_position(conn, session_id, direction=direction,
                                     volume=volume, sl=sl, tp=tp)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(pos))


@router.post("/api/training/sessions/{session_id}/positions/{pid}/close")
def api_training_close(session_id: int, pid: int,
                       conn: sqlite3.Connection = Depends(get_conn)):
    try:
        pos = training.close_position(conn, session_id, pid)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(pos))


@router.post("/api/training/sessions/{session_id}/end")
def api_training_end(session_id: int,
                     conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = training.end_session(conn, session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.get("/api/training/summary")
def api_training_summary(include_study: bool = False,
                         conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse(api.to_jsonable(training.career_summary(conn, include_study)))


@router.patch("/api/training/positions/{position_id}/sltp")
def api_training_modify_sltp(
    position_id: int,
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        position = training.modify_sltp(conn, position_id, sl, tp)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"position": api.to_jsonable(position)})


@router.get("/api/training/sessions/{session_id}/stats")
def api_training_session_stats(session_id: int,
                               conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = training.get_session_stats(conn, session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))
