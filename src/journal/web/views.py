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
from ..domain import risk
from ..domain.commands import CommandError, build_request, validate
from ..domain.risk import risk_amount
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
    """Thin alias kept for callers here; the definition now lives in `format.py`
    so templates (the audit log) and this intent string render a modify level the
    SAME way — `None`='(tetap)', not 'unknown'."""
    return fmt.level_word(level)


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


# --- equity / cumulative-R tape (M9) --------------------------------------
#
# The dashboard had NO time dimension. `equity_curve` adds one as a PURE,
# tested function: cumulative net_profit (USC) and, separately, cumulative
# r_multiple over only the trades where R is known. It returns both the point
# series AND ready-to-drop inline-SVG geometry so the template stays dumb (no
# arithmetic in Jinja) and the maths is unit-testable. All money stays USC; the
# trace is drawn neutral-INK by the CSS, never money-green (see app.css).

# viewBox units — unitless; the <svg> scales to 100% width via CSS.
_VB_W = 720.0
_VB_H = 160.0
_PAD_X = 6.0
_PAD_Y = 10.0


def _svg_geometry(pts: list[tuple[int, float]]) -> dict:
    """Turn a (time_msc, value) series into inline-SVG geometry. Handles the
    degenerate cases the plan calls out WITHOUT dividing by zero:
      * empty  → `empty=True`, a flat baseline, no points (template shows a note);
      * one point → single dot, flat baseline, no span division;
      * a flat series (all equal, e.g. all-zero) → a synthetic ±1 range so the
        baseline sits centred instead of NaN.
    The value range is forced to include 0 so the dashed zero-line is always in
    view and the curve's relationship to breakeven reads honestly."""
    vb = f"0 0 {_VB_W:g} {_VB_H:g}"
    if not pts:
        return {
            "empty": True, "viewbox": vb, "points": "", "area": "",
            "baseline_y": round(_VB_H / 2, 2),
            "first_msc": None, "last_msc": None, "last_value": None,
            "last_x": None, "last_y": None, "vmin": None, "vmax": None,
        }

    values = [v for _, v in pts]
    vmin = min(0.0, min(values))
    vmax = max(0.0, max(values))
    span = vmax - vmin
    if span < 1e-9:  # flat/degenerate series — avoid /0, centre the baseline
        vmin, vmax, span = -1.0, 1.0, 2.0

    n = len(pts)

    def x_of(i: int) -> float:
        if n == 1:
            return _VB_W / 2
        return _PAD_X + (_VB_W - 2 * _PAD_X) * i / (n - 1)

    def y_of(v: float) -> float:
        return _VB_H - _PAD_Y - (_VB_H - 2 * _PAD_Y) * (v - vmin) / span

    coords = [(x_of(i), y_of(v)) for i, (_, v) in enumerate(pts)]
    points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
    baseline_y = y_of(0.0)
    x0, xl = coords[0][0], coords[-1][0]
    # area polygon: baseline-left → the trace → baseline-right, filled faintly.
    area = f"{x0:.2f},{baseline_y:.2f} {points} {xl:.2f},{baseline_y:.2f}"
    return {
        "empty": False, "viewbox": vb, "points": points, "area": area,
        "baseline_y": round(baseline_y, 2),
        "first_msc": pts[0][0], "last_msc": pts[-1][0], "last_value": pts[-1][1],
        "last_x": round(xl, 2), "last_y": round(coords[-1][1], 2),
        "vmin": vmin, "vmax": vmax,
    }


