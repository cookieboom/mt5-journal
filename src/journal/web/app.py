"""FastAPI app factory for the web dashboard.

`create_app(db_path)` builds the app; `journal serve` runs it under uvicorn on
localhost. The routes live in `web/routes/`, one router per area; this module
only wires them together with the local-only guard, the error shape and the
SPA shell.

Pure consumer of the analytics/render/annotate layers — no MT5 adapter import
anywhere under `web/` (CLAUDE.md rules 1 & 12).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..health import FRONTEND_DIR
from ..store.db import connect
from . import local_only
from .routes import chart, journal, lab, live, paper, storage, training

_DEFAULT_DB = "data/journal.db"
_CACHE_DIR = "cache"

# The built SPA (Vite → frontend/dist). Served at the site root (Phase 5
# cutover); absent until `npm --prefix frontend run build` has run.
_FRONTEND_DIST = FRONTEND_DIR / "dist"


def create_app(db_path: str | None = None, cache_dir: str | None = None) -> FastAPI:
    """Build the app. `db_path` falls back to the `JOURNAL_DB` env var, then the
    default `data/journal.db` — so `journal serve --db ...` can pass it through
    the env when uvicorn imports this factory by string. `cache_dir` follows the
    same shape (env var, then `cache`), so tests can point every cache read and
    write — PNGs, lab artifacts, clear-cache — at `tmp_path`."""
    app = FastAPI(title="mt5-journal")
    app.state.db_path = db_path or os.environ.get("JOURNAL_DB", _DEFAULT_DB)
    app.state.cache_dir = cache_dir or os.environ.get("JOURNAL_CACHE_DIR", _CACHE_DIR)
    connect(app.state.db_path).close()   # create/migrate once; requests only verify
    app.middleware("http")(local_only.guard)

    # The SPA reads `error` off every failed response (`postJson`); FastAPI's
    # own errors carry only `detail`, so they surfaced as a bare "HTTP 422".
    @app.exception_handler(RequestValidationError)
    async def _invalid_body(_request: Request, exc: RequestValidationError):
        errors = exc.errors()
        msg = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body') or 'body'}: {e['msg']}"
            for e in errors)
        return JSONResponse({"error": msg, "detail": jsonable_encoder(errors)},
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)  # FastAPI's subclasses it
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": str(exc.detail), "detail": exc.detail},
                            status_code=exc.status_code, headers=exc.headers)

    for module in (journal, live, chart, training, paper, lab, storage):
        app.include_router(module.router)

    # --------------------------------------------------------------- SPA (React)
    # The built SPA is the ONLY UI (Jinja retired, Phase 5). Assets mount at
    # /assets when a build exists; a catch-all — registered LAST — returns the SPA
    # shell for every other path so React Router owns the client routes. /api/* and
    # the chart PNG are declared above and keep precedence.
    if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="spa-assets",
        )

    _NO_BUILD_HTML = (
        "<!doctype html><meta charset='utf-8'><title>mt5-journal</title>"
        "<body style='font-family:system-ui;background:#0b0a1a;color:#e5e7eb;"
        "padding:2rem'><h1>SPA belum di-build</h1><p>Jalankan "
        "<code>npm --prefix frontend run build</code> lalu muat ulang.</p></body>"
    )

    def _spa_index() -> str:
        index = _FRONTEND_DIST / "index.html"
        return index.read_text(encoding="utf-8") if index.is_file() else _NO_BUILD_HTML

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def spa(full_path: str = ""):
        return HTMLResponse(_spa_index())

    return app
