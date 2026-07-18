"""M6 annotation + manual-tag writes — `annotate.py`.

Written before the implementation (CLAUDE.md rule 7). These are the HUMAN layer:
keyed on (account_login, position_id, segment), never trades.id, and never
rebuilt. `set_annotation` upserts; `add_tag`/`remove_tag` touch only
`source='manual'`; all writers refuse an unknown position_id and surface a clean
error (not a raw sqlite IntegrityError) on a bad confidence.
"""

from __future__ import annotations

import sqlite3

import pytest

from journal.adapter.fake import FakeMT5Client
from journal.annotate import (
    AnnotateError,
    add_tag,
    get_annotation,
    list_tags,
    remove_tag,
    set_annotation,
)
from journal.domain.reconstruct import rebuild
from journal.ingest.deals import sync
from journal.store.db import connect

_LOGIN = 0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _seed(conn):
    conn.execute(
        "INSERT INTO accounts (login, currency, balance, first_seen_at) "
        "VALUES (?, 'USC', 0.0, 0)", (_LOGIN,),
    )
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, volume, open_price, net_profit, "
        "deal_count, rebuilt_at) VALUES "
        "(?, 42, 'XAUUSDc', 'XAUUSD', 'buy', 'closed', 1, 0.1, 4000.0, 10.0, 2, 1)",
        (_LOGIN,),
    )
    conn.commit()


# ------------------------------------------------------------- set_annotation


def test_set_annotation_inserts_then_updates_in_place(conn, monkeypatch):
    _seed(conn)
    import journal.annotate as ann

    monkeypatch.setattr(ann, "now_ms", lambda: 1000)
    set_annotation(conn, 42, setup="breakout", confidence=4, notes="first")
    row = get_annotation(conn, 42)
    assert row["setup"] == "breakout" and row["confidence"] == 4
    assert row["created_at"] == 1000 and row["updated_at"] == 1000

    monkeypatch.setattr(ann, "now_ms", lambda: 2000)
    set_annotation(conn, 42, setup="pullback", confidence=5, notes="second")
    row = get_annotation(conn, 42)
    assert row["setup"] == "pullback" and row["confidence"] == 5
    assert row["created_at"] == 1000          # preserved on update
    assert row["updated_at"] == 2000          # advanced
    # exactly one row — an upsert, not a second insert.
    assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 1


def test_set_annotation_followed_plan_stored_as_int(conn):
    _seed(conn)
    set_annotation(conn, 42, followed_plan=True)
    assert get_annotation(conn, 42)["followed_plan"] == 1
    set_annotation(conn, 42, followed_plan=False)
    assert get_annotation(conn, 42)["followed_plan"] == 0


@pytest.mark.parametrize("bad", [0, 6, -1, 10])
def test_set_annotation_rejects_out_of_range_confidence_cleanly(conn, bad):
    _seed(conn)
    with pytest.raises(AnnotateError):
        set_annotation(conn, 42, confidence=bad)
    # nothing was written — the guard fires before the INSERT, so no partial row
    # and definitely no raw IntegrityError.
    assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0


def test_set_annotation_refuses_unknown_position_id(conn):
    _seed(conn)
    with pytest.raises(AnnotateError):
        set_annotation(conn, 999999, setup="oops")
    assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0


# --------------------------------------------------------------- manual tags


def test_add_tag_is_idempotent_and_manual(conn):
    _seed(conn)
    add_tag(conn, 42, "review")
    add_tag(conn, 42, "review")  # again — no duplicate (PK on tag)
    rows = conn.execute(
        "SELECT tag, source FROM tags WHERE position_id = 42"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["tag"] == "review" and rows[0]["source"] == "manual"


def test_remove_tag_removes_only_that_manual_tag(conn):
    _seed(conn)
    add_tag(conn, 42, "keep")
    add_tag(conn, 42, "drop")
    n = remove_tag(conn, 42, "drop")
    assert n == 1
    assert list_tags(conn, 42) == [("keep", "manual")]


def test_remove_tag_will_not_delete_an_auto_tag(conn):
    _seed(conn)
    conn.execute(
        "INSERT INTO tags (account_login, position_id, segment, tag, source) "
        "VALUES (?, 42, 0, 'weekend', 'auto')", (_LOGIN,),
    )
    conn.commit()
    n = remove_tag(conn, 42, "weekend")  # manual-only delete
    assert n == 0
    assert ("weekend", "auto") in list_tags(conn, 42)


def test_add_tag_refuses_unknown_position_id(conn):
    _seed(conn)
    with pytest.raises(AnnotateError):
        add_tag(conn, 999999, "nope")


def test_add_tag_rejects_empty_tag(conn):
    _seed(conn)
    with pytest.raises(AnnotateError):
        add_tag(conn, 42, "   ")


# ---------------------------------------------------- survives rebuild (human layer)


def test_annotations_and_manual_tags_survive_rebuild(conn):
    # The whole point of the position_id key: the human layer is never rebuilt.
    client = FakeMT5Client()
    sync(client, conn)
    rebuild(conn)
    pid = conn.execute("SELECT position_id FROM trades LIMIT 1").fetchone()[0]

    set_annotation(conn, pid, setup="breakout", confidence=3, notes="keep me")
    add_tag(conn, pid, "manual-note")

    rebuild(conn)  # renumbers trades.id, regenerates auto tags — human layer intact
    row = get_annotation(conn, pid)
    assert row is not None and row["setup"] == "breakout" and row["notes"] == "keep me"
    assert ("manual-note", "manual") in list_tags(conn, pid)
