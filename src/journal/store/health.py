"""What `journal status` asks. Read-only, no bridge, no writes.

Every silent failure this project has actually had already had a detector — in
a different command, run at a different moment: `integrity_check` only when a
backup is taken, the §6 identities only when someone types `journal verify`,
`backup.due()` only inside the `journal live` loop. Two of them had no command
at all (deals synced but never reconstructed; a `trade_commands` row queued
with nothing running to send it). Knowing WHICH question to ask is the part a
human gets wrong, so this module asks all of them at once.

It composes; it does not detect. Every number below comes from a function that
already ships, so `status` and the command that owns the check can never
disagree about the answer. A check that needs new logic belongs in the module
that owns it, not here.

Three states, and the difference between the last two is the whole design:

    ok     nothing to do
    warn   something is UNDONE — stale backup, unrebuilt trades, no daemon.
           Exit code stays 0. A status command that exits non-zero because
           nobody backed up today is one that gets `|| true`'d and ignored.
    fail   something is WRONG — the file is corrupt, or the money does not add
           up. Only these set the exit code.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..adapter.base import DealType
from . import backup

# A snapshot older than this is overdue — the same 24 h `journal live` uses for
# its own timer, deliberately shared so the two never disagree.
BACKUP_MAX_AGE_S = 24 * 3600
# `journal live` beats every cycle (seconds). A minute of silence means it is
# gone, not busy.
HEARTBEAT_MAX_AGE_S = 60.0


@dataclass(frozen=True)
class Check:
    name: str
    state: str            # "ok" | "warn" | "fail"
    detail: str           # always carries its numbers
    fix: str | None = None  # the command that resolves it


def _age(seconds: float) -> str:
    """Coarse on purpose: 'is this hours or days old' is the whole question."""
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400 * 2:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _integrity(conn: sqlite3.Connection) -> Check:
    """`quick_check` rather than `integrity_check`: it skips the (slow) index
    cross-checks and still catches a torn page, which is the failure that makes
    every other line of output fiction."""
    rows = conn.execute("PRAGMA quick_check(1)").fetchall()
    first = rows[0][0] if rows else "no result"
    if first == "ok":
        return Check("integrity", "ok", "quick_check ok")
    return Check("integrity", "fail", f"quick_check: {first}",
                 "journal backup --dest rescue.db   # copy it out BEFORE anything else")


def _balance(conn: sqlite3.Connection) -> Check:
    from ..ingest.deals import verify

    try:
        v = verify(conn)
    except RuntimeError as e:
        # No account row, or more than one. Neither is corruption — the store is
        # simply not synced yet, which is where every new journal starts.
        return Check("balance", "warn", str(e), "journal sync")

    if v.passed:
        state2 = "not run" if v.id2_state == "not_run" else "PASS"
        return Check("balance", "ok",
                     f"identity 1 PASS, identity 2 {state2} ({v.trades_count} trades)")
    if not v.passed1:
        return Check("balance", "fail",
                     f"identity 1 residual {v.residual:+.2f} — deals do not sum to "
                     f"the balance snapshot",
                     "journal verify")
    return Check("balance", "fail",
                 f"identity 1 holds, identity 2 does NOT — {v.trades_count} trades do "
                 f"not partition {v.trade_deals_count} trade deals",
                 "journal verify")


def _trades(conn: sqlite3.Connection) -> Check:
    """Is `trades` still the derivation of `deals_raw` it claims to be?

    Two ways it stops being one, and the first does not catch the second: a
    position with NO trade row at all (never rebuilt), and a position whose row
    exists but predates a deal that has since landed on it — a partial close, a
    re-synced OUT. The second is why the watermark comparison is here as well
    as the row count.

    Trap 1's positive whitelist, not its complement: only BUY/SELL deals with a
    non-zero `position_id` reconstruct into a trade, so counting anything else
    (a balance deal ingested last night) would leave a warning no `rebuild`
    could ever clear.

    ponytail: an SL/TP snapshot from the M4 poller also makes `trades` stale
    (it feeds `sl_initial`) and moves neither watermark. Add
    `sl_tp_snapshots.observed_msc` to the comparison if a stale `sl_initial`
    ever costs something.
    """
    (missing,) = conn.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT DISTINCT d.account_login, d.position_id FROM deals_raw d"
        "  WHERE d.position_id != 0 AND d.type IN (?, ?)"
        "    AND NOT EXISTS (SELECT 1 FROM trades t"
        "                    WHERE t.account_login = d.account_login"
        "                      AND t.position_id = d.position_id))",
        (int(DealType.BUY), int(DealType.SELL)),
    ).fetchone()
    (n_trades,) = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
    if missing:
        return Check("trades", "warn",
                     f"{missing} position(s) in deals_raw have no trade row "
                     f"({n_trades} trades built)",
                     "journal rebuild")

    (stale,) = conn.execute(
        "SELECT COUNT(*) FROM deals_raw WHERE position_id != 0 AND type IN (?, ?) "
        "AND ingested_at > COALESCE((SELECT MAX(rebuilt_at) FROM trades), 0)",
        (int(DealType.BUY), int(DealType.SELL)),
    ).fetchone()
    if stale:
        return Check("trades", "warn",
                     f"{stale} trade deal(s) ingested since the last rebuild — "
                     f"{n_trades} trades are derived from older raw data",
                     "journal rebuild")
    return Check("trades", "ok", f"{n_trades} trades, every raw position reconstructed")


def _backup(db_path: Path | str, now: float) -> Check:
    """Age of the newest AUTO-named snapshot — `backup.due`'s rule, read through
    `backup.auto_dir` so the folder and the prefix have one definition."""
    d = backup.auto_dir(db_path)
    snaps = list(d.glob(f"{backup.AUTO_PREFIX}*.db")) if d.is_dir() else []
    if not snaps:
        return Check("backup", "warn",
                     f"no snapshot in {d} — journal.db cannot be re-synced (Trap 16)",
                     "journal backup")
    newest = max(snaps, key=lambda p: p.stat().st_mtime)
    age = now - newest.stat().st_mtime
    detail = f"{newest.name}, {_age(age)} old ({len(snaps)} kept)"
    if age >= BACKUP_MAX_AGE_S:
        return Check("backup", "warn", detail + " — overdue", "journal backup")
    return Check("backup", "ok", detail)


def _live(conn: sqlite3.Connection, now: float) -> Check:
    """The daemon, and anything queued for it.

    Not running is NOT a warning on its own — most of this journal's life is
    `sync` and `rebuild` by hand. A command queued with nothing running to send
    it is: the human believes an SL is in flight and it is sitting in a table.
    """
    from .live_store import read_heartbeat

    beat = read_heartbeat(conn)
    (pending,) = conn.execute(
        "SELECT COUNT(*) FROM trade_commands WHERE status IN ('pending', 'claimed')"
    ).fetchone()
    # 'claimed' counts too: `journal live` marks a row claimed before it sends,
    # so a row stuck there is a command that died between the queue and the
    # broker — the same "nobody is going to send this" the human needs to see.
    queued = f", {pending} command(s) pending/claimed" if pending else ""

    if beat is None:
        detail = "no heartbeat — `journal live` has never run against this store"
        return (Check("live", "warn", detail + queued, "journal live") if pending
                else Check("live", "ok", detail))

    age = now - beat / 1000.0
    if age >= HEARTBEAT_MAX_AGE_S:
        return Check("live", "warn",
                     f"last heartbeat {_age(age)} ago — `journal live` is not running"
                     + queued,
                     "journal live")
    return Check("live", "ok", f"heartbeat {_age(age)} ago" + queued)


def checks(conn: sqlite3.Connection, db_path: Path | str, *,
           now: float | None = None) -> list[Check]:
    """Every check, in the order a broken journal breaks. Pure given
    `(conn, db_path, now)` — `now` is epoch SECONDS, injected by the tests."""
    t = time.time() if now is None else now
    return [
        _integrity(conn),
        _balance(conn),
        _trades(conn),
        _backup(db_path, t),
        _live(conn, t),
    ]
