"""Pure-DB live-monitor store: heartbeat, watch registry, and the single forming
bar per (symbol, timeframe). NO bridge, NO MT5 — safe to import from web/. The
bridge-touching fetch lives in ingest/live.py, exactly like candles_store vs
candle_fill.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import Candle

_MSC_FLOOR = 10**12  # below this, time_msc is seconds leaking through (Trap 15)


def beat(conn: sqlite3.Connection, now_msc: int) -> None:
    """Overwrite the single heartbeat row. Caller need not commit — we do."""
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET beat_msc = excluded.beat_msc",
        (now_msc,),
    )
    conn.commit()


def read_heartbeat(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT beat_msc FROM live_heartbeat WHERE id = 1").fetchone()
    return None if row is None else int(row["beat_msc"])


def upsert_watch(conn: sqlite3.Connection, symbol: str, timeframe: str,
                 now_msc: int, ttl_ms: int) -> None:
    conn.execute(
        "INSERT INTO live_watches (symbol, timeframe, expires_msc, requested_msc) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(symbol, timeframe) DO UPDATE SET "
        "expires_msc = excluded.expires_msc, requested_msc = excluded.requested_msc",
        (symbol, timeframe, now_msc + ttl_ms, now_msc),
    )
    conn.commit()


def active_watches(conn: sqlite3.Connection, now_msc: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT symbol, timeframe FROM live_watches WHERE expires_msc > ? "
        "ORDER BY symbol, timeframe",
        (now_msc,),
    ).fetchall()
    return [(r["symbol"], r["timeframe"]) for r in rows]


def prune_expired(conn: sqlite3.Connection, now_msc: int) -> int:
    cur = conn.execute("DELETE FROM live_watches WHERE expires_msc <= ?", (now_msc,))
    conn.commit()
    return cur.rowcount
