"""Storage page: DB/cache sizes, candle completeness, fetch/fill/prune/export
and maintenance (vacuum, rebuild, clear the render cache).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response

from ...store import candles_store as cs
from .. import deps
from .. import schemas
from ..deps import get_conn

router = APIRouter()


# -------------------------------------------------------- storage & maintenance
@router.get("/api/storage/overview")
def api_storage_overview(
    conn: sqlite3.Connection = Depends(get_conn),
    db_path: str = Depends(deps.db_path),
    cache_dir: Path = Depends(deps.cache_dir),
):
    p = Path(db_path)
    db_size_bytes = p.stat().st_size if p.is_file() else 0

    wal_p = Path(str(db_path) + "-wal")
    wal_size_bytes = wal_p.stat().st_size if wal_p.is_file() else 0

    total_m1_bars = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    cache_p = Path(cache_dir)
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


@router.post("/api/storage/maintenance/clear-cache")
def api_storage_clear_cache(cache_dir: Path = Depends(deps.cache_dir)):
    cache_p = Path(cache_dir)
    models = cache_p / "models"
    cleared_files = 0
    freed_bytes = 0
    if cache_p.is_dir():
        for f in list(cache_p.rglob("*")):
            # Trained lab models live here too, and `lab_models` rows point at
            # them: training output, not a render that can be redrawn.
            if f.is_file() and not f.is_relative_to(models):
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


@router.post("/api/storage/maintenance/vacuum")
def api_storage_vacuum(conn: sqlite3.Connection = Depends(get_conn), db_path: str = Depends(deps.db_path)):
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


@router.post("/api/storage/maintenance/rebuild")
def api_storage_rebuild(conn: sqlite3.Connection = Depends(get_conn)):
    from ...domain.reconstruct import rebuild

    try:
        report = rebuild(conn)
        n = report.n_trades
    except RuntimeError:
        n = 0

    return JSONResponse({
        "status": "ok",
        "trades_rebuilt": n,
    })


@router.get("/api/storage/candles/completeness")
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


@router.post("/api/storage/candles/fetch")
def api_storage_candles_fetch(
    body: schemas.StorageFetchRequest,
    conn: sqlite3.Connection = Depends(get_conn),
):
    from ...store import candle_queue

    rid = candle_queue.request_candles(
        conn, body.symbol, body.timeframe, body.from_ms, body.to_ms)
    return JSONResponse({"status": "queued", "request_id": rid})


@router.post("/api/storage/candles/fill-gaps")
def api_storage_candles_fill_gaps(
    body: schemas.StorageFillGapsRequest,
    conn: sqlite3.Connection = Depends(get_conn),
):
    from ...store import candle_queue

    symbol, timeframe = body.symbol, body.timeframe

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


@router.post("/api/storage/candles/prune")
def api_storage_candles_prune(
    body: schemas.StoragePruneRequest,
    conn: sqlite3.Connection = Depends(get_conn),
):
    from ...store.db import now_ms

    cutoff_ms = now_ms() - body.older_than_days * 86_400_000
    symbol = None if body.symbol in (None, "", "all") else body.symbol
    deleted_bars = cs.prune_before(conn, cutoff_ms, symbol=symbol)

    return JSONResponse({"status": "ok", "deleted_bars": deleted_bars})


@router.get("/api/storage/candles/export")
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
