"""Indicator scripts and their computed values (spec 2026-10-07-indicators §1).
Pure DB + stored candles; never the bridge (M9 boundary)."""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ...domain.indicators.lang import ScriptError
from .. import api, backtest, indicators, schemas, trade_context
from ..deps import get_conn

router = APIRouter()

MAX_LAYOUT_BYTES = 64 * 1024


def _script_error(e: ScriptError) -> JSONResponse:
    return JSONResponse({"error": str(e), "script_error": indicators.error_payload(e)},
                        status_code=400)


@router.get("/api/indicators/scripts")
def api_list(conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse({"scripts": indicators.list_scripts(conn)})


@router.post("/api/indicators/scripts")
def api_create(body: schemas.IndicatorScriptRequest,
               conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(indicators.save_script(conn, None, name=body.name, source=body.source))
    except ScriptError as e:
        return _script_error(e)


@router.put("/api/indicators/scripts/{script_id}")
def api_update(script_id: str, body: schemas.IndicatorScriptRequest,
               conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(indicators.save_script(conn, script_id, name=body.name,
                                                   source=body.source))
    except ScriptError as e:
        return _script_error(e)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.delete("/api/indicators/scripts/{script_id}")
def api_delete(script_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        indicators.delete_script(conn, script_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/indicators/validate")
def api_validate(body: schemas.IndicatorValidateRequest):
    return JSONResponse(indicators.validate(body.source))


@router.post("/api/indicators/compute")
def api_compute(body: schemas.IndicatorComputeRequest,
                conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = indicators.compute(
            conn, script=body.script, source=body.source, inputs=body.inputs,
            symbol=body.symbol, timeframe=body.timeframe, from_ms=body.from_ms,
            to_ms=body.to_ms, session_id=body.session_id,
            include_forming=body.include_forming)
    except ScriptError as e:
        return _script_error(e)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(out)


@router.post("/api/indicators/backtest")
def api_backtest(body: schemas.IndicatorBacktestRequest,
                 conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = backtest.run(
            conn, script=body.script, source=body.source, inputs=body.inputs,
            symbol=body.symbol, timeframe=body.timeframe, from_ms=body.from_ms,
            to_ms=body.to_ms, split=body.split, exits=body.exits.model_dump(),
            spread_fallback=body.spread_fallback)
    except ScriptError as e:
        return _script_error(e)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(out)


@router.post("/api/indicators/context")
def api_context(body: schemas.IndicatorContextRequest,
                conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = trade_context.run(
            conn, script=body.script, source=body.source, inputs=body.inputs,
            symbol=body.symbol, timeframe=body.timeframe, window=body.window)
    except ScriptError as e:
        return _script_error(e)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(out)


@router.post("/api/indicators/backtest/replay")
def api_backtest_replay(body: schemas.BacktestReplayRequest,
                        conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = backtest.replay_session(
            conn, symbol=body.symbol, timeframe=body.timeframe,
            decision_msc=body.decision_msc, exit_msc=body.exit_msc,
            lead_bars=body.lead_bars, tail_bars=body.tail_bars)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.get("/api/indicators/layout")
def api_get_layout(conn: sqlite3.Connection = Depends(get_conn)):
    """`layout` is null until the first save; the client owns its schema."""
    return JSONResponse({"layout": indicators.get_layout(conn)})


@router.put("/api/indicators/layout")
def api_put_layout(layout: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
    if len(json.dumps(layout).encode("utf-8")) > MAX_LAYOUT_BYTES:
        return JSONResponse({"error": f"layout exceeds {MAX_LAYOUT_BYTES} bytes"},
                            status_code=400)
    return JSONResponse({"ok": True, "updated_ms": indicators.set_layout(conn, layout)})