def equity_curve(conn: sqlite3.Connection) -> dict:
    """Cumulative equity (net_profit, USC) and cumulative-R over CLOSED trades,
    ordered by realized close time. Pure DB read; no writes, no adapter.

    Two honest, separate series:
      * equity — running sum of `net_profit` over every closed trade (USC);
      * r-curve — running sum of `r_multiple` over ONLY the trades where R is
        known (`r_multiple IS NOT NULL`), with `n_with_r` exposed so the template
        can say how many trades it covers. R is unit-free (a ratio), so the two
        curves are never mixed.
    Returns the point series plus inline-SVG geometry for each. Zero and one-trade
    cases are handled by `_svg_geometry` without a ZeroDivisionError."""
    login = one_account_login(conn)
    rows = conn.execute(
        "SELECT close_time_msc, net_profit, r_multiple FROM trades "
        "WHERE account_login = ? AND status = 'closed' AND close_time_msc IS NOT NULL "
        "ORDER BY close_time_msc ASC",
        (login,),
    ).fetchall()

    equity_pts: list[tuple[int, float]] = []
    cum = 0.0
    for r in rows:
        cum += r["net_profit"] or 0.0  # a closed trade should have net; guard anyway
        equity_pts.append((r["close_time_msc"], cum))

    r_pts: list[tuple[int, float]] = []
    cum_r = 0.0
    for r in rows:
        if r["r_multiple"] is not None:
            cum_r += r["r_multiple"]
            r_pts.append((r["close_time_msc"], cum_r))

    return {
        "n": len(equity_pts),
        "n_with_r": len(r_pts),
        "series": [{"close_time_msc": t, "equity": e} for t, e in equity_pts],
        "equity_last": equity_pts[-1][1] if equity_pts else None,
        "r_last": r_pts[-1][1] if r_pts else None,
        "equity_svg": _svg_geometry(equity_pts),
        "r_svg": _svg_geometry(r_pts),
    }


def dashboard_context(conn: sqlite3.Connection) -> dict:
    """Account-wide report (M5) + a live strip + the equity/R tape (M9). The
    ReportResult dataclass already did §9 gating, so the template only renders
    `None` honestly. The full analytics tables live at /report; the live strip
    reuses `live_context` (floating P&L, never realized) and the tape reuses
    `equity_curve`."""
    return {
        "report": build_report(conn),
        "live": live_context(conn),
        "equity": equity_curve(conn),
    }


def report_context(conn: sqlite3.Connection) -> dict:
    """The deep analytics tables for /report (M8): money, MAE/MFE, and the
    by-session / by-source / by-symbol breakdowns. Same ReportResult the
    dashboard's cards read (build_report already did §9 gating) — the two pages
    are two views of one object, so there is exactly one SQL read per request."""
    return {"report": build_report(conn)}


