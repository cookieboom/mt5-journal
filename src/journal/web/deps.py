"""Request-scoped dependencies shared by every router.

`create_app` stores the DB path and cache dir on `app.state`; routes reach them
through these instead of closing over `create_app`'s locals, which is what let
the routes live in their own modules.
"""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Request

from ..store.db import connect


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """One SQLite connection per request, closed in a `finally` — the same
    `connect(db) ... finally: conn.close()` shape every CLI command uses.
    `create_app` already migrated the store; a request only verifies it."""
    conn = connect(request.app.state.db_path, migrate=False)
    try:
        yield conn
    finally:
        conn.close()


def db_path(request: Request) -> str:
    return request.app.state.db_path


def cache_dir(request: Request) -> str:
    return request.app.state.cache_dir
