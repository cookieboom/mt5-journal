"""Trades, reports, weekly, the trade PNG and the human layer (notes, tags).

Pure DB: no bridge (M9 boundary).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Body, Depends
from fastapi.responses import FileResponse, JSONResponse, Response

from ...annotate import AnnotateError, add_tag, list_tags, remove_tag, set_annotation
from ...render.chart import NoCandlesError, TradeNotFoundError, render_trade
from ...store import prefs_store
from .. import api
from .. import deps
from ..deps import get_conn

router = APIRouter()


def _parse_week(week: str) -> tuple[int, int]:
    """'YYYY-Www' → (iso_year, iso_week), validated via strptime's ISO
    directives like `cli._parse_iso_week`."""
    dt = datetime.strptime(f"{week}-1", "%G-W%V-%u")
    y, w, _ = dt.isocalendar()
    return y, w


# ------------------------------------------------------------------- api
@router.get("/api/account")
def api_account(conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(api.account_payload(conn))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/dashboard")
def api_dashboard(conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(api.dashboard_payload(conn))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/trades")
def api_trades(
    symbol: str | None = None,
    status: str | None = None,
    source: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return JSONResponse(
            api.trades_payload(conn, symbol=symbol, status=status, source=source)
        )
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# NOTE: must precede /api/trades/{position_id} below — a parametrized path
# would otherwise swallow "png-prefs" as a position_id (422).
@router.get("/api/trades/png-prefs")
def api_get_trade_png_prefs(conn: sqlite3.Connection = Depends(get_conn)):
    """Global trade-PNG render settings, cross-browser. `prefs` null until
    first save. Pure DB (M9 boundary)."""
    return JSONResponse({"prefs": prefs_store.get_trade_png_prefs(conn)})


@router.put("/api/trades/png-prefs")
def api_put_trade_png_prefs(
    prefs: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn),
):
    """Upsert the trade-PNG settings blob under key 'trade_png'."""
    ts = prefs_store.set_trade_png_prefs(conn, prefs)
    return JSONResponse({"ok": True, "updated_ms": ts})


@router.get("/api/trades/{position_id}")
def api_trade_detail(
    position_id: int, conn: sqlite3.Connection = Depends(get_conn)
):
    try:
        payload = api.trade_detail_payload(conn, position_id)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if payload is None:
        return JSONResponse(
            {"error": f"Tidak ada trade dengan position_id {position_id}."},
            status_code=404,
        )
    return JSONResponse(payload)


@router.get("/api/report")
def api_report(conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return JSONResponse(api.report_payload(conn))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/weekly")
def api_weekly_latest(conn: sqlite3.Connection = Depends(get_conn)):
    from ...analytics.weekly import last_complete_iso_week

    y, w = last_complete_iso_week()
    try:
        return JSONResponse(api.weekly_payload(conn, y, w))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/weekly/{week}")
def api_weekly(week: str, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        y, w = _parse_week(week)
    except ValueError:
        return JSONResponse(
            {"error": f"Minggu harus format ISO 'YYYY-Www' (mis. 2026-W28), got {week!r}."},
            status_code=400,
        )
    try:
        return JSONResponse(api.weekly_payload(conn, y, w))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- trade human layer (M6 writes over JSON). Thin over annotate.py; all
# validation (confidence 1-5, orphan-guard, manual-only delete) lives there.
# rule 4: an absent field is null = "not recorded" → stored NULL, never 0.
@router.post("/api/trades/{position_id}/annotate")
def api_annotate(
    position_id: int,
    setup: str | None = Body(None),
    confidence: int | None = Body(None),
    emotion: str | None = Body(None),
    followed_plan: bool | None = Body(None),
    notes: str | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        ann = set_annotation(
            conn, position_id, setup=setup, confidence=confidence,
            emotion=emotion, followed_plan=followed_plan, notes=notes,
        )
    except (AnnotateError, RuntimeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "annotation": api.to_jsonable(ann)})


@router.post("/api/trades/{position_id}/tags")
def api_add_tag(
    position_id: int,
    tag: str = Body(..., embed=True),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        tags = add_tag(conn, position_id, tag)
    except (AnnotateError, RuntimeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "tags": api.to_jsonable(tags)})


@router.post("/api/trades/{position_id}/tags/delete")
def api_remove_tag(
    position_id: int,
    tag: str = Body(..., embed=True),
    conn: sqlite3.Connection = Depends(get_conn),
):
    # Only manual tags are removable; remove_tag's source='manual' filter makes
    # deleting an auto tag a no-op (removed=0), so no guard is needed here.
    try:
        removed = remove_tag(conn, position_id, tag)
        tags = list_tags(conn, position_id)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(
        {"ok": True, "removed": removed, "tags": api.to_jsonable(tags)}
    )


@router.get("/trades/{position_id}/chart.png")
def trade_chart(position_id: int, conn: sqlite3.Connection = Depends(get_conn),
                cache_dir: Path = Depends(deps.cache_dir)):
    """Render (or reuse the cached) PNG for a closed trade. Charts are cache,
    reproducible from the DB (rule 6). A missing window / open trade is a
    plain 404 with a message — never a silently blank image."""
    from ...render.chart import normalize_opts  # local import keeps mpl lazy

    opts = normalize_opts(prefs_store.get_trade_png_prefs(conn))
    try:
        result = render_trade(conn, position_id, opts=opts, cache_dir=cache_dir)
    except (TradeNotFoundError, NoCandlesError, ValueError) as e:
        return Response(str(e), status_code=404, media_type="text/plain")
    except RuntimeError as e:
        return Response(str(e), status_code=400, media_type="text/plain")
    return FileResponse(result.path, media_type="image/png")
