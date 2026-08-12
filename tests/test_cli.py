"""CLI tests. `candles-coverage`, `backup` and `restore` are testable without
the live bridge — `candles-warm` and `candles`/`doctor` all construct
`LiveMT5Client` and are exercised manually, not in this suite."""

from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

from journal.cli import app
from journal.store.db import connect
from journal.store import candles_store as cs


def test_candles_coverage_prints_ranges(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 180000)
    conn.commit()
    conn.close()

    res = CliRunner().invoke(app, ["candles-coverage", "--db", str(db)])

    assert res.exit_code == 0
    assert "XAUUSDc" in res.stdout and "M1" in res.stdout


# ---------------------------------------------------------------------- status


def test_status_reports_warnings_but_still_exits_zero(tmp_path):
    """The design rule: WARN never fails the exit code. A fresh store warns on
    everything it can (no account, no backup) and must still exit 0."""
    db = tmp_path / "t.db"
    connect(db).close()

    res = CliRunner().invoke(app, ["status", "--db", str(db)])

    assert res.exit_code == 0
    assert "[ok" in res.stdout and "[warn" in res.stdout
    assert "journal sync" in res.stdout and "journal backup" in res.stdout


def test_status_refuses_to_invent_a_missing_database(tmp_path):
    """`connect()` creates on open, and an empty store passes almost every
    check — the most confident possible answer to a typo'd path."""
    missing = tmp_path / "nope.db"

    res = CliRunner().invoke(app, ["status", "--db", str(missing)])

    assert res.exit_code == 1
    assert "no database" in res.stdout
    assert not missing.exists()


def test_status_fails_on_a_file_that_is_not_a_database(tmp_path):
    db = tmp_path / "t.db"
    db.write_bytes(b"this is not a database")

    res = CliRunner().invoke(app, ["status", "--db", str(db)])

    assert res.exit_code == 1
    assert "[fail" in res.stdout


# ---------------------------------------------------------------------- backup


def _seeded_db(tmp_path):
    """A DB with one recognisable row, so a snapshot can be proven non-empty."""
    db = tmp_path / "t.db"
    conn = connect(db)
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 180000)
    conn.commit()
    conn.close()
    return db


def test_backup_writes_a_snapshot_that_carries_the_data(tmp_path):
    db = _seeded_db(tmp_path)

    res = CliRunner().invoke(app, ["backup", "--db", str(db)])

    assert res.exit_code == 0
    snaps = list((tmp_path / "backups").glob("journal-*.db"))
    assert len(snaps) == 1
    # The point of a backup is that it can be READ back, not that a file appeared.
    snap = sqlite3.connect(str(snaps[0]))
    try:
        rows = snap.execute("SELECT from_msc, to_msc FROM candle_coverage").fetchall()
    finally:
        snap.close()
    assert rows == [(0, 180000)]
    assert "integrity: ok" in res.stdout


def test_backup_honours_an_explicit_destination(tmp_path):
    db = _seeded_db(tmp_path)
    dest = tmp_path / "elsewhere" / "snap.db"

    res = CliRunner().invoke(app, ["backup", "--db", str(db), "--dest", str(dest)])

    assert res.exit_code == 0
    assert dest.exists()
    assert not (tmp_path / "backups").exists()


def test_backup_refuses_to_overwrite_an_existing_file(tmp_path):
    db = _seeded_db(tmp_path)
    dest = tmp_path / "snap.db"
    dest.write_bytes(b"not a database, but not mine to destroy either")

    res = CliRunner().invoke(app, ["backup", "--db", str(db), "--dest", str(dest)])

    assert res.exit_code == 1
    assert dest.read_bytes().startswith(b"not a database")


def test_backup_of_a_missing_database_fails_instead_of_snapshotting_an_empty_one(tmp_path):
    missing = tmp_path / "nope.db"

    res = CliRunner().invoke(app, ["backup", "--db", str(missing)])

    assert res.exit_code == 1
    # sqlite3.connect() would have CREATED it — the whole point of the guard.
    assert not missing.exists()
    assert not (tmp_path / "backups").exists()


def test_backup_prunes_the_oldest_auto_named_snapshots(tmp_path):
    db = _seeded_db(tmp_path)
    old = tmp_path / "backups"
    old.mkdir()
    for stamp in ("20200101T000000Z", "20210101T000000Z", "20220101T000000Z"):
        (old / f"journal-{stamp}.db").write_bytes(b"")
    (old / "keep-me.db").write_bytes(b"")  # not auto-named: never touched

    res = CliRunner().invoke(app, ["backup", "--db", str(db), "--keep", "2"])

    assert res.exit_code == 0
    left = sorted(p.name for p in old.glob("*.db"))
    # 3 old + 1 new, keep 2 newest by name (timestamps sort chronologically).
    assert len(left) == 3 and "keep-me.db" in left
    assert "journal-20200101T000000Z.db" not in left
    assert "journal-20220101T000000Z.db" in left


def test_backup_keeps_everything_when_keep_is_zero(tmp_path):
    db = _seeded_db(tmp_path)
    old = tmp_path / "backups"
    old.mkdir()
    (old / "journal-20200101T000000Z.db").write_bytes(b"")

    res = CliRunner().invoke(app, ["backup", "--db", str(db), "--keep", "0"])

    assert res.exit_code == 0
    assert (old / "journal-20200101T000000Z.db").exists()


# --------------------------------------------------------------------- restore


def test_restore_asks_before_replacing_and_does_nothing_when_refused(tmp_path):
    db = _seeded_db(tmp_path)
    CliRunner().invoke(app, ["backup", "--db", str(db)])
    conn = connect(db)
    conn.execute("DELETE FROM candle_coverage")
    conn.commit()
    conn.close()

    res = CliRunner().invoke(app, ["restore", "--db", str(db)], input="n\n")

    assert res.exit_code == 1
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM candle_coverage").fetchone()[0] == 0
    conn.close()
    assert not list(tmp_path.glob("t-replaced-*.db"))


def test_restore_puts_the_newest_snapshot_back(tmp_path):
    db = _seeded_db(tmp_path)
    CliRunner().invoke(app, ["backup", "--db", str(db)])
    conn = connect(db)
    conn.execute("DELETE FROM candle_coverage")
    conn.commit()
    conn.close()

    res = CliRunner().invoke(app, ["restore", "--db", str(db), "--yes"])

    assert res.exit_code == 0, res.stdout
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT from_msc FROM candle_coverage").fetchall() == [(0,)]
    finally:
        conn.close()
    # The replaced store is kept, not deleted — it may hold what the snapshot lacks.
    assert len(list(tmp_path.glob("t-replaced-*.db"))) == 1
    assert "integrity: ok" in res.stdout


def test_restore_with_no_snapshot_to_restore_from_fails(tmp_path):
    db = _seeded_db(tmp_path)

    res = CliRunner().invoke(app, ["restore", "--db", str(db), "--yes"])

    assert res.exit_code == 1
    assert "no snapshot" in res.stdout


def test_restore_refuses_a_source_that_is_not_a_database(tmp_path):
    db = _seeded_db(tmp_path)
    junk = tmp_path / "notes.txt"
    junk.write_text("this is not a journal")

    res = CliRunner().invoke(
        app, ["restore", "--db", str(db), "--from", str(junk), "--yes"])

    assert res.exit_code == 1
    assert "ERROR" in res.stdout
    # Refused BEFORE anything moved: the store is still where it was.
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM candle_coverage").fetchone()[0] == 1
    conn.close()
    assert not list(tmp_path.glob("t-replaced-*.db"))
