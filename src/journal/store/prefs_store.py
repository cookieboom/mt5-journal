"""app_prefs — single-value application preferences and small per-key JSON blobs
(chart settings, replay config, risk sizing, chart drawings), pure DB. The web
reads and writes chart settings here so they survive across browsers. NOT a chart
cache and NOT derived from raw, so `journal rebuild` never touches it. No MT5
adapter import — the M9 boundary holds here too (CLAUDE.md rules 1, 12).

Values are opaque JSON text owned by the client; this module does not validate
the shape. The chart convenience wrappers only json.dumps/loads around the
generic key/value core."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import now_ms
from ..domain.symbols import to_base

CHART_KEY = "chart"
REPLAY_KEY = "replay"
TRADE_PNG_KEY = "trade_png"
RISK_KEY = "risk_sizing"
PAPER_KEY = "paper"


def get_pref(conn: sqlite3.Connection, key: str) -> str | None:
    """Raw JSON text stored under `key`, or None if absent."""
    row = conn.execute("SELECT value FROM app_prefs WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_pref(conn: sqlite3.Connection, key: str, value: str,
             updated_ms: int | None = None) -> int:
    """Upsert `value` (raw JSON text) under `key`. Returns the updated_ms stamp."""
    ts = now_ms() if updated_ms is None else updated_ms
    conn.execute(
        "INSERT INTO app_prefs (key, value, updated_ms) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_ms = excluded.updated_ms",
        (key, value, ts),
    )
    conn.commit()
    return ts


def get_chart_prefs(conn: sqlite3.Connection) -> Any | None:
    """Parsed chart settings JSON, or None if never saved."""
    raw = get_pref(conn, CHART_KEY)
    return json.loads(raw) if raw is not None else None


def set_chart_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Persist chart settings (serialised to JSON). Returns the updated_ms stamp."""
    return set_pref(conn, CHART_KEY, json.dumps(prefs), now_ms())


def get_replay_prefs(conn: sqlite3.Connection) -> Any | None:
    """Parsed replay-config prefs JSON, or None if never saved."""
    raw = get_pref(conn, REPLAY_KEY)
    return json.loads(raw) if raw is not None else None


def set_replay_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Persist replay-config prefs (serialised to JSON). Returns updated_ms."""
    return set_pref(conn, REPLAY_KEY, json.dumps(prefs), now_ms())


def get_risk_prefs(conn: sqlite3.Connection) -> Any | None:
    """Risk-sizing panel prefs (mode + value), or None if never saved."""
    raw = get_pref(conn, RISK_KEY)
    return None if raw is None else json.loads(raw)


def set_risk_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Upsert the risk-sizing prefs blob. Returns the updated_ms stamp."""
    return set_pref(conn, RISK_KEY, json.dumps(prefs), now_ms())


def get_trade_png_prefs(conn: sqlite3.Connection) -> Any | None:
    """Parsed trade-PNG render settings JSON, or None if never saved."""
    raw = get_pref(conn, TRADE_PNG_KEY)
    return json.loads(raw) if raw is not None else None


def set_trade_png_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Persist trade-PNG render settings (serialised to JSON). Returns updated_ms."""
    return set_pref(conn, TRADE_PNG_KEY, json.dumps(prefs), now_ms())


def get_paper_prefs(conn: sqlite3.Connection) -> Any | None:
    """Paper-trading UI state: `{mode: 'real'|'paper', accountId: int|null}`.
    Its OWN key, not folded into the chart blob — `ChartSettings` is versioned
    and carries a legacy-object migration, and which paper account is selected is
    not chart appearance."""
    raw = get_pref(conn, PAPER_KEY)
    return None if raw is None else json.loads(raw)


def set_paper_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Upsert the paper-trading UI prefs blob. Returns the updated_ms stamp."""
    return set_pref(conn, PAPER_KEY, json.dumps(prefs), now_ms())


DRAWINGS_PREFIX = "drawings"


def drawings_key(symbol: str, session_id: int | None) -> str:
    """Storage key for a chart's hand-drawn annotations.

    Live/normal chart: `drawings:<symbol_base>` — grouped by base, never by the
    raw broker symbol (rule 11), so XAUUSDc and a future XAUUSD share a level.

    Replay: `drawings:replay:<session_id>`. A replay session is symbol-bound by
    construction, so the id alone identifies it — and keeping it off the live
    key is the point: live drawings were made knowing what happened next, and
    showing them during training would leak the answer.
    """
    if session_id is not None:
        return f"{DRAWINGS_PREFIX}:replay:{int(session_id)}"
    return f"{DRAWINGS_PREFIX}:{to_base(symbol)}"


def get_drawings(conn: sqlite3.Connection, symbol: str,
                 session_id: int | None = None) -> Any | None:
    """Parsed drawings blob for this symbol/session, or None if never saved."""
    raw = get_pref(conn, drawings_key(symbol, session_id))
    return json.loads(raw) if raw is not None else None


def set_drawings(conn: sqlite3.Connection, symbol: str,
                 session_id: int | None, blob: Any) -> int:
    """Persist the drawings blob verbatim (the client owns its schema, exactly
    like the other prefs wrappers). Returns the updated_ms stamp."""
    return set_pref(conn, drawings_key(symbol, session_id), json.dumps(blob), now_ms())
