"""The `trade_commands` queue (M9) — how an intent gets from the web to the bridge.

The web INSERTs a `pending` row here and never talks to MT5; `journal live`
claims it, sends it, and writes the outcome back. That split is what keeps
CLAUDE.md rules 1 and 12 literally true inside `web/`, and it means a web process
that dies can never leave a half-sent order: the intent either committed or it
did not.

This module owns the DB side only. Every rule about whether a command is
ALLOWED lives in `domain/commands.py`, pure and unit-tested; this file reads the
rows those rules need and writes what happened.

Two invariants worth stating out loud, because both are about real money:

  * **A command can be claimed exactly once.** `claim_next` conditions its UPDATE
    on the row still being `pending` and checks `rowcount`; that is the lock. Two
    claims of one row means one order sent twice.
  * **The intent columns are write-once.** `record_result` touches only the
    lifecycle and result columns. If it could rewrite `kind`/`sl`/`tp`/`volume`,
    the log of what was ASKED FOR would silently become a log of what happened —
    and the two differing is precisely what you open the log to discover.
"""

from __future__ import annotations

import json
import sqlite3

from .adapter.base import TradeResult
from .domain.commands import CommandError, classify, validate
from .store.db import now_ms


def _position(conn: sqlite3.Connection, login: int, position_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM open_positions WHERE account_login = ? AND position_id = ?",
        (login, position_id),
    ).fetchone()


