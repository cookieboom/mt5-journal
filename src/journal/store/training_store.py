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

The summary aggregator itself lives in domain/sim_stats.py and is shared with
paper trading.
"""
from __future__ import annotations

import sqlite3

from ..domain.sim_stats import summary as _summary
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
