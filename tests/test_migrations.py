"""M9 Phase 1 — the migration runner.

Written before the implementation (CLAUDE.md rule 7).

Until now `connect()` applied `schema.sql` ONLY to a brand-new DB (`_is_fresh`),
so a new table added to the schema would silently never appear in the live
`data/journal.db`. That is the bug this phase closes.

The load-bearing property, and the one that will keep being true as v3/v4 land:
**a freshly-created DB and a migrated old DB must have the SAME schema.** Two
ways to arrive at a schema means two schemas that can drift; the equivalence
test below is what makes the pair of paths safe to keep.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from journal.store import db as dbmod
from journal.store.db import SCHEMA_VERSION, connect, current_version, migrate

# What a pre-M9 database actually looks like: `schema.sql` exactly as it stood at
# v1, FROZEN as a fixture and never regenerated. Sourcing the "old shape" from
# the live `schema.sql` would make the equivalence test below assert that today
# equals today — the fixture has to be a snapshot, or it proves nothing.
_V1_SCHEMA = (
    Path(__file__).resolve().parent / "fixtures" / "schema_v1.sql"
).read_text()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _make_v1(path) -> None:
    """A pre-M9 database: v1 schema, stamped version 1, with one row of real-ish
    data so the migration is proven not to destroy anything."""
    c = sqlite3.connect(str(path))
    c.executescript(_V1_SCHEMA)
    c.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (1, 0)"
    )
    c.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, ?, ?)",
        (0, "USC", 1),
    )
    c.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, fetched_at) VALUES (?, ?, ?)",
        ("XAUUSDc", "XAUUSD", 1),
    )
    c.commit()
    c.close()


# ------------------------------------------------------------------ version


def test_schema_version_is_2():
    """M9 adds open_positions / trade_commands / symbol_specs columns."""
    assert SCHEMA_VERSION == 2


def test_fresh_db_is_stamped_at_current_version(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        assert current_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_current_version_of_a_v1_db_is_1(tmp_path):
    p = tmp_path / "old.db"
    _make_v1(p)
    c = sqlite3.connect(str(p))
    try:
        assert current_version(c) == 1
    finally:
        c.close()


# ------------------------------------------------------------------ running


def test_connect_migrates_an_existing_db(tmp_path):
    """The actual bug being fixed: opening an OLD db must bring it forward.
    Before M9, `connect()` skipped everything when the DB was not fresh."""
    p = tmp_path / "old.db"
    _make_v1(p)

    conn = connect(p)
    try:
        assert current_version(conn) == SCHEMA_VERSION
        assert "open_positions" in _table_names(conn)
        assert "trade_commands" in _table_names(conn)
    finally:
        conn.close()


def test_migration_preserves_existing_rows(tmp_path):
    """Rule 2's spirit: a migration may add, never destroy. The account row and
    the symbol spec written under v1 must survive verbatim."""
    p = tmp_path / "old.db"
    _make_v1(p)

    conn = connect(p)
    try:
        assert conn.execute("SELECT currency FROM accounts WHERE login=0").fetchone()[0] == "USC"
        row = conn.execute(
            "SELECT symbol_base FROM symbol_specs WHERE symbol='XAUUSDc'"
        ).fetchone()
        assert row["symbol_base"] == "XAUUSD"
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path):
    """Running it twice is a no-op — `journal migrate` is safe to re-run, and
    every `connect()` calls it."""
    p = tmp_path / "old.db"
    _make_v1(p)

    conn = connect(p)
    try:
        before = current_version(conn)
        applied = migrate(conn)
        assert applied == []          # nothing left to do
        assert current_version(conn) == before
    finally:
        conn.close()


def test_migrate_reports_what_it_applied(tmp_path):
    """The CLI prints this; it must be the real list, not a guess."""
    p = tmp_path / "old.db"
    _make_v1(p)

    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        applied = migrate(conn)
        assert applied == [2]
    finally:
        conn.close()


# -------------------------------------------------------------- equivalence


def test_migrated_db_matches_a_fresh_db(tmp_path):
    """THE test of this phase. Two paths to a v2 schema — schema.sql for a fresh
    DB, migrations for an old one — must agree on every table and every column.
    If they drift, one of the two paths is producing a database the code has
    never been tested against."""
    old = tmp_path / "old.db"
    _make_v1(old)
    migrated = connect(old)
    fresh = connect(tmp_path / "fresh.db")

    try:
        # Every table the fresh (authoritative) schema defines must exist in the
        # migrated DB. The migrated DB is allowed no extras either.
        assert _table_names(migrated) == _table_names(fresh)

        for table in sorted(_table_names(fresh)):
            assert _columns(migrated, table) == _columns(fresh, table), (
                f"column drift in {table!r} between schema.sql and the migrations"
            )
    finally:
        migrated.close()
        fresh.close()


# ------------------------------------------------------- the new v2 surface


def test_symbol_specs_gained_the_order_validation_columns(tmp_path):
    """Phase 3 cannot validate a lot size or an SL distance without these, and
    rule 4 says an un-refetched spec is UNKNOWN — so they are all nullable."""
    conn = connect(tmp_path / "j.db")
    try:
        cols = _columns(conn, "symbol_specs")
        assert {
            "volume_min", "volume_max", "volume_step",
            "stops_level", "freeze_level", "trade_mode", "filling_mode",
        } <= cols
        # Nullable: a spec row written without them must still insert.
        conn.execute(
            "INSERT INTO symbol_specs (symbol, symbol_base, fetched_at) "
            "VALUES ('EURUSDc', 'EURUSD', 1)"
        )
        row = conn.execute(
            "SELECT volume_min, volume_step FROM symbol_specs WHERE symbol='EURUSDc'"
        ).fetchone()
        assert row["volume_min"] is None
        assert row["volume_step"] is None
    finally:
        conn.close()


def test_open_positions_is_a_current_state_mirror(tmp_path):
    """One row per open position, keyed so a re-snapshot REPLACEs rather than
    accumulating. History lives in the append-only `sl_tp_snapshots` (M4); this
    table is deliberately not history."""
    conn = connect(tmp_path / "j.db")
    try:
        for _ in range(2):
            conn.execute(
                "INSERT OR REPLACE INTO open_positions "
                "(account_login, position_id, symbol, symbol_base, direction, "
                " volume, open_price, observed_msc) "
                "VALUES (0, 123, 'XAUUSDc', 'XAUUSD', 'buy', 0.01, 3300.0, 5)"
            )
        assert conn.execute("SELECT count(*) FROM open_positions").fetchone()[0] == 1
    finally:
        conn.close()


def test_trade_commands_rejects_an_unknown_kind(tmp_path):
    """The CHECK constraint is the last line of defence behind Phase 3's
    validation — a typo'd kind must not become a pending row nobody executes."""
    conn = connect(tmp_path / "j.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_commands "
                "(account_login, position_id, kind, requested_msc, status) "
                "VALUES (0, 123, 'liquidate_everything', 1, 'pending')"
            )
    finally:
        conn.close()


