"""The interactive chart: candles, coverage, live watches, chart/replay
settings and hand drawings. Pure DB; uncovered ranges are queued for
`journal live` to fill, never fetched here (M9 boundary).
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from ...store import prefs_store
from .. import api
from .. import schemas
from ..deps import get_conn

router = APIRouter()


# Drawings (hand annotations) are stored verbatim and the client owns their
# schema. The server only enforces that the blob is a JSON object of sane size
# — a broken client must not be able to write junk unbounded — and normalises
# the symbol to its base (rule 11) so the key never carries a broker suffix.
MAX_DRAWINGS_BYTES = 256 * 1024


@router.get("/api/candles/live")
def api_candles_live(
    symbol: str, timeframe: str, conn: sqlite3.Connection = Depends(get_conn)
):
    return JSONResponse(api.live_candle_payload(conn, symbol, timeframe))


@router.get("/api/candles")
def api_candles(
    symbol: str,
    timeframe: str,
    from_ms: int = Query(..., alias="from"),
    to_ms: int = Query(..., alias="to"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Read-only candle feed for the chart. Serves from the DB and enqueues a
    fill for any uncovered range (never talks to the bridge — M9 boundary).
    A bad symbol/timeframe is a 400; missing/non-integer from/to yield
    FastAPI's own 422."""
    try:
        payload = api.candles_payload(conn, symbol, timeframe, from_ms, to_ms)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(payload)


@router.get("/api/coverage")
def api_coverage(
    symbol: str, timeframe: str, from_ms: int, to_ms: int,
    conn: sqlite3.Connection = Depends(get_conn),
):
    return JSONResponse(api.coverage_payload(conn, symbol, timeframe, from_ms, to_ms))


@router.post("/api/watch")
def api_watch(body: schemas.WatchRequest, conn: sqlite3.Connection = Depends(get_conn)):
    """Web upserts a demand-driven live watch; `journal live` serves it."""
    try:
        return JSONResponse(api.register_watch(conn, body.symbol, body.timeframe))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/backfill")
def api_backfill(body: schemas.RangeRequest, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(api.backfill(
            conn, body.symbol, body.timeframe, body.from_ms, body.to_ms,
        ))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/chart/prefs")
def api_get_chart_prefs(conn: sqlite3.Connection = Depends(get_conn)):
    """Chart settings blob, cross-browser. `prefs` is null until first save;
    the client then falls back to its own defaults / localStorage. Pure DB —
    never talks to the bridge (M9 boundary)."""
    return JSONResponse({"prefs": prefs_store.get_chart_prefs(conn)})


@router.put("/api/chart/prefs")
def api_put_chart_prefs(
    prefs: dict = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Upsert the chart settings blob under key 'chart'. The server stamps
    updated_ms; the body is stored verbatim (the client owns the schema)."""
    ts = prefs_store.set_chart_prefs(conn, prefs)
    return JSONResponse({"ok": True, "updated_ms": ts})


@router.get("/api/replay/prefs")
def api_get_replay_prefs(conn: sqlite3.Connection = Depends(get_conn)):
    """Replay-config popup prefs, cross-browser. `prefs` is null until first
    save; the client then falls back to its own defaults / localStorage.
    Pure DB — never talks to the bridge (M9 boundary)."""
    return JSONResponse({"prefs": prefs_store.get_replay_prefs(conn)})


@router.put("/api/replay/prefs")
def api_put_replay_prefs(
    prefs: dict = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Upsert the replay-config prefs blob under key 'replay'. The server
    stamps updated_ms; the body is stored verbatim (the client owns the
    schema)."""
    ts = prefs_store.set_replay_prefs(conn, prefs)
    return JSONResponse({"ok": True, "updated_ms": ts})


@router.get("/api/drawings")
def api_get_drawings(
    symbol: str,
    session_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Drawings blob for a symbol, or for one replay session when
    `session_id` is given. `drawings` is null until the first save."""
    return JSONResponse({"drawings": prefs_store.get_drawings(conn, symbol, session_id)})


@router.put("/api/drawings")
def api_put_drawings(
    symbol: str,
    body=Body(...),
    session_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Upsert the drawings blob. The server stamps updated_ms."""
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    # .encode("utf-8") is a no-op today: json.dumps()'s default
    # ensure_ascii=True already escapes every non-ASCII codepoint to a
    # \uXXXX sequence, so the dumped string is pure ASCII and len() on it
    # already equals its encoded byte length (verified directly — see the
    # fix-wave report). Kept explicit anyway: it's the correct measure of
    # "bytes on disk" by construction, and stays correct if this ever
    # switches to ensure_ascii=False (prefs_store.set_drawings uses the
    # same default, so today the two are also identical to what's stored).
    if len(json.dumps(body).encode("utf-8")) > MAX_DRAWINGS_BYTES:
        return JSONResponse(
            {"error": f"drawings blob exceeds {MAX_DRAWINGS_BYTES} bytes"}, status_code=400,
        )
    ts = prefs_store.set_drawings(conn, symbol, session_id, body)
    return JSONResponse({"ok": True, "updated_ms": ts})
