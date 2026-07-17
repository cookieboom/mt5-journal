"""SQLite bootstrap. Opens a connection and, on a brand-new DB, applies the
final `schema.sql` and stamps `schema_version`.

`schema.sql` is authoritative and never edited here (CLAUDE.md: schema changes go
through a migration file, not this module).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def now_ms() -> int:
    """Current true UTC time in epoch milliseconds (integer)."""
    return int(time.time() * 1000)


def one_account_login(conn: sqlite3.Connection) -> int:
    """The single source of the 'exactly one account' guard. Returns that account's
    login or raises RuntimeError. Three call sites (rebuild, verify, the CLI) used to
    each carry their own copy — three chances to drift when multi-account support
    lands. The CLI wraps the RuntimeError into a typer.Exit for a friendly message."""
    rows = conn.execute("SELECT login FROM accounts ORDER BY login").fetchall()
    if not rows:
        raise RuntimeError("no account in the store — run `journal sync` first.")
    if len(rows) > 1:
        raise RuntimeError(
            f"multiple accounts present {[r[0] for r in rows]}; "
            "disambiguation not yet supported."
        )
    return int(rows[0][0])


def _is_fresh(conn: sqlite3.Connection) -> bool:
    """A DB is fresh if it has no `schema_version` table yet."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    return row is None


def connect(path: str | Path) -> sqlite3.Connection:
    """Open `path`, applying the schema and stamping the version if it's new.

    Returns a connection with `Row` factory and foreign keys enforced.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if _is_fresh(conn):
        conn.executescript(_SCHEMA_PATH.read_text())
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now_ms()),
        )
        conn.commit()

    return conn
