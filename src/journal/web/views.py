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

from .. import execute
from ..analytics.report import build_report
from ..analytics.sessions import session_of
from ..analytics.weekly import build_weekly, iso_week_bounds_ms
from ..annotate import get_annotation, list_tags
from ..domain.commands import build_request
from ..store.db import now_ms, one_account_login
from . import format as fmt

# ~15s = 3× the 5s idle interval `journal live` polls at. A snapshot older than
# this means the live process is probably not running; the view flags itself
# STALE and warns rather than showing figures the human will read as current.
_STALE_MS = 15_000

# int retcode → short NAME, for the audit log. These MIRROR adapter/base.py's
# `TradeRetcode` IntEnum but are DUPLICATED here deliberately: web/ must never
# import the adapter (CLAUDE.md rules 1 & 12), and a name is all the log needs.
# Any code not listed falls back to "retcode {n}" — honest, never a fake label.
_RETCODE_NAMES: dict[int, str] = {
    10004: "REQUOTE",
    10008: "PLACED",
    10009: "DONE",
    10010: "DONE_PARTIAL",
    10016: "INVALID_STOPS",
    10018: "MARKET_CLOSED",
    10019: "NO_MONEY",
    10025: "NO_CHANGES",
    10030: "INVALID_FILL",
}


def _retcode_name(code: int | None) -> str | None:
    """A retcode's NAME (not the bare int). `None` (broker said nothing yet) stays
    `None` so the template can show its own 'unknown' state."""
    if code is None:
        return None
    return _RETCODE_NAMES.get(int(code), f"retcode {code}")


def _opt_float(s: str | None) -> float | None:
    """Parse an optional numeric form field, preserving rule 4 to the letter.

    EMPTY / whitespace → `None` ("leave this level unchanged"). An explicit "0" or
    "0.0" → `0.0` ("clear this level"). These are DIFFERENT and the difference must
    survive: collapsing "" into 0.0 would silently clear a stop the human meant to
    leave; coercing None→0 would do the same. So the two are never merged here.
    """
    if s is None or not s.strip():
        return None
    return float(s)


def _level_word(level: float | None) -> str:
    """A modify-SL/TP level for the plain-language intent string: `None` = leave it
    ('(tetap)'), `0.0` = clear it ('(hapus)'), else the price (rule 4)."""
    if level is None:
        return "(tetap)"
    if abs(level) < 1e-9:
        return "(hapus)"
    return fmt.price(level)


def _intent_text(
    kind: str, pos: sqlite3.Row, *,
    sl: float | None, tp: float | None, volume: float | None,
) -> str:
    """Plain-Indonesian description of exactly what will be queued — the sentence
    the human confirms. No numbers are invented: it echoes what was typed."""
    symbol = pos["symbol"]
    position_id = pos["position_id"]
    if kind == "modify_sltp":
        return (
            f"Ubah SL→{_level_word(sl)}, TP→{_level_word(tp)} "
            f"pada posisi {position_id} ({symbol})"
        )
    if kind == "close":
        held = pos["volume"]
        return f"Tutup {held} lot {symbol} (posisi {position_id})"
    if kind == "close_partial":
        return f"Tutup sebagian {volume} lot {symbol} (posisi {position_id})"
    # add_volume — a hedging account opens a SECOND position, not a bigger one.
    return (
        f"Tambah {volume} lot {symbol} searah posisi {position_id} "
        f"— membuka posisi BARU (akun hedging)"
    )


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
    template only has to render `None` honestly. The dashboard shows the
    at-a-glance cards; the full tables live at /report (`report_context`)."""
    return {"report": build_report(conn)}


def report_context(conn: sqlite3.Connection) -> dict:
    """The deep analytics tables for /report (M8): money, MAE/MFE, and the
    by-session / by-source / by-symbol breakdowns. Same ReportResult the
    dashboard's cards read (build_report already did §9 gating) — the two pages
    are two views of one object, so there is exactly one SQL read per request."""
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


# --------------------------------------------------------------- live (M9)


def live_context(conn: sqlite3.Connection) -> dict:
    """The current open positions (mirrored into `open_positions` by `journal
    live`), their TOTAL FLOATING P&L, and how fresh the snapshot is.

    Honest about a hard ambiguity: with no heartbeat table, an empty `open_positions`
    could mean 'no positions open' OR '`journal live` never ran' — indistinguishable
    here, so the template says both. A snapshot older than `_STALE_MS` flags the view
    STALE with a warning that live may not be running.

    `profit` is FLOATING, in accounts.currency (USC); the template must label it so
    and never present it as realized. `observed_msc` is true UTC (wall clock); it is
    NOT compared with `open_time_msc`, which is broker server time (Trap 7).
    """
    login = one_account_login(conn)
    rows = conn.execute(
        "SELECT * FROM open_positions WHERE account_login = ? "
        "ORDER BY observed_msc DESC, position_id",
        (login,),
    ).fetchall()

    total_floating = sum((r["profit"] or 0.0) for r in rows)

    now = now_ms()
    if rows:
        newest = max(r["observed_msc"] for r in rows)
        age_s = max(0, (now - newest) // 1000)
        stale = (now - newest) > _STALE_MS
        empty = False
    else:
        # No rows: cannot tell 'flat' from 'live never ran'. Not stale (there is
        # no snapshot to be old); the template shows the both-meanings message.
        age_s = None
        stale = False
        empty = True

    return {
        "positions": rows,
        "total_floating": total_floating,
        "age_s": age_s,
        "stale": stale,
        "empty": empty,
    }


def commands_context(conn: sqlite3.Connection, limit: int = 50) -> dict:
    """The trade-command audit log (newest first) mapped for display: the human
    intent, the STATUS, the retcode NAME (never the bare int), and any error text
    (e.g. the never-retried 'process died mid-command' message)."""
    login = one_account_login(conn)
    rows = execute.list_commands(conn, login, limit=limit)
    cmds = [
        {
            "id": r["id"],
            "position_id": r["position_id"],
            "kind": r["kind"],
            "status": r["status"],
            "sl": r["sl"],
            "tp": r["tp"],
            "volume": r["volume"],
            "requested_msc": r["requested_msc"],
            "retcode": r["retcode"],
            "retcode_name": _retcode_name(r["retcode"]),
            "result_volume": r["result_volume"],
            "result_price": r["result_price"],
            "broker_comment": r["broker_comment"],
            "error": r["error"],
        }
        for r in rows
    ]
    return {"commands": cmds}


def preview_command(
    conn: sqlite3.Connection, login: int, position_id: int, kind: str,
    *, sl: float | None, tp: float | None, volume: float | None,
) -> dict:
    """The CONFIRM-step data. Loads the (position, spec) pair and runs
    `build_request` — which VALIDATES — purely, so a command that would be refused
    is refused HERE, before anything is written. Writes NOTHING.

    Returns the plain-language intent plus the exact parsed sl/tp/volume to re-POST
    at the enqueue step. `load_context`/`build_request` raise `CommandError` on
    refusal; that propagates to the route, which renders the error page.
    """
    pos, spec = execute.load_context(conn, login, position_id)
    build_request(kind, pos, spec, sl=sl, tp=tp, volume=volume)  # validates; may raise
    return {
        "intent": _intent_text(kind, pos, sl=sl, tp=tp, volume=volume),
        "position_id": position_id,
        "kind": kind,
        "symbol": pos["symbol"],
        "fields": {"sl": sl, "tp": tp, "volume": volume},
    }
