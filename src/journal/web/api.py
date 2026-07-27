"""The /api JSON layer over the M7 context builders.

A thin, testable seam: `to_jsonable` makes any builder's return value
JSON-safe, and each `*_payload` wraps exactly one `web/views.py` builder — no
business logic lives here. Never imports the MT5 adapter (CLAUDE.md rules 1, 12).
Money stays raw in `accounts.currency` (USC); the client formats. NULL stays
null (rule 4); the §9 gate arrives as null and is passed through untouched.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

from ..adapter.base import TIMEFRAMES
from ..store import candle_queue
from ..store import candles_store as cs
from . import views


def to_jsonable(obj: Any) -> Any:
    """Recursively convert builder output to JSON-safe values.

    Handles dataclasses (field-by-field, so a Row nested in a dataclass is still
    converted), `sqlite3.Row`, dict, and list/tuple. Primitives pass through.
    Anything else raises `TypeError` rather than silently dropping data."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, sqlite3.Row):
        return {k: to_jsonable(obj[k]) for k in obj.keys()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def account_payload(conn: sqlite3.Connection) -> dict:
    """`{login, currency, offset_s}` — the header every page needs. Raises
    RuntimeError (no account / multi-account) up to the route."""
    return to_jsonable(views.account_header(conn))


def dashboard_payload(conn: sqlite3.Connection) -> dict:
    """Header + the M5 report + live strip + equity/R tape — the Dashboard's
    single JSON read. Wraps `views.dashboard_context`; adds no logic. The §9
    gate and NULLs arrive as JSON null and pass through untouched."""
    ctx = views.dashboard_context(conn)
    return to_jsonable({
        "header": views.account_header(conn),
        "report": ctx["report"],
        "live": ctx["live"],
        "equity": ctx["equity"],
    })


def report_payload(conn: sqlite3.Connection) -> dict:
    """Header + the M8 analytics report + the raw per-trade chart series for
    /api/report. Composes `views.report_context` and `views.analytics_series_context`
    (like `dashboard_payload` composes its pieces); adds no logic. The §9 gate and
    NULLs arrive as JSON null and pass through untouched (rule 4). Money stays raw USC."""
    return to_jsonable({
        "header": views.account_header(conn),
        "report": views.report_context(conn)["report"],
        "series": views.analytics_series_context(conn)["series"],
    })


def weekly_payload(conn: sqlite3.Connection, iso_year: int, iso_week: int) -> dict:
    """Header + one ISO week's `WeeklyResult` + the week-navigation list for
    /api/weekly. Wraps `views.weekly_context`; adds no logic. `net_total` is a
    realized sum (always shown); rate/average fields arrive `null` when §9-gated
    (a single week rarely clears n≥20) — never 0 (rule 4). Money raw USC; the
    route resolves which (year, week) to pass."""
    return to_jsonable(views.weekly_context(conn, iso_year, iso_week))


def live_payload(conn: sqlite3.Connection) -> dict:
    """Header + the open-positions strip (floating P&L, staleness) for /api/live.
    Wraps `views.live_context`; adds no logic. `profit` is FLOATING (USC);
    `observed_msc` is true UTC (never compared with the server-time open_time_msc)."""
    return to_jsonable({
        "header": views.account_header(conn),
        "live": views.live_context(conn),
    })


def live_status_payload(
    conn: sqlite3.Connection, *, stale_ms: int = 15_000, now_msc: int | None = None
) -> dict:
    """Is `journal live` running? `live` is True when the last heartbeat is newer
    than `stale_ms`. `now_msc` is injectable for tests; None = real clock."""
    from ..store import live_store
    from ..store.db import now_ms

    now = now_ms() if now_msc is None else now_msc
    beat = live_store.read_heartbeat(conn)
    if beat is None:
        return {"live": False, "beat_msc": None, "age_ms": None}
    age = now - beat
    return {"live": age < stale_ms, "beat_msc": beat, "age_ms": age}


def live_candle_payload(conn: sqlite3.Connection, symbol: str, timeframe: str, *,
                        now_msc: int | None = None) -> dict:
    """The forming bar (or None) plus liveness — the FE poll for a live chart."""
    from ..store import live_store

    status = live_status_payload(conn, now_msc=now_msc)
    c = live_store.read_forming(conn, symbol, timeframe)
    forming = None if c is None else {
        "time_msc": c.time_msc, "o": c.open, "h": c.high, "l": c.low,
        "c": c.close, "v": c.tick_volume,
    }
    return {"forming": forming, "beat_msc": status["beat_msc"], "live": status["live"]}


def commands_payload(conn: sqlite3.Connection) -> dict:
    """Header + the trade-command audit log (newest first) for /api/commands.
    Wraps `views.commands_context`; the retcode NAME (never the bare int) and any
    error text arrive already mapped."""
    return to_jsonable({
        "header": views.account_header(conn),
        "commands": views.commands_context(conn)["commands"],
    })


def trades_payload(
    conn: sqlite3.Connection,
    *,
    symbol: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> dict:
    """The /api/trades list: trade rows (newest-open first) with optional
    symbol/status/source filters, the per-page `max_abs_net` the sparkbar scales
    to, tags grouped by position_id, and the distinct symbol list for the filter
    chips. Wraps `views.trades_context`; adds no logic. Money stays raw USC;
    a trade's unknown `r_multiple` stays null (rule 4)."""
    return to_jsonable(
        views.trades_context(conn, symbol=symbol, status=status, source=source)
    )


def trade_detail_payload(conn: sqlite3.Connection, position_id: int) -> dict | None:
    """Full facts + human layer for one trade, or `None` if there is no such
    trade (route → 404). Wraps `views.trade_detail_context`; adds no logic.
    `sl_initial`/`tp_initial`/`r_multiple` may be null = unknown (rule 4, never
    0); `annotation` is null until one is written; `chartable` says whether the
    reused `/trades/{id}/chart.png` will render."""
    ctx = views.trade_detail_context(conn, position_id)
    return None if ctx is None else to_jsonable(ctx)


def register_watch(conn: sqlite3.Connection, symbol: str, timeframe: str, *,
                   ttl_ms: int = 30_000, now_msc: int | None = None) -> dict:
    """Web-side: upsert a demand-driven live watch. `journal live` serves it."""
    from ..adapter.base import TIMEFRAMES
    from ..store import live_store
    from ..store.db import now_ms

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")
    now = now_ms() if now_msc is None else now_msc
    live_store.upsert_watch(conn, symbol, timeframe, now, ttl_ms)
    return {"ok": True}


def candles_payload(
    conn: sqlite3.Connection, symbol: str, timeframe: str,
    from_ms: int, to_ms: int, *, max_bars: int = 5000,
) -> dict:
    """Serve candles from the DB (native, else aggregated from M1). NEVER touches
    the bridge: if a range is uncovered it ENQUEUES a fill (deduped) for
    `journal live` to drain, and reports `missing`/`pending` so the client polls.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")

    bars = cs.load_bars(conn, symbol, timeframe, from_ms, to_ms)

    if len(bars) > max_bars:
        bars = bars[-max_bars:]

    missing = cs.missing_ranges(cs.read_coverage(conn, symbol, timeframe), (from_ms, to_ms))
    pending = False
    if missing:
        candle_queue.request_candles(conn, symbol, timeframe, from_ms, to_ms)
        pending = True

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {"time_msc": b.time_msc, "o": b.open, "h": b.high,
             "l": b.low, "c": b.close, "v": b.tick_volume}
            for b in bars
        ],
        "missing": [[lo, hi] for lo, hi in missing],
        "pending": pending,
    }
