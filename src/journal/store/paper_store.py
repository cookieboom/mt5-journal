"""paper_store — pure DB access for the paper-trading tables. No MT5 adapter
import (rules 1/12), no evaluation logic (that is `domain/paper_eval`), and
nothing near `trades`/raw (rule 2). Money is USC.

`split_for_partial` is the one function with a real invariant: a partial close
inserts a child row carrying the parent's entry and reduces the parent's volume,
so every closed row is a complete trade record.
"""
from __future__ import annotations

import sqlite3

from .db import now_ms


def create_account(conn: sqlite3.Connection, *, name: str, initial_balance: float,
                   leverage: int, stopout_pct: float) -> int:
    try:
        cur = conn.execute(
            "INSERT INTO paper_accounts "
            "(name, initial_balance, balance, leverage, stopout_pct, status, "
            " created_at_msc) VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (name, initial_balance, initial_balance, leverage, stopout_pct, now_ms()),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Nama akun '{name}' sudah dipakai.") from e
    conn.commit()
    return int(cur.lastrowid)


def get_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
    ).fetchone()


def list_accounts(conn: sqlite3.Connection,
                  status: str | None = None) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute("SELECT * FROM paper_accounts ORDER BY id DESC").fetchall()
    return conn.execute(
        "SELECT * FROM paper_accounts WHERE status = ? ORDER BY id DESC", (status,)
    ).fetchall()


def archive_account(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "UPDATE paper_accounts SET status = 'archived', archived_at_msc = ? "
        "WHERE id = ?", (now_ms(), account_id),
    )
    conn.commit()


def add_balance(conn: sqlite3.Connection, account_id: int, delta: float) -> None:
    """Move the REALIZED balance by a signed amount, in USC."""
    conn.execute(
        "UPDATE paper_accounts SET balance = balance + ? WHERE id = ?",
        (delta, account_id),
    )
    conn.commit()


def insert_position(conn: sqlite3.Connection, *, account_id: int, symbol: str,
                    symbol_base: str, direction: str, order_kind: str,
                    request_price: float | None, volume: float, sl: float,
                    tp: float, status: str, entry_price: float | None,
                    entry_msc: int | None, expires_msc: int | None) -> int:
    ts = now_ms()
    cur = conn.execute(
        "INSERT INTO paper_positions "
        "(account_id, symbol, symbol_base, direction, order_kind, request_price, "
        " volume, sl, tp, sl_initial, expires_msc, status, requested_msc, "
        " entry_msc, entry_price, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, symbol, symbol_base, direction, order_kind, request_price,
         volume, sl, tp, (sl if status == "open" and sl > 0 else None),
         expires_msc, status, ts, entry_msc, entry_price, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_positions(conn: sqlite3.Connection, account_id: int,
                   statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
    if statuses is None:
        return conn.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? ORDER BY id",
            (account_id,),
        ).fetchall()
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT * FROM paper_positions WHERE account_id = ? "
        f"AND status IN ({marks}) ORDER BY id",
        (account_id, *statuses),
    ).fetchall()


def get_position(conn: sqlite3.Connection, position_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE id = ?", (position_id,)
    ).fetchone()


def open_or_pending_symbols(conn: sqlite3.Connection) -> list[str]:
    """Every symbol that some ACTIVE account still has live exposure on — the
    exact set the daemon needs a tick for. No exposure means no bridge call."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT p.symbol FROM paper_positions p "
        "JOIN paper_accounts a ON a.id = p.account_id "
        "WHERE p.status IN ('pending','open') AND a.status = 'active'"
    ).fetchall()]


def update_status(conn: sqlite3.Connection, position_id: int, status: str) -> None:
    conn.execute(
        "UPDATE paper_positions SET status = ? WHERE id = ?", (status, position_id)
    )
    conn.commit()


def mark_fill(conn: sqlite3.Connection, position_id: int, *, entry_msc: int,
              entry_price: float, sl_initial: float | None) -> None:
    """Open the position and record the stop it was born with. `sl_initial` is
    written here and nowhere else, which is what keeps R honest after a move."""
    conn.execute(
        "UPDATE paper_positions SET status = 'open', entry_msc = ?, "
        "entry_price = ?, sl_initial = ? WHERE id = ?",
        (entry_msc, entry_price, sl_initial, position_id),
    )
    conn.commit()


def set_sltp(conn: sqlite3.Connection, position_id: int, *, sl: float,
             tp: float) -> None:
    """Move the live stop/target. Never touches `sl_initial`."""
    conn.execute(
        "UPDATE paper_positions SET sl = ?, tp = ? WHERE id = ?",
        (sl, tp, position_id),
    )
    conn.commit()


def mark_close(conn: sqlite3.Connection, position_id: int, *, exit_msc: int,
               exit_price: float | None, exit_reason: str,
               net_profit: float | None, r_multiple: float | None,
               mae: float | None, mfe: float | None, mae_r: float | None,
               mfe_r: float | None) -> None:
    conn.execute(
        "UPDATE paper_positions SET status = 'closed', exit_msc = ?, "
        "exit_price = ?, exit_reason = ?, net_profit = ?, r_multiple = ?, "
        "mae = ?, mfe = ?, mae_r = ?, mfe_r = ? WHERE id = ?",
        (exit_msc, exit_price, exit_reason, net_profit, r_multiple,
         mae, mfe, mae_r, mfe_r, position_id),
    )
    conn.commit()


def split_for_partial(conn: sqlite3.Connection, position_id: int,
                      volume: float) -> int:
    """Carve `volume` off an open position into a new child row and return its id.

    The child inherits the parent's symbol, direction, entry price and entry
    time, so once the caller closes it, it is a complete trade record on its own.
    Refuses a slice that is not strictly smaller than what is held — closing the
    whole thing is `mark_close`, not a split.
    """
    parent = get_position(conn, position_id)
    if parent is None:
        raise ValueError(f"tidak ada posisi paper {position_id}")
    if parent["status"] != "open":
        raise ValueError("hanya posisi terbuka yang bisa ditutup sebagian")
    if volume >= parent["volume"] - 1e-9:
        raise ValueError(
            f"volume {volume} lebih besar atau sama dengan volume posisi "
            f"{parent['volume']} — pakai close penuh"
        )
    ts = now_ms()
    cur = conn.execute(
        "INSERT INTO paper_positions "
        "(account_id, symbol, symbol_base, direction, order_kind, request_price, "
        " volume, sl, tp, sl_initial, expires_msc, status, requested_msc, "
        " entry_msc, entry_price, parent_id, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'open', ?, ?, ?, ?, ?)",
        (parent["account_id"], parent["symbol"], parent["symbol_base"],
         parent["direction"], parent["order_kind"], parent["request_price"],
         volume, parent["sl"], parent["tp"], parent["sl_initial"],
         ts, parent["entry_msc"], parent["entry_price"], position_id, ts),
    )
    conn.execute(
        "UPDATE paper_positions SET volume = volume - ? WHERE id = ?",
        (volume, position_id),
    )
    conn.commit()
    return int(cur.lastrowid)
