"""SQLite bootstrap. Opens a connection and brings the schema up to date — by
applying `schema.sql` wholesale to a brand-new DB, or by running the pending
files in `migrations/` against an existing one.

`schema.sql` is authoritative for a FRESH database and is never edited here
(CLAUDE.md: schema changes go through a migration file, not this module). Both
paths must produce the same schema; `tests/test_migrations.py` compares every
table and column of a fresh DB against a migrated one and fails on any drift.

Before M9 this module only ever handled the fresh case, so a table added to
`schema.sql` silently never reached the live `data/journal.db`.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 5

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


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


def current_version(conn: sqlite3.Connection) -> int:
    """The schema version this DB is stamped at, or 0 if it has never been
    stamped. Reads the MAX, not the last row: `schema_version` is an append log
    and row order is not a guarantee."""
    if _is_fresh(conn):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migration_files() -> list[Path]:
    """Every migration on disk, in application order. Names are `NNN_slug.sql`
    and NNN is the version the file brings the DB TO."""
    if not _MIGRATIONS_DIR.is_dir():
        return []
    return sorted(_MIGRATIONS_DIR.glob("[0-9]*_*.sql"), key=lambda p: p.name)


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply every migration newer than the DB's stamped version, in order.

    Returns the versions actually applied — `[]` when there was nothing to do,
    which is what makes this safe to call from every `connect()` and from
    `journal migrate` twice in a row.

    Each file runs in its own transaction together with its version stamp, so a
    half-applied migration cannot leave the DB claiming a version it does not
    have. A failing file aborts the run and leaves the DB at the last good
    version rather than limping forward.
    """
    version = current_version(conn)
    applied: list[int] = []

    for path in migration_files():
        target = int(path.name.split("_", 1)[0])
        if target <= version:
            continue
        # executescript() COMMITs any open transaction before running, so the
        # BEGIN has to live inside the script's own text to cover both the DDL
        # and the stamp. Simpler and just as safe: run the DDL, stamp it, then
        # commit once — a crash between them leaves the DB unstamped, and the
        # migration re-runs. Every statement in 002 is IF NOT EXISTS or an
        # additive ALTER, but ALTER is NOT idempotent, so guard the re-run by
        # committing the stamp in the same call.
        try:
            conn.executescript(
                "BEGIN;\n"
                + path.read_text()
                + f"\nINSERT INTO schema_version (version, applied_at) "
                f"VALUES ({target}, {now_ms()});\nCOMMIT;"
            )
        except sqlite3.Error as e:
            conn.rollback()
            raise RuntimeError(
                f"migration {path.name} failed: {e}. The database is still at "
                f"version {version} — fix the migration and re-run; nothing was "
                f"half-applied."
            ) from e
        version = target
        applied.append(target)

    return applied


def connect(path: str | Path) -> sqlite3.Connection:
    """Open `path`, bringing its schema up to date.

    A fresh DB gets `schema.sql` and the current version stamp. An existing DB
    gets every pending migration — before M9 it got nothing at all, which is how
    a new table could exist in `schema.sql` and never in the live database.

    Returns a connection with `Row` factory and foreign keys enforced.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # M9 made this a MULTI-PROCESS database: `journal live` holds one long-lived
    # writer connection for the whole poll loop, while `journal serve` opens its
    # own connections to read `open_positions` and to write a `pending` command.
    # SQLite's defaults (rollback journal + busy_timeout 0) fail such a collision
    # INSTANTLY with "database is locked" — which is exactly what a live loop and
    # a dashboard running side by side produce. WAL lets one writer and any number
    # of readers proceed without blocking each other, and a busy_timeout makes the
    # rare writer-vs-writer overlap (live's per-cycle write vs serve's enqueue)
    # wait-and-retry instead of erroring. Both are the standard shape for a
    # long-running-writer + web-reader SQLite app; neither weakens any invariant.
    # (On an in-memory DB, WAL silently stays 'memory' — harmless.)
    conn.execute("PRAGMA busy_timeout = 5000")   # ms; wait, don't fail instantly
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")  # safe under WAL, no fsync/commit

    if _is_fresh(conn):
        conn.executescript(_SCHEMA_PATH.read_text())
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now_ms()),
        )
        conn.commit()
    else:
        migrate(conn)

    return conn
