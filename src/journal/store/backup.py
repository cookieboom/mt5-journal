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


def _read_back(path: Path) -> tuple[str, int, int]:
    """Open a copy and read what it actually contains. A backup — or a restore —
    nobody has read back is a guess. Plain sqlite3: this must not migrate the
    file it is checking."""
    chk = sqlite3.connect(str(path))
    try:
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        n_deals = chk.execute("SELECT COUNT(*) FROM deals_raw").fetchone()[0]
        n_trades = chk.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise BackupError(f"{path} is not a readable journal: {e}") from e
    finally:
        chk.close()
    return integrity, n_deals, n_trades


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

    integrity, n_deals, n_trades = _read_back(out)

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


# ------------------------------------------------------------------- restore


@dataclass(frozen=True)
class Restored:
    db: Path
    source: Path
    replaced: Path | None   # where the previous store went; None if there was none
    integrity: str
    n_deals: int
    n_trades: int


def newest_snapshot(db_path: Path | str) -> Path | None:
    """The most recent auto-named snapshot, by mtime — the same file `due()`
    measures its interval from, so "the backup `status` says you have" and "the
    backup `restore` picks" are always the same file."""
    d = auto_dir(db_path)
    snaps = list(d.glob(f"{AUTO_PREFIX}*.db")) if d.is_dir() else []
    return max(snaps, key=lambda p: p.stat().st_mtime) if snaps else None


def _live_is_writing(db_path: Path) -> bool:
    """Best effort: a fresh heartbeat means `journal live` holds this file open.

    Read-only URI, never `store.db.connect()` — this must not create, migrate or
    write the database it is about to replace. A file too damaged to answer is
    one nothing can be sanely writing to, and it is exactly the file this
    command exists to repair, so it answers False.
    """
    from .health import HEARTBEAT_MAX_AGE_S  # imported here: health imports us

    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute("SELECT beat_msc FROM live_heartbeat WHERE id = 1").fetchone()
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()
    if row is None:
        return False
    return time.time() - row[0] / 1000.0 < HEARTBEAT_MAX_AGE_S


def restore(db_path: Path | str, src: Path | str | None = None) -> Restored:
    """Put a snapshot back at `db_path`. The half of `backup` that runs on the
    worst day, and the reason it is code and not a `cp` a human improvises.

    `src` defaults to the newest auto-named snapshot. The source is verified
    BEFORE anything on disk is touched — restoring an unverified file over a
    damaged one turns one bad database into two — and the database being
    replaced is moved aside, never deleted: even a corrupt file is evidence, and
    it may hold deals the snapshot predates (Trap 16 means `sync` cannot always
    get them back).

    The `-wal`/`-shm` sidecars move with it. Leaving them beside a replaced file
    is the failure `cp` cannot see: SQLite may recover the PREVIOUS database's
    WAL frames into the restored one.

    ponytail: whole-file replacement only — no merge of the two stores. If the
    replaced file turns out to hold deals worth keeping, `journal sync` re-pulls
    what the broker still has; merging two SQLite journals by hand is how you
    get a third, wronger one.
    """
    db_path = Path(db_path)
    source = Path(src) if src is not None else newest_snapshot(db_path)
    if source is None:
        raise BackupError(f"no snapshot in {auto_dir(db_path)} to restore from.")
    if not source.exists():
        raise BackupError(f"no such snapshot: {source}")
    if source.resolve() == db_path.resolve():
        raise BackupError(f"{source} IS the database; nothing to restore.")

    integrity, _, _ = _read_back(source)
    if integrity != "ok":
        raise BackupError(f"{source} fails integrity_check ({integrity}); "
                          f"{db_path} left untouched.")

    if _live_is_writing(db_path):
        raise BackupError(
            "`journal live` is running against this store — stop it first. It "
            "holds the old file open and would keep committing into the copy "
            "this replaces.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    replaced: Path | None = None
    moved: list[tuple[Path, Path]] = []
    if db_path.exists():
        replaced = db_path.with_name(f"{db_path.stem}-replaced-{stamp}{db_path.suffix}")
        if replaced.exists():
            raise BackupError(f"{replaced} exists; refusing to overwrite it.")
        for suffix in ("", "-wal", "-shm"):
            old = db_path.with_name(db_path.name + suffix)
            if old.exists():
                new = replaced.with_name(replaced.name + suffix)
                old.rename(new)
                moved.append((old, new))

    try:
        s = sqlite3.connect(str(source))
        try:
            d = sqlite3.connect(str(db_path))
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
        integrity, n_deals, n_trades = _read_back(db_path)
    except Exception:
        # Put the old store back rather than leave the human with neither file
        # where they left it.
        db_path.unlink(missing_ok=True)
        for old, new in moved:
            new.rename(old)
        raise

    return Restored(db=db_path, source=source, replaced=replaced,
                    integrity=integrity, n_deals=n_deals, n_trades=n_trades)