def analytics_series_context(conn: sqlite3.Connection) -> dict:
    """Raw per-trade rows for the /report charts (R-histogram, MAE/MFE scatter,
    daily-P&L calendar). Every CLOSED trade with a realized `close_time_msc`,
    ordered by close time. Same tier as `equity_curve`: a plain DB read, no
    averaging and no §9 gating — the client bins/buckets and applies gating.
    `r_multiple`/`mae_r`/`mfe_r` stay NULL when unknown (rule 4); such trades are
    dropped per-chart on the client, never plotted as 0. Money is raw USC."""
    login = one_account_login(conn)
    rows = conn.execute(
        "SELECT position_id, symbol_base, close_time_msc, net_profit, "
        "r_multiple, mae_r, mfe_r FROM trades "
        "WHERE account_login = ? AND status = 'closed' AND close_time_msc IS NOT NULL "
        "ORDER BY close_time_msc ASC",
        (login,),
    ).fetchall()
    return {"series": rows}


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
        "close_time_msc, duration_s, net_profit, r_multiple, magic "
        "FROM trades WHERE " + " AND ".join(where) + " ORDER BY open_time_msc DESC",
        params,
    ).fetchall()

    # Largest |net| in the visible set — the sparkbar scales each row's bar to
    # this so the per-row win/loss mark is honest RELATIVE to the page. The USC
    # figure itself always sits in its own cell; the bar is only a glance cue.
    max_abs_net = max((abs(r["net_profit"]) for r in rows if r["net_profit"] is not None), default=0.0)

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
        "max_abs_net": max_abs_net,
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

    # Exposure = total open volume in lots (a plain sum; notional in USC would
    # need per-symbol contract maths we don't do here). Labelled "lot" so it is
    # never mistaken for a money figure.
    total_volume = sum((r["volume"] or 0.0) for r in rows)

    return {
        "positions": rows,
        "count": len(rows),
        "total_floating": total_floating,
        "total_volume": total_volume,
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


# ---------------------------------------------------------- risk sizing (M9+)


def size_order(
    conn: sqlite3.Connection, login: int, *,
    symbol: str, entry: float | None, sl: float | None, tp: float | None,
    risk_mode: str, risk_value: float | None,
) -> dict:
    """Derive a lot size from a stop distance and a risk budget.

    Writes nothing. Returns the numbers the panel shows PLUS the reason it
    cannot, so a half-finished drag renders an explanation instead of an HTTP
    error. `error` non-null always means `volume` is null: there is no partial
    answer here, and a number the human could act on must never appear beside a
    refusal.

    The reported `risk_usc` is the risk of the ROUNDED lot — what will actually
    be at stake — which is always at or below the budget, never above it.

    This is arithmetic on numbers the human supplied. It does not choose the
    symbol, the side, the stop, or the moment (rule 9).
    """
    out = {
        "volume": None, "risk_usc": None, "risk_pct": None,
        "distance": None, "rr": None, "direction": None, "error": None,
    }

    spec = conn.execute(
        "SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if spec is None:
        out["error"] = (
            f"Spesifikasi simbol {symbol} belum ada di database — "
            f"jalankan `journal sync` dulu."
        )
        return out

    balance = execute.account_balance(conn, login)

    direction = risk.direction_for_sl(entry, sl)
    if direction is None:
        out["error"] = (
            "SL harus berada di atas atau di bawah harga sekarang — "
            "tarik garisnya menjauh dari harga."
        )
        return out
    out["direction"] = direction
    out["distance"] = abs(entry - sl)

    # Budget in account currency (USC). Percent mode needs a balance; fixed
    # mode does not — but the ceiling check downstream needs one either way.
    if risk_mode == "pct":
        if balance is None:
            out["error"] = (
                "Balance akun belum diketahui — jalankan `journal sync` dulu, "
                "atau isi risiko dalam USC."
            )
            return out
        if risk_value is None or risk_value <= 0:
            out["error"] = "Risiko harus lebih besar dari 0."
            return out
        budget = balance * risk_value / 100.0
    elif risk_mode == "usc":
        if risk_value is None or risk_value <= 0:
            out["error"] = "Risiko harus lebih besar dari 0."
            return out
        budget = risk_value
    else:
        out["error"] = f"Mode risiko tidak dikenal: {risk_mode!r}."
        return out

    raw = risk.volume_for_risk(
        entry, sl, spec["tick_size"], spec["tick_value"], budget
    )
    volume = risk.floor_to_step(raw, spec["volume_step"])
    if volume is None:
        out["error"] = (
            "Ukuran lot tidak bisa dihitung — tick_size/tick_value/volume_step "
            "simbol belum diketahui. Jalankan `journal sync` dulu."
        )
        return out

    # A budget too thin for this stop distance floors to 0 lot. `validate`'s
    # own zero-volume message is generic ("isi volume-nya") — meant for a human
    # who forgot to type a number, not for a budget that computed to nothing.
    # Say what actually happened instead of quietly clamping up to volume_min.
    if volume <= 1e-9:
        vmin = spec["volume_min"]
        out["error"] = (
            f"Budget risiko terlalu kecil untuk jarak SL ini — ukuran lot "
            f"dibulatkan ke bawah menjadi 0"
            + (f" (di bawah volume minimum broker {vmin:g} lot)." if vmin else ".")
        )
        return out

    # One refusal path for everything a real order would be refused for, so the
    # panel can never show a lot the confirm step would then reject.
    pos, _ = execute.load_open_context(conn, login, symbol, direction, entry)
    try:
        validate("open", pos, spec, sl=sl, tp=tp, volume=volume, balance=balance)
    except CommandError as e:
        out["error"] = str(e)
        return out

    realised = risk_amount(entry, sl, spec["tick_size"], spec["tick_value"], volume)
    out["volume"] = volume
    out["risk_usc"] = realised
    out["risk_pct"] = (realised / balance * 100.0) if balance else None
    if tp is not None and abs(tp) > 1e-9:
        out["rr"] = abs(tp - entry) / out["distance"]
    return out
