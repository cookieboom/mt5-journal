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
from .store import live_store
from .store.db import now_ms

# How old the evidence that the price feed is moving may be before an open is
# refused. Matches `api.live_status_payload`'s own staleness window, so the dot
# the human sees on `/live` and this refusal flip at the same moment.
#
# Mirrored in `frontend/src/lib/candles.ts` (the button has to disarm before the
# click); `tests/test_frontend_constants.py` fails if the two drift.
FEED_STALE_MS = 15_000

# How far `price_ref` may sit from the price the server last saw, as a fraction
# of the stop distance the lot was derived from. A quarter keeps the realised
# risk within 25% of the intended risk; loosen it and a frozen browser tab can
# still stake a multiple of what the human read off the screen. Calibration
# knob: raise it if normal tick-to-tick drift on a tight stop starts refusing.
# Mirrored in `frontend/src/lib/candles.ts`, pinned by the same test as above.
PRICE_REF_STOP_FRACTION = 0.25

# How long a queued command may wait for an executor before `journal live`
# refuses it instead of sending it. `journal live` claims within a cycle or two
# (1–5 s), so anything approaching this means nothing was executing at all —
# and by then every number the command was validated against is stale. Five
# minutes is deliberately longer than a `journal live` restart, which is the one
# routine reason a fresh row waits: a restart must not cost the human the
# command they just queued. Calibration knob — see `expire_stale`.
STALE_PENDING_S = 300.0


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


def account_balance(conn: sqlite3.Connection, login: int) -> float | None:
    """`accounts.balance` in account currency (USC), or None if unknown.

    A SNAPSHOT from the last `journal sync`, not a live figure — which is why
    the risk ceiling it feeds is a hard 5% rather than a knife-edge limit.
    """
    row = conn.execute(
        "SELECT balance FROM accounts WHERE login = ?", (login,)
    ).fetchone()
    return None if row is None or row["balance"] is None else float(row["balance"])


def load_open_context(
    conn: sqlite3.Connection, login: int, symbol: str, direction: str, price: float,
) -> tuple[dict, sqlite3.Row]:
    """The (position, spec) pair for an OPEN, where no position exists yet.

    The position is synthesised from what the human chose, in exactly the shape
    `domain/commands` reads off a real `open_positions` row — which is why the
    open path needs no new branch inside `_check_trade_mode`, `_check_volume`,
    or `_check_level`. Same rules, same messages, one code path.
    """
    spec = _spec(conn, symbol)
    if spec is None:
        raise CommandError(
            f"Spesifikasi simbol {symbol} belum ada di database — "
            f"jalankan `journal sync` dulu."
        )
    pos = {
        "position_id": None,
        "symbol": symbol,
        "direction": direction,
        "price_current": price,
        "volume": None,
        "sl": 0.0,
        "tp": 0.0,
    }
    return pos, spec


def _check_feed_fresh(
    conn: sqlite3.Connection, symbol: str, price_ref: float | None = None,
    sl: float | None = None, *, now_msc: int | None = None,
) -> None:
    """Refuse an open whose reference price the server cannot vouch for.

    `price_ref` is what the lot was derived from, so a stale one does not fail
    loudly — it silently resizes the order. 0.10 lot against a 4035 close with a
    4030 stop is 50 USC of intended risk; if the market really sits at 4060, the
    same command stakes ~300 USC. `_check_level`'s fresh-tick re-validation in
    `journal live` catches a stop on the wrong SIDE, never a wrong SIZE, because
    the volume is frozen at enqueue by design — so the guard has to be here.

    Three independent ways the reference price can be untrustworthy, and all
    three refuse:

      * `journal live` is not beating at all — nothing is pulling prices.
      * It is beating, but an ACTIVELY WATCHED forming bar for this symbol has
        not been refreshed. The process is up; this symbol is not being served
        through it. (A bucket with no ticks is NOT that case — `serve_watches`
        stamps `updated_msc` whenever the bridge answers, precisely so a quiet
        session does not read as a dead feed.) No active watch means we have no
        evidence either way, and an open from `/live` (which mounts no chart) is
        exactly that case — allowed, with the heartbeat as the only gate.
      * The feed is moving but `price_ref` did not come from it. A moving feed
        proves the SERVER sees prices; it says nothing about the number the
        browser posted. A wedged `/api/candles` fetch leaves `mergeForming`
        painting the last bar it has while `staleEntryReason` (2 x timeframe —
        30 minutes at M15) still arms the button, and the lot is then sized off
        a half-hour-old price. Only comparing the two numbers closes that.

    `lib/candles.staleEntryReason` gates the button in the browser on the same
    facts. This is the copy that guards the row actually being written.
    """
    now = now_ms() if now_msc is None else now_msc

    beat = live_store.read_heartbeat(conn)
    if beat is None or now - beat >= FEED_STALE_MS:
        raise CommandError(
            "`journal live` tidak berjalan (heartbeat "
            f"{'tidak ada' if beat is None else f'basi {(now - beat) / 1000:.0f}s'}) "
            "— harga acuan tidak segar, jadi ukuran lot tidak bisa dipercaya. "
            "Jalankan `journal live` dulu."
        )

    live = live_store.newest_forming(conn, symbol, now)
    if live is None:
        return
    updated, close = live
    if now - updated >= FEED_STALE_MS:
        raise CommandError(
            f"Feed {symbol} beku — bar berjalan terakhir diperbarui "
            f"{(now - updated) / 1000:.0f}s lalu. Harga acuan tidak segar, "
            "jadi ukuran lot tidak bisa dipercaya."
        )

    if price_ref is None:
        return
    # The lot is `risk / stop distance`, so the same drift matters exactly in
    # proportion to that distance: 0.5 off a 5.0 stop is a tenth of the intended
    # risk, off a 0.5 stop it is all of it. `validate` refuses an SL-less open
    # before this runs, so a zero distance here would refuse everything — which
    # is the safe direction anyway.
    tolerance = abs(price_ref - (sl or 0.0)) * PRICE_REF_STOP_FRACTION
    if abs(price_ref - close) > tolerance:
        raise CommandError(
            f"Harga acuan {price_ref:g} tidak cocok dengan harga {symbol} yang "
            f"terakhir dilihat server ({close:g}, selisih "
            f"{abs(price_ref - close):g} > toleransi {tolerance:g}). "
            "Chart di browser kemungkinan tertinggal — muat ulang halaman "
            "sebelum membuka posisi."
        )


