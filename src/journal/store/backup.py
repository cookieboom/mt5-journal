"""Snapshot `journal.db`. Used by `journal backup` (a human types it) and by
`journal live` (nobody types anything).

Trap 16 — the broker deletes its own deal history — is why this journal exists,
and it is why this file is the ONLY copy of most of what is in it. A lost
`journal.db` cannot be re-synced: the deals are gone from the server.

SQLite's online backup API, and nothing else. It copies through the pager, so
committed data still sitting in the `-wal` comes along, and it restarts itself
if a writer commits mid-copy — which is why this is safe to run while `journal
live` and `journal serve` are up, and needs no bridge. `cp data/journal.db`
is NOT the same thing: it can hand you a file whose newest commits live only in
the `-wal` it did not copy. What lands is one self-contained file, no
`-wal`/`-shm` sidecars to keep with it.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

AUTO_PREFIX = "journal-"  # auto-named snapshots; only these are ever pruned


class BackupError(Exception):
    """A backup that did not happen, or happened and cannot be trusted."""


@dataclass(frozen=True)
class Snapshot:
    out: Path
    integrity: str
    n_deals: int
    n_trades: int
    pruned: list[Path]


def auto_dir(db_path: Path | str) -> Path:
    return Path(db_path).parent / "backups"


def due(db_path: Path | str, every_s: float, now: float | None = None) -> bool:
    """Has it been more than `every_s` since the last auto-named snapshot?

    Deliberately stateless — the answer is read off the filesystem, not out of a
    table. No daemon state to drift, nothing to migrate, and a `journal live`
    restarted six times a day still backs up once a day. Hand-named files
    (`--dest`, anything a human dropped in the folder) do not count as this
    module's own record of itself.
    """
    d = auto_dir(db_path)
    snaps = list(d.glob(f"{AUTO_PREFIX}*.db")) if d.is_dir() else []
    if not snaps:
        return True
    newest = max(p.stat().st_mtime for p in snaps)
    return (time.time() if now is None else now) - newest >= every_s


def snapshot(db_path: Path | str, dest: Path | str | None = None,
             keep: int = 7) -> Snapshot:
    """Copy the database to `<db dir>/backups/journal-<UTC>.db` (or `dest`).

    The copy is opened and `PRAGMA integrity_check`ed before this returns,
    because a backup nobody has read back is a guess. Auto-named snapshots older
    than the newest `keep` are deleted; `keep=0` and any explicit `dest` prune
    nothing.

    ponytail: same disk as the source, so it survives a corrupted DB and an
    `rm`, not a dead drive. Point `--dest` at external storage if that matters.
    """
    src_path = Path(db_path)
    if not src_path.exists():
        # sqlite3.connect() would happily CREATE it and snapshot the empty
        # result — a typo'd path must not look like a successful backup.
        raise BackupError(f"no such database: {src_path}")

    if dest:
        out = Path(dest)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = auto_dir(src_path) / f"{AUTO_PREFIX}{stamp}.db"
    if out.exists():
        # Backing up ONTO a file is data loss in the one command whose whole
        # purpose is not losing data.
        raise BackupError(f"{out} exists; refusing to overwrite.")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Plain sqlite3, not store.db.connect(): a backup must not migrate the
    # schema of the thing it is preserving.
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(out))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    chk = sqlite3.connect(str(out))
    try:
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        n_deals = chk.execute("SELECT COUNT(*) FROM deals_raw").fetchone()[0]
        n_trades = chk.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        chk.close()

    pruned: list[Path] = []
    # Never prune behind a snapshot that failed its own read-back: the old files
    # may be the last good ones left.
    if keep > 0 and not dest and integrity == "ok":
        d = auto_dir(src_path)
        # Timestamps are fixed-width UTC, so name order IS chronological order.
        olds = sorted(d.glob(f"{AUTO_PREFIX}*.db"))
        for p in olds[: max(0, len(olds) - keep)]:
            p.unlink()
            pruned.append(p)

    return Snapshot(out=out, integrity=integrity, n_deals=n_deals,
                    n_trades=n_trades, pruned=pruned)
