"""Live positions and the order path (M9).

The web never reaches the bridge: a preview writes nothing, an enqueue inserts
ONE pending `trade_commands` row, and `journal live` executes it.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ... import execute
from ...execute import CommandError, enqueue, enqueue_open
from ...store import prefs_store
from .. import api
from .. import views
from ..deps import get_conn

router = APIRouter()


# URL path segment → command kind. The URL uses hyphens; the kind uses
# underscores (matching trade_commands.kind and domain/commands.KINDS).
_ACTIONS = {
    "sltp": "modify_sltp",
    "close": "close",
    "close-partial": "close_partial",
    "add-volume": "add_volume",
}


@router.get("/api/live")
def api_live(conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(api.live_payload(conn))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/live-status")
def api_live_status(conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse(api.live_status_payload(conn))


@router.get("/api/commands")
def api_commands(conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(api.commands_payload(conn))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- risk-based sizing (writes nothing). Shared by replay and live: the
# panel asks here on every drag, and the live open path recomputes from the
# same function, so a lot the client "knows" is never trusted.
@router.post("/api/size")
def api_size(
    symbol: str = Body(...),
    entry: float | None = Body(None),
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    risk_mode: str = Body("pct"),
    risk_value: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        login = views.account_header(conn)["login"]
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(execute.size_order(
        conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
        risk_mode=risk_mode, risk_value=risk_value,
    )))


# --- live open (M9+ risk sizing). Two-step, same shape as the position
# commands below: preview sizes + validates and writes nothing, enqueue
# re-derives the volume server-side and inserts ONE pending row. Must
# precede /api/live/{position_id}/{action}[/preview] — a literal path
# segment 'open' would otherwise be parsed as position_id and 422.
@router.post("/api/live/open/preview")
def api_open_preview(
    symbol: str = Body(...),
    entry: float | None = Body(None),
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    risk_mode: str = Body("pct"),
    risk_value: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        login = views.account_header(conn)["login"]
        preview = views.preview_open(
            conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
            risk_mode=risk_mode, risk_value=risk_value,
        )
    except (RuntimeError, CommandError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(preview))


@router.post("/api/live/open")
def api_open(
    symbol: str = Body(...),
    entry: float | None = Body(None),
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    risk_mode: str = Body("pct"),
    risk_value: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Enqueue ONE pending open. The volume is derived here, from the same
    `size_order` the preview used — a lot computed in the browser is never
    accepted, and the second derivation is what makes a stale preview
    harmless."""
    try:
        login = views.account_header(conn)["login"]
        sizing = execute.size_order(
            conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
            risk_mode=risk_mode, risk_value=risk_value,
        )
        if sizing["error"] is not None:
            raise CommandError(sizing["error"])
        cmd_id = enqueue_open(
            conn, login, symbol=symbol, direction=sizing["direction"],
            sl=sl, tp=tp, volume=sizing["volume"], price_ref=entry,
        )
    except (RuntimeError, CommandError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "command_id": cmd_id})


@router.get("/api/risk-prefs")
def api_get_risk_prefs(conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse({"prefs": prefs_store.get_risk_prefs(conn)})


@router.put("/api/risk-prefs")
def api_put_risk_prefs(
    prefs: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn),
):
    ts = prefs_store.set_risk_prefs(conn, prefs)
    return JSONResponse({"ok": True, "updated_ms": ts})


# --- two-step trade command (M9 safety: preview writes nothing; enqueue
# inserts ONE pending row; `journal live` executes. Validation lives in
# domain/commands via preview_command/enqueue and is re-run at enqueue.)
@router.post("/api/live/{position_id}/{action}/preview")
def api_preview(
    position_id: int,
    action: str,
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    volume: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    kind = _ACTIONS.get(action)
    if kind is None:
        return JSONResponse({"error": f"Aksi tidak dikenal: {action!r}."}, status_code=404)
    try:
        login = views.account_header(conn)["login"]
        preview = views.preview_command(
            conn, login, position_id, kind, sl=sl, tp=tp, volume=volume
        )
    except (RuntimeError, CommandError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(preview))


@router.post("/api/live/{position_id}/{action}")
def api_enqueue(
    position_id: int,
    action: str,
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    volume: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    kind = _ACTIONS.get(action)
    if kind is None:
        return JSONResponse({"error": f"Aksi tidak dikenal: {action!r}."}, status_code=404)
    try:
        login = views.account_header(conn)["login"]
        cmd_id = enqueue(conn, login, kind, position_id, sl=sl, tp=tp, volume=volume)
    except (RuntimeError, CommandError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "command_id": cmd_id})
