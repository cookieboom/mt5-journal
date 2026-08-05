"""`store/db.connect()` — connection-level invariants.

The web layer opens one connection per request via a FastAPI `yield` dependency.
In modern Starlette both a sync endpoint AND a sync `yield`-dependency run in the
anyio threadpool, and the dependency's setup (which creates the connection) is
not guaranteed to run on the same worker thread as the endpoint (which uses it).
SQLite's default `check_same_thread=True` turns that thread hop into a
`ProgrammingError` — the intermittent 500s seen while clicking around the SPA.
So a connection handed out by `connect()` must remain usable from another thread.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from journal.store.db import connect


def test_connection_usable_from_another_thread(tmp_path):
    """A connection created on one thread must be usable on another — mirroring
    the FastAPI threadpool handing the dependency and the endpoint to different
    worker threads."""
    conn = connect(tmp_path / "journal.db")
    try:
        result: dict[str, object] = {}

        def use_it() -> None:
            try:
                result["value"] = conn.execute("SELECT 1").fetchone()[0]
            except Exception as e:  # noqa: BLE001 — capture to assert in caller
                result["error"] = e

        t = threading.Thread(target=use_it)
        t.start()
        t.join()

        assert "error" not in result, result.get("error")
        assert result["value"] == 1
    finally:
        conn.close()


def test_migration_009_allows_an_open_command(tmp_path):
    """The audit trail of real orders must survive a table rebuild, and the new
    shape must accept exactly the rows the open path needs."""
    import sqlite3
    from journal.store.db import SCHEMA_VERSION, connect, current_version

    db = tmp_path / "m009.db"

    # Build the PRE-009 table by hand and put a row in it, so the test proves
    # the migration copies data rather than just producing the right columns.
    raw = sqlite3.connect(str(db))
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
        INSERT INTO schema_version (version, applied_at) VALUES (8, 1);
        CREATE TABLE trade_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_login INTEGER NOT NULL,
            position_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN
                ('modify_sltp','close','close_partial','add_volume')),
            sl REAL, tp REAL, volume REAL,
            requested_msc INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                ('pending','claimed','sent','done','failed','rejected')),
            claimed_msc INTEGER, completed_msc INTEGER, retcode INTEGER,
            result_deal INTEGER, result_order INTEGER, result_volume REAL,
            result_price REAL, broker_comment TEXT, error TEXT, raw_json TEXT
        );
        INSERT INTO trade_commands (account_login, position_id, kind, sl, tp,
                                    volume, requested_msc, status)
        VALUES (7, 111, 'modify_sltp', 4030.0, NULL, NULL, 1234, 'done');
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    assert current_version(conn) == SCHEMA_VERSION == 10

    # (a) the pre-existing row survived, intent columns untouched
    old = conn.execute("SELECT * FROM trade_commands WHERE id = 1").fetchone()
    assert old["kind"] == "modify_sltp"
    assert old["position_id"] == 111
    assert abs(old["sl"] - 4030.0) < 1e-9
    assert old["status"] == "done"
    assert old["symbol"] is None and old["direction"] is None and old["price_ref"] is None

    # (b) an open row is now insertable: NULL position_id, symbol/direction set
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, symbol, "
        "direction, sl, tp, volume, price_ref, requested_msc, status) "
        "VALUES (7, NULL, 'open', 'XAUUSDc', 'buy', 4030.0, 4045.0, 0.1, 4035.0, 5678, 'pending')",
    )
    conn.commit()
    new = conn.execute("SELECT * FROM trade_commands WHERE kind = 'open'").fetchone()
    assert new["position_id"] is None
    assert new["symbol"] == "XAUUSDc" and new["direction"] == "buy"
    assert abs(new["price_ref"] - 4035.0) < 1e-9

    # (c) the CHECK constraints still bite
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO trade_commands (account_login, position_id, kind, "
            "requested_msc) VALUES (7, NULL, 'teleport', 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO trade_commands (account_login, position_id, kind, "
            "direction, requested_msc) VALUES (7, NULL, 'open', 'sideways', 1)"
        )
    conn.close()
