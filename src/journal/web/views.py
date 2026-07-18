"""Context builders — the seam between the DB and the templates.

Each function takes a `sqlite3.Connection` and returns a plain dict the route
hands to a template. Keeping them separate from the FastAPI routes makes them
unit-testable against a seeded DB with no HTTP layer (mirrors how the analytics
tests exercise `build_report`/`build_weekly` directly).

All reads reuse the existing pure functions; nothing here writes, and nothing
here imports the MT5 adapter (CLAUDE.md rules 1 & 12). Trades are addressed by
`position_id`, never `trades.id` (which renumbers every rebuild).
"""

from __future__ import annotations

import sqlite3

from ..analytics.report import build_report
from ..analytics.sessions import session_of
from ..analytics.weekly import build_weekly, iso_week_bounds_ms
from ..annotate import get_annotation, list_tags
from ..store.db import one_account_login
from . import format as fmt


def account_header(conn: sqlite3.Connection) -> dict:
    """The login/currency/offset every page's header needs. Raises RuntimeError
    (no account / multi-account) up to the route, which renders a friendly page."""
    login = one_account_login(conn)
    row = conn.execute(
        "SELECT currency FROM accounts WHERE login = ?", (login,)
    ).fetchone()
    currency = (row[0] if row else "") or ""
    return {
        "login": login,
        "currency": currency,
        "offset_s": fmt.server_offset_s(conn, login),
    }


def dashboard_context(conn: sqlite3.Connection) -> dict:
    """Account-wide report (M5). The dataclass already did §9 gating, so the
    template only has to render `None` honestly."""
    return {"report": build_report(conn)}


def _tags_by_position(conn: sqlite3.Connection, login: int) -> dict[int, list[tuple[str, str]]]:
    """Every tag for the account, grouped by position_id — one query instead of
    N. Ordered source-first like `list_tags`."""
    out: dict[int, list[tuple[str, str]]] = {}
    for r in conn.execute(
        "SELECT position_id, tag, source FROM tags WHERE account_login = ? "
        "ORDER BY source, tag",
        (login,),
    ):
        out.setdefault(r["position_id"], []).append((r["tag"], r["source"]))
    return out


def trades_context(
    conn: sqlite3.Connection,
    *,
    symbol: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> dict:
    """The trade list, newest-open first, with optional filters. `source` is
    'ea' (magic truthy) or 'disc' (magic NULL/0), matching the report's EA vs
    discretionary split (docs §7)."""
    header = account_header(conn)
    login = header["login"]

    where = ["account_login = ?"]
    params: list = [login]
    if symbol:
        where.append("symbol_base = ?")
        params.append(symbol)
    if status:
        where.append("status = ?")
        params.append(status)
    if source == "ea":
        where.append("magic IS NOT NULL AND magic != 0")
    elif source == "disc":
        where.append("(magic IS NULL OR magic = 0)")

    rows = conn.execute(
        "SELECT position_id, symbol_base, direction, status, open_time_msc, "
        "close_time_msc, net_profit, r_multiple, magic "
        "FROM trades WHERE " + " AND ".join(where) + " ORDER BY open_time_msc DESC",
        params,
    ).fetchall()

    tags = _tags_by_position(conn, login)
    symbols = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symbol_base FROM trades WHERE account_login = ? "
            "ORDER BY symbol_base",
            (login,),
        )
    ]
    return {
        "header": header,
        "trades": rows,
        "tags": tags,
        "symbols": symbols,
        "filters": {"symbol": symbol or "", "status": status or "", "source": source or ""},
    }


def trade_detail_context(conn: sqlite3.Connection, position_id: int) -> dict | None:
    """Full detail + human layer for one trade, or `None` if no such trade
    (route turns that into a 404). `segment` is always 0 on this hedging account."""
    header = account_header(conn)
    login = header["login"]
    trade = conn.execute(
        "SELECT * FROM trades WHERE account_login = ? AND position_id = ? AND segment = 0",
        (login, position_id),
    ).fetchone()
    if trade is None:
        return None

    ann = get_annotation(conn, position_id)
    tags = list_tags(conn, position_id)
    session = session_of(trade["open_time_msc"])
    is_ea = bool(trade["magic"])
    chartable = trade["status"] == "closed" and trade["close_time_msc"] is not None
    return {
        "header": header,
        "trade": trade,
        "annotation": ann,
        "tags": tags,
        "session": session,
        "is_ea": is_ea,
        "chartable": chartable,
    }


def _available_weeks(conn: sqlite3.Connection, login: int) -> list[tuple[int, int]]:
    """Distinct ISO (year, week) that have at least one CLOSED trade, newest
    first — the navigation list for the weekly page. Derived from
    `close_time_msc` (realized), same attribution `build_weekly` uses."""
    seen: dict[tuple[int, int], None] = {}
    from datetime import datetime, timezone

    for r in conn.execute(
        "SELECT close_time_msc FROM trades WHERE account_login = ? "
        "AND status = 'closed' AND close_time_msc IS NOT NULL "
        "ORDER BY close_time_msc DESC",
        (login,),
    ):
        dt = datetime.fromtimestamp(r["close_time_msc"] / 1000, tz=timezone.utc)
        y, w, _ = dt.isocalendar()
        seen.setdefault((y, w), None)
    return list(seen.keys())


def weekly_context(conn: sqlite3.Connection, iso_year: int, iso_week: int) -> dict:
    """One ISO week (M6.1) plus the week-navigation list."""
    header = account_header(conn)
    result = build_weekly(conn, iso_year, iso_week)
    start_ms, _ = iso_week_bounds_ms(iso_year, iso_week)
    return {
        "header": header,
        "result": result,
        "weeks": _available_weeks(conn, header["login"]),
        "start_ms": start_ms,
    }
