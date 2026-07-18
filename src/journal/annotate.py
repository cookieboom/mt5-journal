"""M6 human layer — annotation + manual-tag writes.

This is user input, NOT MT5 ingest, so it lives here rather than under `ingest/`
(which pulls FROM the bridge). It copies `ingest/deals.py:add_reconciliation`'s
shape: plain functions taking a `conn` + fields, one INSERT/UPSERT, a commit.

Everything here keys on (account_login, position_id, segment) — NEVER trades.id,
which AUTOINCREMENTs and renumbers on every `rebuild`, orphaning notes. This is
the "human layer (never rebuilt)" the schema carves out: `rebuild` regenerates
`trades` and `source='auto'` tags around these rows without touching them.

`account_login` is resolved via `one_account_login(conn)` (the codebase
convention), never passed in. Every writer refuses a position_id absent from
`trades` so a typo can't create an orphan, and surfaces a clean `AnnotateError`
instead of letting the schema's `confidence BETWEEN 1 AND 5` CHECK leak a raw
sqlite IntegrityError.
"""

from __future__ import annotations

import sqlite3

from .store.db import now_ms, one_account_login


class AnnotateError(ValueError):
    """A user-input problem stated in plain language — an unknown position_id, an
    out-of-range confidence, an empty tag. Callers (the CLI) turn it into a
    friendly message; it never surfaces a raw sqlite IntegrityError."""


def _require_trade(
    conn: sqlite3.Connection, login: int, position_id: int, segment: int
) -> None:
    """Guard: the (login, position_id, segment) must name a real trade before we
    write a note or tag for it, so a mistyped id cannot create an orphan row in
    the never-rebuilt human layer."""
    row = conn.execute(
        "SELECT 1 FROM trades "
        "WHERE account_login = ? AND position_id = ? AND segment = ?",
        (login, position_id, segment),
    ).fetchone()
    if row is None:
        raise AnnotateError(
            f"no trade with position_id={position_id} (segment={segment}). "
            "Annotations/tags key on the trade's position_id — check the id "
            "(`journal chart <id>` / `journal report`) or run `journal rebuild`."
        )


def get_annotation(
    conn: sqlite3.Connection, position_id: int, *, segment: int = 0
) -> sqlite3.Row | None:
    """The annotation row for this trade, or None if none has been written."""
    login = one_account_login(conn)
    return conn.execute(
        "SELECT * FROM annotations "
        "WHERE account_login = ? AND position_id = ? AND segment = ?",
        (login, position_id, segment),
    ).fetchone()


def set_annotation(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    setup: str | None = None,
    confidence: int | None = None,
    emotion: str | None = None,
    followed_plan: bool | int | None = None,
    notes: str | None = None,
    segment: int = 0,
) -> sqlite3.Row | None:
    """UPSERT one annotation on the PK. First write sets `created_at`; every write
    (insert or update) sets `updated_at = now_ms()`. `created_at` is preserved on
    update — it is deliberately absent from the DO UPDATE clause.

    `confidence` is validated against the schema's 1-5 CHECK here, so a bad value
    is a clean `AnnotateError`, not a raw IntegrityError. `followed_plan` is
    coerced to the schema's 0/1 integer (None stays NULL = 'not recorded')."""
    login = one_account_login(conn)
    _require_trade(conn, login, position_id, segment)

    if confidence is not None and confidence not in (1, 2, 3, 4, 5):
        raise AnnotateError(
            f"confidence must be an integer 1-5 (or omitted), got {confidence!r}."
        )
    fp = None if followed_plan is None else int(bool(followed_plan))

    ts = now_ms()
    conn.execute(
        """
        INSERT INTO annotations
            (account_login, position_id, segment, setup, confidence, emotion,
             followed_plan, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (account_login, position_id, segment) DO UPDATE SET
            setup         = excluded.setup,
            confidence    = excluded.confidence,
            emotion       = excluded.emotion,
            followed_plan = excluded.followed_plan,
            notes         = excluded.notes,
            updated_at    = excluded.updated_at
        """,
        (login, position_id, segment, setup, confidence, emotion, fp, notes, ts, ts),
    )
    conn.commit()
    return get_annotation(conn, position_id, segment=segment)


def add_tag(
    conn: sqlite3.Connection, position_id: int, tag: str, *, segment: int = 0
) -> list[tuple[str, str]]:
    """Attach a `source='manual'` tag (idempotent — INSERT OR IGNORE on the PK).
    Returns the trade's full tag list after the write."""
    login = one_account_login(conn)
    _require_trade(conn, login, position_id, segment)
    tag = (tag or "").strip()
    if not tag:
        raise AnnotateError("tag must be a non-empty string.")

    conn.execute(
        "INSERT OR IGNORE INTO tags "
        "(account_login, position_id, segment, tag, source) "
        "VALUES (?, ?, ?, ?, 'manual')",
        (login, position_id, segment, tag),
    )
    conn.commit()
    return list_tags(conn, position_id, segment=segment)


def remove_tag(
    conn: sqlite3.Connection, position_id: int, tag: str, *, segment: int = 0
) -> int:
    """Remove a MANUAL tag. The `source='manual'` filter means an auto tag can
    never be deleted through here (the auto pass owns those). Returns the number
    of rows deleted (0 if there was no such manual tag)."""
    login = one_account_login(conn)
    cur = conn.execute(
        "DELETE FROM tags WHERE account_login = ? AND position_id = ? "
        "AND segment = ? AND tag = ? AND source = 'manual'",
        (login, position_id, segment, tag),
    )
    conn.commit()
    return cur.rowcount


def list_tags(
    conn: sqlite3.Connection, position_id: int, *, segment: int = 0
) -> list[tuple[str, str]]:
    """Every tag on this trade as (tag, source), ordered so auto and manual group
    predictably in the CLI output."""
    login = one_account_login(conn)
    return [
        (r["tag"], r["source"])
        for r in conn.execute(
            "SELECT tag, source FROM tags "
            "WHERE account_login = ? AND position_id = ? AND segment = ? "
            "ORDER BY source, tag",
            (login, position_id, segment),
        )
    ]
