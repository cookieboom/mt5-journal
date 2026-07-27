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


def test_active_watches_empty(conn):
    assert ls.active_watches(conn, 1_700_000_000_000) == []


def test_upsert_then_active(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    assert ls.active_watches(conn, 1_700_000_010_000) == [("XAUUSDc", "M5")]


def test_watch_expires(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    assert ls.active_watches(conn, 1_700_000_040_000) == []   # past expiry


def test_upsert_is_idempotent_per_pair(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_005_000, ttl_ms=30_000)
    assert conn.execute("SELECT COUNT(*) c FROM live_watches").fetchone()["c"] == 1
    assert ls.active_watches(conn, 1_700_000_034_000) == [("XAUUSDc", "M5")]  # refreshed


def test_prune_expired(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    assert ls.prune_expired(conn, 1_700_000_040_000) == 1
    assert conn.execute("SELECT COUNT(*) c FROM live_watches").fetchone()["c"] == 0
