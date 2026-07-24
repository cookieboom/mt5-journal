"""The candle_requests queue — pure DB. The web INSERTs (request_candles);
`journal live` drains (claim/fulfil/mark). Idempotent and retry-safe: an
orphaned 'claimed' row is re-queued, never failed (candles carry no money).
"""
from __future__ import annotations

import sqlite3

from .db import now_ms
from . import candles_store as cs


def request_candles(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int) -> int:
    """Enqueue a fill unless the range is already covered or an identical request
    is already pending/claimed. Returns 0 when nothing was queued (already
    covered), else the (new or existing) request id."""
    if not cs.missing_ranges(cs.read_coverage(conn, symbol, timeframe), (from_ms, to_ms)):
        return 0
    row = conn.execute(
        "SELECT id FROM candle_requests WHERE symbol = ? AND timeframe = ? "
        "AND from_msc = ? AND to_msc = ? AND status IN ('pending', 'claimed') LIMIT 1",
        (symbol, timeframe, from_ms, to_ms),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO candle_requests (symbol, timeframe, from_msc, to_msc, status, requested_msc) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (symbol, timeframe, from_ms, to_ms, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def claim_next_request(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Take the oldest pending request. The `WHERE status='pending'` + rowcount
    check is the lock (same shape as execute.claim_next)."""
    row = conn.execute(
        "SELECT id FROM candle_requests WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE candle_requests SET status = 'claimed', claimed_msc = ? WHERE id = ? AND status = 'pending'",
        (now_ms(), row["id"]),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    return conn.execute("SELECT * FROM candle_requests WHERE id = ?", (row["id"],)).fetchone()


def mark_done(conn: sqlite3.Connection, req_id: int, bars: int) -> None:
    conn.execute(
        "UPDATE candle_requests SET status = 'done', completed_msc = ?, bars_written = ? WHERE id = ?",
        (now_ms(), bars, req_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, req_id: int, error: str) -> None:
    conn.execute(
        "UPDATE candle_requests SET status = 'failed', completed_msc = ?, error = ? WHERE id = ?",
        (now_ms(), error, req_id),
    )
    conn.commit()


def requeue_orphaned(conn: sqlite3.Connection) -> int:
    """On `journal live` startup, reset any 'claimed' row (a crash mid-fetch) back
    to 'pending'. Safe because refetching candles is idempotent — the opposite of
    trade_commands, which must NEVER auto-retry."""
    cur = conn.execute(
        "UPDATE candle_requests SET status = 'pending', claimed_msc = NULL WHERE status = 'claimed'"
    )
    conn.commit()
    return cur.rowcount
