"""`store/backup.py` — the snapshot logic shared by `journal backup` and the
`journal live` loop.

The command's own behaviour (output, exit codes, `--dest`, pruning) is pinned in
`test_cli.py` and not repeated here. What is here is the part the loop depends
on and the command never exercises: `due()`, the stateless "has one been taken
recently" check, which is the only thing standing between a daemon and a
backup every 5 seconds.
"""

from __future__ import annotations

import os

import pytest

from journal.store import backup as bk
from journal.store.db import connect


def _seeded_db(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    conn.commit()
    conn.close()
    return db


def _snap(tmp_path, name, age_s=0.0):
    d = tmp_path / "backups"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(b"")
    if age_s:
        t = os.stat(p).st_mtime - age_s
        os.utime(p, (t, t))
    return p


def test_due_when_nothing_has_ever_been_backed_up(tmp_path):
    assert bk.due(tmp_path / "t.db", 86400.0) is True


def test_not_due_while_a_recent_snapshot_exists(tmp_path):
    _snap(tmp_path, "journal-20260813T000000Z.db")

    assert bk.due(tmp_path / "t.db", 86400.0) is False


def test_due_again_once_the_newest_snapshot_is_older_than_the_interval(tmp_path):
    _snap(tmp_path, "journal-20260813T000000Z.db", age_s=86401.0)

    assert bk.due(tmp_path / "t.db", 86400.0) is True


def test_due_ignores_files_this_module_did_not_write(tmp_path):
    # A hand-named archive in the same folder must not be read as "we're covered"
    # — only auto-named snapshots are this module's own record of itself.
    _snap(tmp_path, "keep-me.db")

    assert bk.due(tmp_path / "t.db", 86400.0) is True


def test_snapshot_reports_where_it_wrote_and_that_it_read_it_back(tmp_path):
    db = _seeded_db(tmp_path)

    s = bk.snapshot(db)

    assert s.out.exists() and s.out.parent == tmp_path / "backups"
    assert s.integrity == "ok"
    # And the fresh snapshot immediately satisfies the due-check — the property
    # the loop relies on to not back up every cycle.
    assert bk.due(db, 86400.0) is False


def test_snapshot_refuses_a_missing_source_instead_of_creating_one(tmp_path):
    missing = tmp_path / "nope.db"

    with pytest.raises(bk.BackupError):
        bk.snapshot(missing)

    assert not missing.exists()


def test_snapshot_refuses_to_overwrite(tmp_path):
    db = _seeded_db(tmp_path)
    dest = tmp_path / "snap.db"
    dest.write_bytes(b"not mine to destroy")

    with pytest.raises(bk.BackupError):
        bk.snapshot(db, dest=dest)

    assert dest.read_bytes() == b"not mine to destroy"
