"""Display formatters for the web layer — the HTML mirror of `cli.py`'s `_fmt`/
`_gated`, kept here as PURE functions (no typer) so templates and tests can use
them directly.

Every rule the CLI honors, this honors:
  * money always carries its currency code — never a bare '$' (Trap 13). The unit
    is `accounts.currency` = USC (cents) on this account.
  * `None` reads "n/a", NEVER 0 — a missing value is not a real zero (rule 4).
  * an averaged statistic under docs §9's n<20 gate is already `None` in the
    dataclass; `gated()` renders that as an honest "insufficient data" note,
    never a fabricated number.
  * `*_msc` are broker SERVER-time epoch ms; convert to WIB (UTC+7) ONLY here at
    display time (rule 3), reusing the measured offset like `render/chart.py`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

_WIB = timezone(timedelta(hours=7))  # display zone only (CLAUDE.md rule 3)


def money(x: float | None, ccy: str = "", *, sign: bool = False) -> str:
    """Money → text. `None` → "n/a" (never 0). Always carries the currency code."""
    if x is None:
        return "n/a"
    s = f"{x:+,.2f}" if sign else f"{x:,.2f}"
    return f"{s} {ccy}".strip()


def pct(x: float | None) -> str:
    """A rate in [0,1] → "48.2%". `None` → "n/a"."""
    return "n/a" if x is None else f"{x * 100:.1f}%"


def rmult(x: float | None) -> str:
    """R-multiple → "1.35R" (unit-free). `None` → "n/a"."""
    return "n/a" if x is None else f"{x:.2f}R"


def num(x: float | None, *, sign: bool = False) -> str:
    """A plain unit-free number (e.g. profit factor). `None` → "n/a"."""
    if x is None:
        return "n/a"
    return f"{x:+.2f}" if sign else f"{x:.2f}"


def gated(n: int, avg: float | None, *, unit: str = "") -> str:
    """A §9-gated statistic. When the dataclass already withheld the average
    (`avg is None`) because n<20, say WHY with the count — never a silent blank
    or a fake 0. Otherwise show the value beside its n."""
    if avg is None:
        return f"n/a (n={n}, perlu ≥20)"
    body = f"{avg:.2f}{unit}"
    return f"{body}  (n={n})"


def is_gated(n: int, avg: float | None) -> bool:
    """True when a statistic is being withheld for thin data — templates use this
    to grey the row (docs §9)."""
    return avg is None and n < 20


def wib(server_msc: int | None, offset_s: int = 0) -> str:
    """Broker server-time epoch ms → 'YYYY-MM-DD HH:MM WIB'. True UTC = server -
    offset (Trap 7); WIB = UTC+7. `None` (e.g. an open trade's close time) → "—"."""
    if server_msc is None:
        return "—"
    true_utc_s = server_msc / 1000 - offset_s
    dt = datetime.fromtimestamp(true_utc_s, tz=timezone.utc).astimezone(_WIB)
    return dt.strftime("%Y-%m-%d %H:%M WIB")


def dur(duration_s: int | None) -> str:
    """Human duration, mirroring `render/chart.py:_human_duration`. `None` → "—"."""
    if duration_s is None:
        return "—"
    if duration_s < 60:
        return f"{duration_s}s"
    m, s = divmod(duration_s, 60)
    if m < 60:
        return f"{m}m{s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def price(x: float | None) -> str:
    """A price (SL/TP/entry/exit). `None` = unknown → "unknown" (rule 4: never 0).
    A real 0.0 is a confirmed 'none set' and is shown as-is."""
    if x is None:
        return "unknown"
    return f"{x:g}"


def server_offset_s(conn: sqlite3.Connection, login: int) -> int:
    """The MEASURED server↔UTC offset from `sync_state` (Trap 7) — same read as
    `render/chart.py:_server_offset_s`. Falls back to 0 only when nothing was
    ever measured (fresh DB), never as an assumption over real data."""
    row = conn.execute(
        "SELECT server_utc_offset_s FROM sync_state "
        "WHERE account_login = ? AND server_utc_offset_s IS NOT NULL "
        "ORDER BY measured_at DESC LIMIT 1",
        (login,),
    ).fetchone()
    return int(row[0]) if row is not None else 0
