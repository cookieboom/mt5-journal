"""`store/backup.restore()` — reading a snapshot back into place.

The command wrapper (prompt, `--yes`, exit codes) is pinned in `test_cli.py`.
What is here is the part that decides whether a bad day stays recoverable: what
it refuses to touch, and what it refuses to throw away.
"""

from __future__ import annotations

import os
import sqlite3
import time

import pytest

from journal.store import backup as bk
from journal.store.db import connect
from journal.store.live_store import beat


def _store(path, *, deals=0):
    """A real, migrated journal with `deals` distinguishable raw deals in it."""
    conn = connect(path)
    for i in range(deals):
        _add_deal(conn, i + 1)
    conn.commit()
    conn.close()
    return path


def _add_deal(conn, ticket):
    conn.execute(
        "INSERT INTO deals_raw (account_login, ticket, position_id, type, entry, "
        "time_msc, raw_json, ingested_at) VALUES (1, ?, 0, 2, 0, 1, '{}', 0)",
        (ticket,),
    )


def _snapshot_of(db, **kw):
    return bk.snapshot(db, **kw).out


def test_restore_replaces_the_store_with_the_snapshot(tmp_path):
    db = _store(tmp_path / "journal.db", deals=3)
    snap = _snapshot_of(db)
    # Three more deals land, then the file is judged bad.
    _store(db, deals=0)
    conn = connect(db)
    conn.execute("DELETE FROM deals_raw")
    conn.commit()
    conn.close()

    r = bk.restore(db, snap)

    assert r.integrity == "ok"
    assert r.n_deals == 3
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM deals_raw").fetchone()[0] == 3
    conn.close()


def test_restore_keeps_the_replaced_file_instead_of_deleting_it(tmp_path):
    db = _store(tmp_path / "journal.db", deals=1)
    snap = _snapshot_of(db)
    _store(db, deals=0)

    r = bk.restore(db, snap)

    assert r.replaced is not None and r.replaced.exists()
    assert r.replaced != db
    # Still openable: the point of keeping it is that it may hold deals the
    # snapshot predates, and `sync` cannot always get them back (Trap 16).
    conn = sqlite3.connect(str(r.replaced))
    assert conn.execute("SELECT COUNT(*) FROM deals_raw").fetchone()[0] == 1
    conn.close()


def test_restore_moves_the_wal_and_shm_aside_too(tmp_path):
    # Left behind, the previous database's WAL can be recovered INTO the
    # restored file — the failure `cp backup journal.db` cannot see.
    db = _store(tmp_path / "journal.db", deals=1)
    snap = _snapshot_of(db)
    (tmp_path / "journal.db-wal").write_bytes(b"stale wal")
    (tmp_path / "journal.db-shm").write_bytes(b"stale shm")

    r = bk.restore(db, snap)

    assert not (tmp_path / "journal.db-wal").exists()
    assert not (tmp_path / "journal.db-shm").exists()
    # They go WITH the file they belong to, so the replaced store can still be
    # opened (and recover its own WAL) if it turns out to hold something.
    assert r.replaced.with_name(r.replaced.name + "-wal").read_bytes() == b"stale wal"
    # (the -shm is rebuilt by the read-only heartbeat probe before the move, so
    # only its existence is this test's business)
    assert r.replaced.with_name(r.replaced.name + "-shm").exists()


def test_restore_refuses_a_source_that_fails_its_own_integrity_check(tmp_path):
    db = _store(tmp_path / "journal.db", deals=2)
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)

    with pytest.raises(bk.BackupError):
        bk.restore(db, bad)

    # The target is untouched — a refused restore must cost nothing.
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM deals_raw").fetchone()[0] == 2
    conn.close()


def test_restore_refuses_a_source_that_does_not_exist(tmp_path):
    db = _store(tmp_path / "journal.db", deals=1)

    with pytest.raises(bk.BackupError):
        bk.restore(db, tmp_path / "nope.db")


def test_restore_refuses_to_restore_a_file_over_itself(tmp_path):
    db = _store(tmp_path / "journal.db", deals=1)

    with pytest.raises(bk.BackupError):
        bk.restore(db, db)


def test_restore_refuses_while_journal_live_is_writing(tmp_path):
    # The daemon holds the old file open: after a copy it keeps committing into
    # the file that was replaced, and the store forks in two.
    db = _store(tmp_path / "journal.db", deals=1)
    snap = _snapshot_of(db)
    conn = connect(db)
    beat(conn, int(time.time() * 1000))
    conn.close()

    with pytest.raises(bk.BackupError, match="live"):
        bk.restore(db, snap)


def test_restore_proceeds_once_the_heartbeat_is_stale(tmp_path):
    db = _store(tmp_path / "journal.db", deals=1)
    snap = _snapshot_of(db)
    conn = connect(db)
    beat(conn, int((time.time() - 3600) * 1000))
    conn.close()

    assert bk.restore(db, snap).integrity == "ok"


def test_restore_defaults_to_the_newest_auto_snapshot(tmp_path):
    db = _store(tmp_path / "journal.db", deals=1)
    old = _snapshot_of(db)
    conn = connect(db)
    _add_deal(conn, 99)
    conn.commit()
    conn.close()
    new = _snapshot_of(db, dest=old.parent / "journal-20260813T000001Z.db")
    # Fixed mtimes: two snapshots taken inside the same second must not decide
    # this test's outcome.
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (1_000_100, 1_000_100))

    r = bk.restore(db, None)

    assert r.source == new
    assert r.n_deals == 2


def test_restore_without_a_source_and_without_any_snapshot_refuses(tmp_path):
    db = _store(tmp_path / "journal.db", deals=1)

    with pytest.raises(bk.BackupError, match="no snapshot"):
        bk.restore(db, None)


def test_restore_works_when_the_target_is_missing_entirely(tmp_path):
    # The file was deleted, which is one of the reasons to type this at all.
    db = _store(tmp_path / "journal.db", deals=2)
    snap = _snapshot_of(db)
    db.unlink()

    r = bk.restore(db, snap)

    assert r.replaced is None
    assert r.n_deals == 2


def test_restore_works_when_the_target_is_too_corrupt_to_open(tmp_path):
    # Nothing can be writing to it sanely, so the heartbeat check cannot and
    # must not block the one command that repairs it.
    db = _store(tmp_path / "journal.db", deals=2)
    snap = _snapshot_of(db)
    db.write_bytes(b"not a database at all")

    r = bk.restore(db, snap)

    assert r.integrity == "ok" and r.n_deals == 2
    assert r.replaced is not None and r.replaced.exists()
