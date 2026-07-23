"""FastAPI app factory for the M7 web dashboard.

`create_app(db_path)` builds the app; `journal serve` runs it under uvicorn on
localhost. One SQLite connection is opened per request (via the `get_conn`
dependency) and closed in a `finally`, mirroring every CLI command's
`connect(db) ... finally: conn.close()` shape.

Pure consumer of the analytics/render/annotate layers — no MT5 adapter import
anywhere here (CLAUDE.md rules 1 & 12).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..annotate import AnnotateError, add_tag, remove_tag, set_annotation
from ..render.chart import NoCandlesError, TradeNotFoundError, render_trade
from ..store.db import connect
from . import format as fmt
from . import views

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB = "data/journal.db"
_CACHE_DIR = "cache"


def create_app(db_path: str | None = None) -> FastAPI:
    """Build the app. `db_path` falls back to the `JOURNAL_DB` env var, then the
    default `data/journal.db` — so `journal serve --db ...` can pass it through
    the env when uvicorn imports this factory by string."""
    db_path = db_path or os.environ.get("JOURNAL_DB", _DEFAULT_DB)

    app = FastAPI(title="mt5-journal")
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    templates.env.filters.update(
        money=fmt.money, pct=fmt.pct, rmult=fmt.rmult, num=fmt.num,
        gated=fmt.gated, wib=fmt.wib, dur=fmt.dur, price=fmt.price,
    )
    # `gated` and `is_gated` are called as functions inside templates/macros
    # (they take two args — n and the pre-gated average), so they're globals,
    # not filters.
    templates.env.globals["gated"] = fmt.gated
    templates.env.globals["is_gated"] = fmt.is_gated

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def render(request: Request, name: str, ctx: dict, status_code: int = 200) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def error_page(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
        return render(request, "error.html", {"message": message}, status_code)

    # ---------------------------------------------------------------- dashboard

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        try:
            ctx = views.dashboard_context(conn)
            ctx["header"] = views.account_header(conn)
        except RuntimeError as e:
            return error_page(request, str(e))
        return render(request, "dashboard.html", ctx)

    # ------------------------------------------------------------------- report

    @app.get("/report", response_class=HTMLResponse)
    def report_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        """The full analytics tables (M8) the dashboard cards summarise: money,
        MAE/MFE, and the by-session / by-source / by-symbol breakdowns."""
        try:
            ctx = views.report_context(conn)
            ctx["header"] = views.account_header(conn)
        except RuntimeError as e:
            return error_page(request, str(e))
        return render(request, "report.html", ctx)

    # ------------------------------------------------------------------- trades

    @app.get("/trades", response_class=HTMLResponse)
    def trades(
        request: Request,
        symbol: str | None = None,
        status: str | None = None,
        source: str | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            ctx = views.trades_context(conn, symbol=symbol, status=status, source=source)
        except RuntimeError as e:
            return error_page(request, str(e))
        return render(request, "trades.html", ctx)

    @app.get("/trades/{position_id}", response_class=HTMLResponse)
    def trade_detail(
        request: Request,
        position_id: int,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            ctx = views.trade_detail_context(conn, position_id)
        except RuntimeError as e:
            return error_page(request, str(e))
        if ctx is None:
            return error_page(request, f"Tidak ada trade dengan position_id {position_id}.", 404)
        return render(request, "trade_detail.html", ctx)

    @app.get("/trades/{position_id}/chart.png")
    def trade_chart(position_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        """Render (or reuse the cached) PNG for a closed trade. Charts are cache,
        reproducible from the DB (rule 6). A missing window / open trade is a
        plain 404 with a message — never a silently blank image."""
        try:
            result = render_trade(conn, position_id, cache_dir=_CACHE_DIR)
        except (TradeNotFoundError, NoCandlesError, ValueError) as e:
            return Response(str(e), status_code=404, media_type="text/plain")
        except RuntimeError as e:
            return Response(str(e), status_code=400, media_type="text/plain")
        return FileResponse(result.path, media_type="image/png")

    # ------------------------------------------------------------------- weekly

    def _parse_week(week: str) -> tuple[int, int]:
        """'YYYY-Www' → (iso_year, iso_week), validated via strptime's ISO
        directives like `cli._parse_iso_week`."""
        dt = datetime.strptime(f"{week}-1", "%G-W%V-%u")
        y, w, _ = dt.isocalendar()
        return y, w

    @app.get("/weekly", response_class=HTMLResponse)
    def weekly_latest(conn: sqlite3.Connection = Depends(get_conn)):
        from ..analytics.weekly import last_complete_iso_week

        y, w = last_complete_iso_week()
        return RedirectResponse(f"/weekly/{y}-W{w:02d}", status_code=302)

    @app.get("/weekly/{week}", response_class=HTMLResponse)
    def weekly(request: Request, week: str, conn: sqlite3.Connection = Depends(get_conn)):
        try:
            y, w = _parse_week(week)
        except ValueError:
            return error_page(request, f"Minggu harus format ISO 'YYYY-Www' (mis. 2026-W28), got {week!r}.")
        try:
            ctx = views.weekly_context(conn, y, w)
        except RuntimeError as e:
            return error_page(request, str(e))
        return render(request, "weekly.html", ctx)

    # ------------------------------------------------------- writes (human layer)

    def _back(position_id: int) -> RedirectResponse:
        return RedirectResponse(f"/trades/{position_id}", status_code=303)

    @app.post("/trades/{position_id}/annotate")
    def post_annotate(
        request: Request,
        position_id: int,
        setup: str = Form(""),
        confidence: str = Form(""),
        emotion: str = Form(""),
        followed_plan: str = Form(""),  # "yes" | "no" | "" (unset)
        notes: str = Form(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        fp = {"yes": True, "no": False}.get(followed_plan, None)
        conf = int(confidence) if confidence.strip() else None
        try:
            set_annotation(
                conn, position_id,
                setup=setup or None, confidence=conf, emotion=emotion or None,
                followed_plan=fp, notes=notes or None,
            )
        except AnnotateError as e:
            return error_page(request, str(e))
        except RuntimeError as e:
            return error_page(request, str(e))
        return _back(position_id)

    @app.post("/trades/{position_id}/tags")
    def post_add_tag(
        request: Request,
        position_id: int,
        tag: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            add_tag(conn, position_id, tag)
        except AnnotateError as e:
            return error_page(request, str(e))
        except RuntimeError as e:
            return error_page(request, str(e))
        return _back(position_id)

    @app.post("/trades/{position_id}/tags/delete")
    def post_remove_tag(
        position_id: int,
        tag: str = Form(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        # Only manual tags are removable; `remove_tag`'s source='manual' filter
        # makes deleting an auto tag a no-op, so no guard needed here.
        remove_tag(conn, position_id, tag)
        return _back(position_id)

    return app