def enqueue_open(
    conn: sqlite3.Connection,
    login: int,
    *,
    symbol: str,
    direction: str,
    sl: float | None,
    tp: float | None,
    volume: float | None,
    price_ref: float | None,
) -> int:
    """Validate, then queue an open. Returns the new command id.

    A refused open writes NOTHING, exactly as `enqueue` does. `price_ref` is
    stored as evidence of the price the human sized against — it is NOT sent to
    the broker (execution is MARKET) and it is not the fill price.
    """
    pos, spec = load_open_context(conn, login, symbol, direction, price_ref)
    validate("open", pos, spec, sl=sl, tp=tp, volume=volume,
             balance=account_balance(conn, login))
    # LAST, so an unknown symbol or a missing spec reports itself instead of
    # being masked by "journal live tidak berjalan" — and so the stop distance
    # the price-ref tolerance is measured against is already known to be real.
    _check_feed_fresh(conn, symbol, price_ref, sl)

    cur = conn.execute(
        "INSERT INTO trade_commands "
        "(account_login, position_id, kind, symbol, direction, price_ref, "
        " sl, tp, volume, requested_msc, status) "
        "VALUES (?, NULL, 'open', ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (login, symbol, direction, price_ref, sl, tp, volume, now_ms()),
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


def expire_stale(
    conn: sqlite3.Connection, login: int, max_age_s: float = STALE_PENDING_S
) -> int:
    """Refuse `pending` commands nobody claimed in time. Returns how many.

    A pending row is a promise the UI keeps making: `/live` shows it queued and
    the human reads that as "the SL is on its way". Nothing here ever retracted
    it. With `journal live` down — or up with `--no-trading` — the row simply sat
    there, and the two ways that ends are both bad: the human walks away
    believing a stop is attached to a real position, or `journal live` starts
    hours later and sends an order whose `price_ref`, whose stop distance and
    whose `_check_feed_fresh` verdict were all measured against a market that no
    longer exists. `enqueue` validates ONCE, at queue time; that verdict has a
    shelf life and this is it.

    Only `pending`. `claimed`/`sent` belong to `recover_interrupted` and the
    distinction is the whole point — a `sent` row may already exist at the
    broker, so closing it out on a timer would invent an outcome for a real
    order. A pending one provably never left.

    Refusing is also what un-sticks the daily snapshot: `_maybe_backup` steps
    aside for anything pending, so before this, one forgotten row deferred every
    backup for as long as it sat there.
    """
    now = now_ms()
    cutoff = now - int(max_age_s * 1000)
    # Count first, write only if there is something to write. This runs every
    # `journal live` cycle and the answer is almost always zero; an UPDATE that
    # matches no rows still takes the WAL writer slot, and this project has
    # twice paid for holding that slot for no reason (`deals.sync`,
    # `candle_fill.fill_range`).
    (stale,) = conn.execute(
        "SELECT COUNT(*) FROM trade_commands "
        "WHERE account_login = ? AND status = 'pending' AND requested_msc < ?",
        (login, cutoff),
    ).fetchone()
    if not stale:
        return 0

    cur = conn.execute(
        "UPDATE trade_commands SET status = 'rejected', completed_msc = ?, error = ? "
        "WHERE account_login = ? AND status = 'pending' AND requested_msc < ?",
        (
            now,
            f"kedaluwarsa — antre >{max_age_s / 60:.0f} menit tanpa ada yang "
            f"mengirim (`journal live` mati, atau jalan dengan --no-trading). "
            f"TIDAK dikirim: harga acuan dan jarak stop-nya sudah basi. "
            f"Kirim ulang dari /live kalau masih mau.",
            login,
            cutoff,
        ),
    )
    conn.commit()
    return cur.rowcount


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
    "account_balance",
    "claim_next",
    "enqueue",
    "enqueue_open",
    "expire_stale",
    "get_command",
    "list_commands",
    "load_context",
    "load_open_context",
    "mark_sent",
    "pending_count",
    "record_result",
    "recover_interrupted",
    "reject",
]