def test_trade_commands_rejects_an_unknown_status(tmp_path):
    conn = connect(tmp_path / "j.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_commands "
                "(account_login, position_id, kind, requested_msc, status) "
                "VALUES (0, 123, 'close', 1, 'probably_fine')"
            )
    finally:
        conn.close()


def test_trade_commands_defaults_to_pending(tmp_path):
    """A row inserted without a status is an intent nobody has acted on yet."""
    conn = connect(tmp_path / "j.db")
    try:
        conn.execute(
            "INSERT INTO trade_commands "
            "(account_login, position_id, kind, requested_msc) "
            "VALUES (0, 123, 'close', 1)"
        )
        row = conn.execute("SELECT status, retcode FROM trade_commands").fetchone()
        assert row["status"] == "pending"
        # Rule 4: no broker verdict yet is UNKNOWN, not 0.
        assert row["retcode"] is None
    finally:
        conn.close()


def test_migration_files_are_numbered_contiguously_from_2():
    """A gap or a duplicate number means a migration silently never runs.
    Cheap structural check; costs nothing and catches a real filing mistake."""
    files = sorted(dbmod.migration_files())
    numbers = [int(p.name.split("_", 1)[0]) for p in files]
    assert numbers == list(range(2, SCHEMA_VERSION + 1))
