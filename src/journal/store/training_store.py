"""training_store — pure DB access for Chart Phase D. Sessions and fake positions
live here and NOWHERE near `trades`/raw (CLAUDE.md rule 2). No MT5 adapter import
(M9 boundary, rules 1/12). Money is USC; R is unit-free (rule 4).

Training summaries are NOT §8-gated (unlike analytics/report, which still is).
A replay session — and a competitive scenario in particular — is a handful of
trades, so a 20-sample floor blanked every rate/average all the time and the
panel carried no information at all. Every metric is reported with its own `n`
alongside; the reader judges the sample size.

Only CLOSED positions with a non-null `net_profit` count toward a summary — an
`eod` (unresolved) or never-filled position is excluded (unknown outcome, rule 4).
"""
from __future__ import annotations

import sqlite3

from .db import now_ms


def create_session(conn: sqlite3.Connection, *, symbol: str, symbol_base: str,
                   timeframe: str, range_start_msc: int, range_end_msc: int,
                   cursor_msc: int) -> int:
    cur = conn.execute(
        "INSERT INTO training_sessions "
        "(symbol, symbol_base, timeframe, range_start_msc, range_end_msc, "
        " cursor_msc, status, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
        (symbol, symbol_base, timeframe, range_start_msc, range_end_msc,
         cursor_msc, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM training_sessions WHERE id = ?", (session_id,)
    ).fetchone()


def list_sessions(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute(
            "SELECT * FROM training_sessions ORDER BY id DESC"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM training_sessions WHERE status = ? ORDER BY id DESC",
        (status,),
    ).fetchall()


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute("DELETE FROM training_sessions WHERE id = ?", (session_id,))
    conn.commit()


def update_cursor(conn: sqlite3.Connection, session_id: int, cursor_msc: int) -> None:
    conn.execute(
        "UPDATE training_sessions SET cursor_msc = ? WHERE id = ?",
        (cursor_msc, session_id),
    )
    conn.commit()


def set_session_status(conn: sqlite3.Connection, session_id: int, status: str) -> None:
    conn.execute(
        "UPDATE training_sessions SET status = ? WHERE id = ?", (status, session_id)
    )
    conn.commit()


def insert_position(conn: sqlite3.Connection, *, session_id: int, direction: str,
                    volume: float, decision_msc: int, sl: float, tp: float) -> int:
    cur = conn.execute(
        "INSERT INTO training_positions "
        "(session_id, direction, volume, decision_msc, sl, tp, status, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (session_id, direction, volume, decision_msc, sl, tp, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_positions(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM training_positions WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()


def active_positions(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM training_positions WHERE session_id = ? "
        "AND status IN ('pending','open') ORDER BY id",
        (session_id,),
    ).fetchall()


def get_position(conn: sqlite3.Connection, position_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM training_positions WHERE id = ?", (position_id,)
    ).fetchone()


def mark_fill(conn: sqlite3.Connection, position_id: int, *, entry_msc: int,
              entry_price: float) -> None:
    conn.execute(
        "UPDATE training_positions SET status = 'open', entry_msc = ?, "
        "entry_price = ? WHERE id = ?",
        (entry_msc, entry_price, position_id),
    )
    conn.commit()


def request_close(conn: sqlite3.Connection, position_id: int,
                  close_requested_msc: int) -> None:
    conn.execute(
        "UPDATE training_positions SET close_requested_msc = ? "
        "WHERE id = ? AND status = 'open'",
        (close_requested_msc, position_id),
    )
    conn.commit()


def mark_close(conn: sqlite3.Connection, position_id: int, *, exit_msc: int,
               exit_price: float | None, exit_reason: str,
               net_profit: float | None, r_multiple: float | None,
               mae: float | None, mfe: float | None,
               mae_r: float | None, mfe_r: float | None) -> None:
    conn.execute(
        "UPDATE training_positions SET status = 'closed', exit_msc = ?, "
        "exit_price = ?, exit_reason = ?, net_profit = ?, r_multiple = ?, "
        "mae = ?, mfe = ?, mae_r = ?, mfe_r = ? WHERE id = ?",
        (exit_msc, exit_price, exit_reason, net_profit, r_multiple,
         mae, mfe, mae_r, mfe_r, position_id),
    )
    conn.commit()


def _summary(rows: list[sqlite3.Row]) -> dict:
    """Aggregate CLOSED, resolved (non-null net_profit) positions. Ungated: a
    metric is null only when it has NO input (rule 4 — unknown, not zero)."""
    resolved = [r for r in rows if r["net_profit"] is not None]
    n = len(resolved)
    r_vals = [r["r_multiple"] for r in resolved if r["r_multiple"] is not None]
    mae_vals = [r["mae_r"] for r in resolved if r["mae_r"] is not None]
    mfe_vals = [r["mfe_r"] for r in resolved if r["mfe_r"] is not None]
    total_r = sum(r_vals)
    wins = sum(1 for r in resolved if r["net_profit"] > 0)
    return {
        "n": n,
        "win_rate": (wins / n) if n else None,
        "avg_r": (total_r / len(r_vals)) if r_vals else None,
        "total_r": total_r,
        "avg_mae_r": (sum(mae_vals) / len(mae_vals)) if mae_vals else None,
        "avg_mfe_r": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
    }


def session_summary(conn: sqlite3.Connection, session_id: int) -> dict:
    return _summary(list(conn.execute(
        "SELECT net_profit, r_multiple, mae_r, mfe_r FROM training_positions "
        "WHERE session_id = ? AND status = 'closed'", (session_id,),
    ).fetchall()))


def career_summary(conn: sqlite3.Connection) -> dict:
    return _summary(list(conn.execute(
        "SELECT net_profit, r_multiple, mae_r, mfe_r FROM training_positions "
        "WHERE status = 'closed'"
    ).fetchall()))
