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

import threading

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
