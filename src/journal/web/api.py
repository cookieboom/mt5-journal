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


def live_payload(conn: sqlite3.Connection) -> dict:
    """Header + the open-positions strip (floating P&L, staleness) for /api/live.
    Wraps `views.live_context`; adds no logic. `profit` is FLOATING (USC);
    `observed_msc` is true UTC (never compared with the server-time open_time_msc)."""
    return to_jsonable({
        "header": views.account_header(conn),
        "live": views.live_context(conn),
    })


def commands_payload(conn: sqlite3.Connection) -> dict:
    """Header + the trade-command audit log (newest first) for /api/commands.
    Wraps `views.commands_context`; the retcode NAME (never the bare int) and any
    error text arrive already mapped."""
    return to_jsonable({
        "header": views.account_header(conn),
        "commands": views.commands_context(conn)["commands"],
    })
