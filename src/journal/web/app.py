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

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..annotate import AnnotateError, add_tag, list_tags, remove_tag, set_annotation
from ..execute import CommandError, enqueue, enqueue_open
from ..render.chart import NoCandlesError, TradeNotFoundError, render_trade
from ..store import prefs_store
from ..store import training_store
from ..store import candles_store as cs
from ..store.db import connect
from . import views
from . import api
from . import lab_api
from . import training

# URL path segment → command kind. The URL uses hyphens; the kind uses
# underscores (matching trade_commands.kind and domain/commands.KINDS).
_ACTIONS = {
    "sltp": "modify_sltp",
    "close": "close",
    "close-partial": "close_partial",
    "add-volume": "add_volume",
}

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB = "data/journal.db"
_CACHE_DIR = "cache"

# The built SPA (Vite → frontend/dist). Served at the site root (Phase 5
# cutover); absent until `npm --prefix frontend run build` has run.
_FRONTEND_DIST = _HERE.parent.parent.parent / "frontend" / "dist"


def create_app(db_path: str | None = None, cache_dir: str | None = None) -> FastAPI:
    """Build the app. `db_path` falls back to the `JOURNAL_DB` env var, then the
    default `data/journal.db` — so `journal serve --db ...` can pass it through
    the env when uvicorn imports this factory by string. `cache_dir` follows the
    same shape (env var, then `_CACHE_DIR`) — the lab routes accept a caller-
    supplied cache dir so tests can point training's joblib artifacts at
    `tmp_path` instead of the repo's real `cache/models/`; every other route
    still reads `_CACHE_DIR` directly and is unaffected."""
    db_path = db_path or os.environ.get("JOURNAL_DB", _DEFAULT_DB)
    cache_dir = cache_dir or os.environ.get("JOURNAL_CACHE_DIR", _CACHE_DIR)

    app = FastAPI(title="mt5-journal")

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _parse_week(week: str) -> tuple[int, int]:
        """'YYYY-Www' → (iso_year, iso_week), validated via strptime's ISO
        directives like `cli._parse_iso_week`. The one ISO-week parser (fold-in)."""
        dt = datetime.strptime(f"{week}-1", "%G-W%V-%u")
        y, w, _ = dt.isocalendar()
        return y, w

    # ------------------------------------------------------------------- api
    @app.get("/api/account")
    def api_account(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.account_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/dashboard")
    def api_dashboard(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.dashboard_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/live")
    def api_live(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.live_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/live-status")
    def api_live_status(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.live_status_payload(conn))

    @app.get("/api/candles/live")
    def api_candles_live(
        symbol: str, timeframe: str, conn: sqlite3.Connection = Depends(get_conn)
    ):
        return JSONResponse(api.live_candle_payload(conn, symbol, timeframe))

    @app.get("/api/commands")
    def api_commands(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.commands_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/trades")
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
    @app.get("/api/trades/png-prefs")
    def api_get_trade_png_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        """Global trade-PNG render settings, cross-browser. `prefs` null until
        first save. Pure DB (M9 boundary)."""
        return JSONResponse({"prefs": prefs_store.get_trade_png_prefs(conn)})

    @app.put("/api/trades/png-prefs")
    def api_put_trade_png_prefs(
        prefs=Body(...), conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the trade-PNG settings blob under key 'trade_png'."""
        ts = prefs_store.set_trade_png_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})

    @app.get("/api/trades/{position_id}")
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

    @app.get("/api/report")
    def api_report(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.report_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/weekly")
    def api_weekly_latest(conn: sqlite3.Connection = Depends(get_conn)):
        from ..analytics.weekly import last_complete_iso_week

        y, w = last_complete_iso_week()
        try:
            return JSONResponse(api.weekly_payload(conn, y, w))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/weekly/{week}")
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

    @app.get("/api/candles")
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

    @app.get("/api/coverage")
    def api_coverage(
        symbol: str, timeframe: str, from_ms: int, to_ms: int,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        return JSONResponse(api.coverage_payload(conn, symbol, timeframe, from_ms, to_ms))

    @app.post("/api/watch")
    def api_watch(body=Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        """Web upserts a demand-driven live watch; `journal live` serves it."""
        try:
            return JSONResponse(api.register_watch(conn, body["symbol"], body["timeframe"]))
        except (KeyError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/backfill")
    def api_backfill(body=Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.backfill(
                conn, body["symbol"], body["timeframe"],
                int(body["from_ms"]), int(body["to_ms"]),
            ))
        except (KeyError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/chart/prefs")
    def api_get_chart_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        """Chart settings blob, cross-browser. `prefs` is null until first save;
        the client then falls back to its own defaults / localStorage. Pure DB —
        never talks to the bridge (M9 boundary)."""
        return JSONResponse({"prefs": prefs_store.get_chart_prefs(conn)})

    @app.put("/api/chart/prefs")
    def api_put_chart_prefs(
        prefs=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the chart settings blob under key 'chart'. The server stamps
        updated_ms; the body is stored verbatim (the client owns the schema)."""
        ts = prefs_store.set_chart_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})

    @app.get("/api/replay/prefs")
    def api_get_replay_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        """Replay-config popup prefs, cross-browser. `prefs` is null until first
        save; the client then falls back to its own defaults / localStorage.
        Pure DB — never talks to the bridge (M9 boundary)."""
        return JSONResponse({"prefs": prefs_store.get_replay_prefs(conn)})

    @app.put("/api/replay/prefs")
    def api_put_replay_prefs(
        prefs=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the replay-config prefs blob under key 'replay'. The server
        stamps updated_ms; the body is stored verbatim (the client owns the
        schema)."""
        ts = prefs_store.set_replay_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})

    # --- risk-based sizing (writes nothing). Shared by replay and live: the
    # panel asks here on every drag, and the live open path recomputes from the
    # same function, so a lot the client "knows" is never trusted.
    @app.post("/api/size")
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
        return JSONResponse(api.to_jsonable(views.size_order(
            conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
            risk_mode=risk_mode, risk_value=risk_value,
        )))

    # --- live open (M9+ risk sizing). Two-step, same shape as the position
    # commands below: preview sizes + validates and writes nothing, enqueue
    # re-derives the volume server-side and inserts ONE pending row. Must
    # precede /api/live/{position_id}/{action}[/preview] — a literal path
    # segment 'open' would otherwise be parsed as position_id and 422.
    @app.post("/api/live/open/preview")
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

    @app.post("/api/live/open")
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
            sizing = views.size_order(
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

    @app.get("/api/risk-prefs")
    def api_get_risk_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse({"prefs": prefs_store.get_risk_prefs(conn)})

    @app.put("/api/risk-prefs")
    def api_put_risk_prefs(
        prefs=Body(...), conn: sqlite3.Connection = Depends(get_conn),
    ):
        ts = prefs_store.set_risk_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})

    # --- two-step trade command (M9 safety: preview writes nothing; enqueue
    # inserts ONE pending row; `journal live` executes. Validation lives in
    # domain/commands via preview_command/enqueue and is re-run at enqueue.)
    @app.post("/api/live/{position_id}/{action}/preview")
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

    @app.post("/api/live/{position_id}/{action}")
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

    # --- trade human layer (M6 writes over JSON). Thin over annotate.py; all
    # validation (confidence 1-5, orphan-guard, manual-only delete) lives there.
    # rule 4: an absent field is null = "not recorded" → stored NULL, never 0.
    @app.post("/api/trades/{position_id}/annotate")
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

    @app.post("/api/trades/{position_id}/tags")
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

    @app.post("/api/trades/{position_id}/tags/delete")
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

    @app.get("/trades/{position_id}/chart.png")
    def trade_chart(position_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        """Render (or reuse the cached) PNG for a closed trade. Charts are cache,
        reproducible from the DB (rule 6). A missing window / open trade is a
        plain 404 with a message — never a silently blank image."""
        from ..render.chart import normalize_opts  # local import keeps mpl lazy

        opts = normalize_opts(prefs_store.get_trade_png_prefs(conn))
        try:
            result = render_trade(conn, position_id, opts=opts, cache_dir=_CACHE_DIR)
        except (TradeNotFoundError, NoCandlesError, ValueError) as e:
            return Response(str(e), status_code=404, media_type="text/plain")
        except RuntimeError as e:
            return Response(str(e), status_code=400, media_type="text/plain")
        return FileResponse(result.path, media_type="image/png")

    # --------------------------------------------------------- training (Phase D)
    # Replay/training. Pure DB + cached candles; never the bridge (M9 boundary).
    # Results live in training_* tables, untouched by `journal rebuild` (rule 2).
    @app.post("/api/training/sessions")
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

    @app.get("/api/training/sessions")
    def api_training_list(status: str | None = None,
                          conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.to_jsonable(training.list_sessions_view(conn, status)))

    @app.get("/api/training/sessions/{session_id}")
    def api_training_get(session_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        view = training.session_view(conn, session_id)
        if view is None:
            return JSONResponse({"error": f"no training session {session_id}"},
                                status_code=404)
        return JSONResponse(api.to_jsonable(view))

    @app.delete("/api/training/sessions/{session_id}")
    def api_training_delete(session_id: int,
                            conn: sqlite3.Connection = Depends(get_conn)):
        training_store.delete_session(conn, session_id)
        return JSONResponse({"ok": True})

    @app.post("/api/training/sessions/{session_id}/step")
    def api_training_step(session_id: int, n: int = Body(1, embed=True),
                          conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = training.step(conn, session_id, n)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.post("/api/training/sessions/{session_id}/positions")
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

    @app.post("/api/training/sessions/{session_id}/positions/{pid}/close")
    def api_training_close(session_id: int, pid: int,
                           conn: sqlite3.Connection = Depends(get_conn)):
        try:
            pos = training.close_position(conn, session_id, pid)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(pos))

    @app.post("/api/training/sessions/{session_id}/end")
    def api_training_end(session_id: int,
                         conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = training.end_session(conn, session_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.get("/api/training/summary")
    def api_training_summary(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.to_jsonable(training.career_summary(conn)))

    @app.patch("/api/training/positions/{position_id}/sltp")
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

    @app.get("/api/training/sessions/{session_id}/stats")
    def api_training_session_stats(session_id: int,
                                   conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = training.get_session_stats(conn, session_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    # --------------------------------------------------------------------- lab
    # Regime + entry-timing models. Candle-only, never the bridge (M9 boundary,
    # same as /api/candles). `train` is synchronous — one request, one fit — the
    # write-lock discipline is inside lab_api.train (read, then fit with no
    # connection held, then one short write), not concurrency here.
    def _lab(fn, *args, **kwargs):
        """LabRequestError is a caller mistake, not a server fault."""
        try:
            return fn(*args, **kwargs)
        except lab_api.LabRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/lab/train")
    def api_lab_train(body=Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        return _lab(lab_api.train, conn, body, cache_dir)

    @app.get("/api/lab/models")
    def api_lab_models(symbol: str | None = None, timeframe: str | None = None,
                       conn: sqlite3.Connection = Depends(get_conn)):
        return lab_api.models_payload(conn, symbol, timeframe)

    @app.post("/api/lab/models/{model_id}/activate")
    def api_lab_activate(model_id: int,
                         conn: sqlite3.Connection = Depends(get_conn)):
        return _lab(lab_api.activate_payload, conn, model_id)

    @app.get("/api/lab/score")
    def api_lab_score(symbol: str, timeframe: str,
                      bars: int = lab_api.DEFAULT_SCORE_BARS,
                      conn: sqlite3.Connection = Depends(get_conn)):
        return lab_api.score_payload(conn, symbol, timeframe, bars, cache_dir)

    @app.get("/api/lab/regimes")
    def api_lab_regimes(symbol: str, timeframe: str, from_ms: int, to_ms: int,
                        conn: sqlite3.Connection = Depends(get_conn)):
        return lab_api.regimes_payload(conn, symbol, timeframe, from_ms, to_ms,
                                       cache_dir)

    # -------------------------------------------------------- storage & maintenance
    @app.get("/api/storage/overview")
    def api_storage_overview(conn: sqlite3.Connection = Depends(get_conn)):
        p = Path(db_path)
        db_size_bytes = p.stat().st_size if p.is_file() else 0

        wal_p = Path(str(db_path) + "-wal")
        wal_size_bytes = wal_p.stat().st_size if wal_p.is_file() else 0

        total_m1_bars = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

        cache_p = Path(_CACHE_DIR)
        cache_files = [f for f in cache_p.rglob("*") if f.is_file()] if cache_p.is_dir() else []
        cache_size_bytes = sum(f.stat().st_size for f in cache_files)
        cache_files_count = len(cache_files)

        rows = conn.execute("SELECT DISTINCT symbol FROM candle_coverage ORDER BY symbol").fetchall()
        symbols = [r[0] for r in rows]

        return JSONResponse({
            "db_size_bytes": db_size_bytes,
            "wal_size_bytes": wal_size_bytes,
            "total_m1_bars": total_m1_bars,
            "total_trades": total_trades,
            "cache_size_bytes": cache_size_bytes,
            "cache_files_count": cache_files_count,
            "symbols": symbols,
        })

    @app.post("/api/storage/maintenance/clear-cache")
    def api_storage_clear_cache():
        cache_p = Path(_CACHE_DIR)
        cleared_files = 0
        freed_bytes = 0
        if cache_p.is_dir():
            for f in list(cache_p.rglob("*")):
                if f.is_file():
                    try:
                        freed_bytes += f.stat().st_size
                        f.unlink()
                        cleared_files += 1
                    except OSError:
                        pass
        return JSONResponse({
            "cleared_files": cleared_files,
            "freed_bytes": freed_bytes,
        })

    @app.post("/api/storage/maintenance/vacuum")
    def api_storage_vacuum(conn: sqlite3.Connection = Depends(get_conn)):
        if conn.in_transaction:
            conn.commit()
        conn.execute("VACUUM")
        conn.execute("PRAGMA optimize")

        p = Path(db_path)
        db_size_after = p.stat().st_size if p.is_file() else 0
        return JSONResponse({
            "status": "ok",
            "db_size_after": db_size_after,
        })

    @app.post("/api/storage/maintenance/rebuild")
    def api_storage_rebuild(conn: sqlite3.Connection = Depends(get_conn)):
        from ..domain.reconstruct import rebuild

        try:
            report = rebuild(conn)
            n = report.n_trades
        except RuntimeError:
            n = 0

        return JSONResponse({
            "status": "ok",
            "trades_rebuilt": n,
        })

    @app.get("/api/storage/candles/completeness")
    def api_storage_candles_completeness(
        symbol: str,
        tf: str | None = Query(None),
        timeframe: str | None = Query(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        timeframe = timeframe or tf or "M1"
        covered = cs.read_coverage(conn, symbol, timeframe)
        if not covered:
            return JSONResponse({
                "symbol": symbol,
                "timeframe": timeframe,
                "total_bars": 0,
                "from_ms": 0,
                "to_ms": 0,
                "coverage_percent": 0.0,
                "covered_ranges": [],
                "gaps": [],
            })

        overall_from = covered[0][0]
        overall_to = max(b for _, b in covered)
        covered_ranges = [{"from_ms": a, "to_ms": b} for a, b in covered]

        missing = cs.missing_ranges(covered, (overall_from, overall_to))
        gaps = [
            {
                "from_ms": a,
                "to_ms": b,
                "duration_hours": round((b - a) / 3600000.0, 2),
            }
            for a, b in missing
        ]

        total_span = max(1, overall_to - overall_from)
        total_covered_ms = sum(b - a for a, b in covered)
        coverage_percent = round(min(100.0, (total_covered_ms / total_span) * 100.0), 1)

        total_bars_row = conn.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()
        total_bars = total_bars_row[0] if total_bars_row else 0

        return JSONResponse({
            "symbol": symbol,
            "timeframe": timeframe,
            "total_bars": total_bars,
            "from_ms": overall_from,
            "to_ms": overall_to,
            "coverage_percent": coverage_percent,
            "covered_ranges": covered_ranges,
            "gaps": gaps,
        })

    @app.post("/api/storage/candles/fetch")
    def api_storage_candles_fetch(
        body=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        from ..store import candle_queue

        symbol = body.get("symbol")
        timeframe = body.get("timeframe") or body.get("tf") or "M1"
        from_ms = int(body["from_ms"])
        to_ms = int(body["to_ms"])

        if not symbol:
            return JSONResponse({"error": "symbol required"}, status_code=400)

        rid = candle_queue.request_candles(conn, symbol, timeframe, from_ms, to_ms)
        return JSONResponse({"status": "queued", "request_id": rid})

    @app.post("/api/storage/candles/fill-gaps")
    def api_storage_candles_fill_gaps(
        body=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        from ..store import candle_queue

        symbol = body.get("symbol")
        timeframe = body.get("timeframe") or body.get("tf") or "M1"

        if not symbol:
            return JSONResponse({"error": "symbol required"}, status_code=400)

        covered = cs.read_coverage(conn, symbol, timeframe)
        if not covered:
            return JSONResponse({"status": "queued", "requests_count": 0})

        overall_from = covered[0][0]
        overall_to = max(b for _, b in covered)
        missing = cs.missing_ranges(covered, (overall_from, overall_to))

        queued_count = 0
        for g_from, g_to in missing:
            rid = candle_queue.request_candles(conn, symbol, timeframe, g_from, g_to)
            if rid > 0:
                queued_count += 1

        return JSONResponse({"status": "queued", "requests_count": queued_count})

    @app.post("/api/storage/candles/prune")
    def api_storage_candles_prune(
        body=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        from ..store.db import now_ms

        symbol = body.get("symbol")
        older_than_days = int(body.get("older_than_days", 180))
        cutoff_ms = now_ms() - (older_than_days * 86400 * 1000)

        if symbol and symbol != "all":
            cur = conn.execute(
                "DELETE FROM candles WHERE symbol = ? AND time_msc < ?",
                (symbol, cutoff_ms),
            )
            deleted_bars = cur.rowcount
            conn.execute(
                "DELETE FROM candle_coverage WHERE symbol = ? AND to_msc < ?",
                (symbol, cutoff_ms),
            )
            conn.execute(
                "UPDATE candle_coverage SET from_msc = ? WHERE symbol = ? AND from_msc < ? AND to_msc >= ?",
                (cutoff_ms, symbol, cutoff_ms, cutoff_ms),
            )
        else:
            cur = conn.execute(
                "DELETE FROM candles WHERE time_msc < ?",
                (cutoff_ms,),
            )
            deleted_bars = cur.rowcount
            conn.execute(
                "DELETE FROM candle_coverage WHERE to_msc < ?",
                (cutoff_ms,),
            )
            conn.execute(
                "UPDATE candle_coverage SET from_msc = ? WHERE from_msc < ? AND to_msc >= ?",
                (cutoff_ms, cutoff_ms, cutoff_ms),
            )
        conn.commit()

        return JSONResponse({"status": "ok", "deleted_bars": deleted_bars})

    @app.get("/api/storage/candles/export")
    def api_storage_candles_export(
        symbol: str = Query(...),
        tf: str | None = Query(None),
        timeframe: str | None = Query(None),
        from_ms: int | None = Query(None),
        to_ms: int | None = Query(None),
        format: str = Query("json"),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        tf = tf or timeframe or "M1"
        if not symbol:
            return JSONResponse({"error": "symbol required"}, status_code=400)

        f_ms = 0 if from_ms is None else from_ms
        t_ms = 9999999999999 if to_ms is None else to_ms

        bars = cs.load_bars(conn, symbol, tf, f_ms, t_ms)

        if format.lower() == "csv":
            lines = ["time_msc,open,high,low,close,tick_volume"]
            for b in bars:
                lines.append(f"{b.time_msc},{b.open},{b.high},{b.low},{b.close},{b.tick_volume}")
            csv_text = "\n".join(lines) + "\n"
            return Response(
                content=csv_text,
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{symbol}_{tf}_candles.csv"'},
            )

        bar_dicts = [
            {
                "time_msc": b.time_msc,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "tick_volume": b.tick_volume,
            }
            for b in bars
        ]
        return JSONResponse({
            "symbol": symbol,
            "timeframe": tf,
            "count": len(bar_dicts),
            "bars": bar_dicts,
        })

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
