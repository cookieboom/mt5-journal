"""app_prefs — single-value application preferences, pure DB. The web reads and
writes chart settings here so they survive across browsers. NOT a chart cache
and NOT derived from raw, so `journal rebuild` never touches it. No MT5 adapter
import — the M9 boundary holds here too (CLAUDE.md rules 1, 12).

Values are opaque JSON text owned by the client; this module does not validate
the shape. The chart convenience wrappers only json.dumps/loads around the
generic key/value core."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import now_ms

CHART_KEY = "chart"
REPLAY_KEY = "replay"
TRADE_PNG_KEY = "trade_png"
RISK_KEY = "risk_sizing"


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
