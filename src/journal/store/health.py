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

import hashlib
import json
import sqlite3
import sys
import time
from collections.abc import Callable
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


_PKG = Path(__file__).resolve().parent.parent   # src/journal


def newest_source(pkg: Path | None = None) -> tuple[str, float]:
    """The most recently edited `.py` in this package: `(relative name, mtime)`.

    The daemon's code age, measured the same way `web.app.stale_dist_reason`
    measures the bundle's — mtimes, no hashes, no build stamp. It inherits that
    check's one blind spot too: a `git checkout` rewrites mtimes, so restoring
    OLD code can read as new. Both only ever cost a warning, never correctness.
    """
    files = [p for p in (pkg or _PKG).glob("**/*.py") if p.is_file()]
    if not files:
        return ("", 0.0)
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return (str(newest.relative_to(pkg or _PKG)), newest.stat().st_mtime)


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def code_fingerprint() -> str:
    """The code THIS process is running: JSON `{module path: sha256[:12]}` over
    every `journal.*` module already imported.

    `newest_source` above answers "is anything on disk newer than the daemon",
    and both of its errors come from the same place — an mtime is not the code.
    It fired on any `.py` under the package, including the ones the live loop
    never imports, so a warning that means "restart me" arrived after editing a
    web view; and a `git checkout` rewriting mtimes let genuinely old code read
    as new. Content answers both, and names the file that actually moved.

    Only imported modules are listed, and that is the honest set: a module the
    daemon has not loaded yet will be read fresh off disk when it first needs
    it, so an edit to it is not old code running. The gap is the module it
    imports lazily AFTER this ran — edited later, it stays unlisted and unseen.
    """
    out: dict[str, str] = {}
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        p = Path(f).resolve()
        try:
            rel = p.relative_to(_PKG)
        except ValueError:
            continue                      # not ours: stdlib, site-packages
        sha = _sha(p)
        if sha:
            out[str(rel)] = sha
    return json.dumps(out, sort_keys=True)


def changed_modules(fingerprint: str) -> list[str]:
    """Which files in `fingerprint` no longer match the bytes on disk.

    A file that has since been deleted counts as changed — it cannot be proven
    to match. Unparseable JSON yields nothing: an unreadable stamp is an unknown
    daemon, and unknown is never an accusation (`NULL means unknown`, rule 4).
    """
    try:
        recorded = json.loads(fingerprint)
    except (TypeError, ValueError):
        return []
    if not isinstance(recorded, dict):
        return []
    return [rel for rel, sha in sorted(recorded.items()) if _sha(_PKG / rel) != sha]


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
                 "journal backup --dest rescue.db && journal restore   "
                 "# rescue what is left FIRST, then put the newest snapshot back")


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
    from .live_store import read_code_fingerprint, read_heartbeat, read_started

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

    # Alive, but is it running the code on disk? A restart is the last step of
    # nearly every change to the live loop, and it is the one step no test can
    # perform. Ask the daemon's own fingerprint first: it names the modules it
    # loaded, so an edit to a module it never imports says nothing.
    started = read_started(conn)
    started_s = None if started is None else started / 1000.0
    up = "" if started_s is None else f", up {_age(now - started_s)}"
    fingerprint = read_code_fingerprint(conn)
    if fingerprint:
        changed = changed_modules(fingerprint)
        if changed:
            more = f" (+{len(changed) - 1} more)" if len(changed) > 1 else ""
            return Check("live", "warn",
                         f"heartbeat {_age(age)} ago{up}, but {changed[0]}{more} "
                         "changed since it loaded — it is running OLD code"
                         + queued,
                         "restart `journal live`")
    elif started_s is not None:
        # A daemon from before the fingerprint column: mtimes are all it left,
        # and silence would read as "current".
        name, src_mtime = newest_source()
        if src_mtime > started_s:
            return Check("live", "warn",
                         f"heartbeat {_age(age)} ago, but the daemon has been up "
                         f"{_age(now - started_s)} and {name} changed "
                         f"{_age(now - src_mtime)} ago — it is running OLD code"
                         + queued,
                         "restart `journal live`")
    return Check("live", "ok", f"heartbeat {_age(age)} ago" + queued)


def _dist() -> Check:
    """The other half of the "am I running old code" question `_live` asks.

    `journal serve` mounts `frontend/dist` from disk and never builds it, so a
    forgotten `npm run build` serves yesterday's JavaScript against today's
    Python — the symptom is a fix that "did not work". `serve` already prints
    this, once, at startup: in a terminal a human scrolled past days ago, on a
    process nobody restarts to re-read a warning. Asking it here costs one
    mtime scan and is the whole point of a command that asks everything at once.

    Never a `fail`: an unbuilt bundle serves an old page, it does not make a
    single number in this store untrue.
    """
    from ..web.app import stale_dist_reason

    reason = stale_dist_reason()
    if reason is None:
        return Check("frontend", "ok", "dist is newer than every source file")
    return Check("frontend", "warn", reason, "npm --prefix frontend run build")


def checks(conn: sqlite3.Connection, db_path: Path | str, *,
           now: float | None = None) -> list[Check]:
    """Every check, in the order a broken journal breaks. Pure given
    `(conn, db_path, now)` — `now` is epoch SECONDS, injected by the tests."""
    t = time.time() if now is None else now
    todo: list[tuple[str, Callable[[], Check]]] = [
        ("integrity", lambda: _integrity(conn)),
        ("balance", lambda: _balance(conn)),
        ("trades", lambda: _trades(conn)),
        ("backup", lambda: _backup(db_path, t)),
        ("live", lambda: _live(conn, t)),
        ("frontend", lambda: _dist()),
    ]
    out: list[Check] = []
    for name, run in todo:
        try:
            out.append(run())
        except sqlite3.DatabaseError as e:
            # A malformed page kills whichever check reads it first, and it
            # killed the whole COMMAND until 2026-08-13: `status` on a corrupted
            # copy of the live store printed a traceback out of `_balance` and
            # not one line of the report — on the single day it exists for. The
            # guard is here rather than in five checks because the next check to
            # touch a bad page has not been written yet.
            out.append(Check(name, "fail", f"{type(e).__name__}: {e}",
                             "journal restore   # this store cannot be read"))
    return out