def _spec(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()


def load_context(
    conn: sqlite3.Connection, login: int, position_id: int
) -> tuple[sqlite3.Row, sqlite3.Row]:
    """The (position, spec) pair every command needs, or `CommandError`.

    Shared by `enqueue` (validate before queueing) and the executor (re-validate
    at claim time, because the world moves in between).
    """
    pos = _position(conn, login, position_id)
    if pos is None:
        raise CommandError(
            f"Posisi {position_id} tidak ada di daftar posisi terbuka. "
            f"Mungkin sudah tertutup, atau `journal live` belum pernah jalan."
        )
    spec = _spec(conn, pos["symbol"])
    if spec is None:
        raise CommandError(
            f"Spesifikasi simbol {pos['symbol']} belum ada di database — "
            f"jalankan `journal sync` dulu."
        )
    return pos, spec


def enqueue(
    conn: sqlite3.Connection,
    login: int,
    kind: str,
    position_id: int,
    *,
    sl: float | None = None,
    tp: float | None = None,
    volume: float | None = None,
) -> int:
    """Validate, then queue. Returns the new command id.

    A refused command raises and writes NOTHING — the human finds out
    immediately, and the table does not fill with entries nobody will ever run.
    The `rejected` STATUS is for the different case where a command passed here
    but no longer passes when the executor picks it up (see `reject`).
    """
    pos, spec = load_context(conn, login, position_id)
    validate(kind, pos, spec, sl=sl, tp=tp, volume=volume)

    cur = conn.execute(
        "INSERT INTO trade_commands "
        "(account_login, position_id, kind, sl, tp, volume, requested_msc, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
        # sl/tp go in exactly as given: a None stays NULL and a 0.0 stays 0.0.
        # The rule-4 chain is only as strong as its weakest link, and the DB is
        # a link — coercing here would clear a stop-loss two phases later.
        (login, position_id, kind, sl, tp, volume, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def claim_next(conn: sqlite3.Connection, login: int) -> sqlite3.Row | None:
    """Take ownership of the oldest pending command, or return None.

    The UPDATE's `WHERE status = 'pending'` plus the `rowcount` check IS the
    lock — a second caller racing for the same row updates 0 rows and gets None.
    Losing that check means sending one order twice.
    """
    row = conn.execute(
        "SELECT id FROM trade_commands "
        "WHERE account_login = ? AND status = 'pending' ORDER BY id LIMIT 1",
        (login,),
    ).fetchone()
    if row is None:
        return None

    cur = conn.execute(
        "UPDATE trade_commands SET status = 'claimed', claimed_msc = ? "
        "WHERE id = ? AND status = 'pending'",
        (now_ms(), row["id"]),
    )
    if cur.rowcount != 1:
        conn.commit()
        return None      # someone else got there first
    conn.commit()
    return get_command(conn, int(row["id"]))


def mark_sent(conn: sqlite3.Connection, cmd_id: int) -> None:
    """Record that the request is about to leave for the broker.

    Committed BEFORE `order_send` is called, deliberately: if the process dies
    mid-flight, this row is the only evidence that an order may exist at the
    broker, and `recover_interrupted` needs it to say so rather than quietly
    re-queueing.
    """
    conn.execute(
        "UPDATE trade_commands SET status = 'sent' WHERE id = ?", (cmd_id,)
    )
    conn.commit()


def record_result(conn: sqlite3.Connection, cmd_id: int, result: TradeResult) -> str:
    """Write what the broker said. Returns the resulting status.

    Touches ONLY the lifecycle and result columns — never the intent.
    `result_volume` is the ACTUAL fill, which on a DONE_PARTIAL is not what was
    requested; the requested volume stays untouched beside it so the difference
    is visible.
    """
    status = classify(result.retcode)
    error = None
    if result.retcode is None:
        # Not proof of failure: the request may well have reached the broker.
        # The status has to be something, and 'failed' is the safe reading —
        # but the text is what stops a human assuming nothing happened.
        error = (
            "bridge tidak mengembalikan hasil — status order TIDAK DIKETAHUI. "
            "Cek MT5 sebelum mengirim ulang."
        )

    conn.execute(
        "UPDATE trade_commands SET status = ?, completed_msc = ?, retcode = ?, "
        "result_deal = ?, result_order = ?, result_volume = ?, result_price = ?, "
        "broker_comment = ?, error = ?, raw_json = ? "
        "WHERE id = ?",
        (
            status, now_ms(),
            int(result.retcode) if result.retcode is not None else None,
            result.deal, result.order, result.volume, result.price,
            result.comment, error,
            json.dumps(result.raw, default=str) if result.raw else None,
            cmd_id,
        ),
    )
    conn.commit()
    return status


def reject(conn: sqlite3.Connection, cmd_id: int, reason: str) -> None:
    """Refuse a claimed command WITHOUT sending it.

    For a command that was valid when queued but is not when the executor picks
    it up — the position closed in the meantime, the spec changed, the symbol
    went close-only. `retcode` stays NULL because the broker never saw it.
    """
    conn.execute(
        "UPDATE trade_commands SET status = 'rejected', completed_msc = ?, error = ? "
        "WHERE id = ?",
        (now_ms(), reason, cmd_id),
    )
    conn.commit()


def recover_interrupted(conn: sqlite3.Connection, login: int) -> int:
    """Deal with commands orphaned by a crash. Returns how many were closed out.

    A `claimed` or `sent` row on startup means the process died mid-command. A
    `sent` one may ALREADY EXIST at the broker, so neither is re-queued: both are
    marked failed with an error telling the human to look in MT5 themselves.

    This is the single most important refusal in M9. Re-sending an order that
    might already have been filled is how one intended trade becomes two real
    positions, and no amount of cleverness here can distinguish "never arrived"
    from "arrived and the answer was lost".
    """
    cur = conn.execute(
        "UPDATE trade_commands SET status = 'failed', completed_msc = ?, error = ? "
        "WHERE account_login = ? AND status IN ('claimed','sent')",
        (
            now_ms(),
            "proses berhenti di tengah perintah — status TIDAK DIKETAHUI. "
            "Perintah ini TIDAK diulang otomatis. Cek posisinya di MT5 dulu "
            "sebelum mengirim ulang.",
            login,
        ),
    )
    conn.commit()
    return cur.rowcount


def get_command(conn: sqlite3.Connection, cmd_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM trade_commands WHERE id = ?", (cmd_id,)
    ).fetchone()


def list_commands(
    conn: sqlite3.Connection, login: int, limit: int = 50
) -> list[sqlite3.Row]:
    """Recent commands, newest first — the audit log the UI shows."""
    return conn.execute(
        "SELECT * FROM trade_commands WHERE account_login = ? "
        "ORDER BY id DESC LIMIT ?",
        (login, limit),
    ).fetchall()


def pending_count(conn: sqlite3.Connection, login: int) -> int:
    """Used by `journal live` to decide whether to poll fast or idle."""
    return int(
        conn.execute(
            "SELECT count(*) FROM trade_commands "
            "WHERE account_login = ? AND status = 'pending'",
            (login,),
        ).fetchone()[0]
    )


__all__ = [
    "CommandError",
    "claim_next",
    "enqueue",
    "get_command",
    "list_commands",
    "load_context",
    "mark_sent",
    "pending_count",
    "record_result",
    "recover_interrupted",
    "reject",
]
