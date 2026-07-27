import sqlite3
import pytest
from journal.store.db import connect
from journal.store import live_store as ls


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def test_read_heartbeat_none_when_never_beaten(conn):
    assert ls.read_heartbeat(conn) is None


def test_beat_then_read(conn):
    ls.beat(conn, 1_700_000_000_000)
    assert ls.read_heartbeat(conn) == 1_700_000_000_000


def test_beat_overwrites_single_row(conn):
    ls.beat(conn, 1_700_000_000_000)
    ls.beat(conn, 1_700_000_005_000)
    assert ls.read_heartbeat(conn) == 1_700_000_005_000
    assert conn.execute("SELECT COUNT(*) c FROM live_heartbeat").fetchone()["c"] == 1
