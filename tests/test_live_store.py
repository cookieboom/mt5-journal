import sqlite3
import pytest
from journal.store.db import connect
from journal.store import live_store as ls
from journal.adapter.base import Candle


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


def test_mark_started_keeps_the_fingerprint_beside_the_start(conn):
    ls.mark_started(conn, 1_700_000_000_000, '{"ingest/live.py": "abc123abc123"}')
    assert ls.read_started(conn) == 1_700_000_000_000
    assert ls.read_code_fingerprint(conn) == '{"ingest/live.py": "abc123abc123"}'


def test_read_code_fingerprint_none_when_the_daemon_never_said(conn):
    ls.beat(conn, 1_700_000_000_000)
    assert ls.read_code_fingerprint(conn) is None


def test_a_restart_replaces_the_previous_fingerprint(conn):
    ls.mark_started(conn, 1_700_000_000_000, '{"a.py": "1"}')
    ls.mark_started(conn, 1_700_000_060_000, '{"a.py": "2"}')
    assert ls.read_code_fingerprint(conn) == '{"a.py": "2"}'


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


_BAR = Candle(time_msc=1_700_000_040_000, open=1.0, high=2.0, low=0.5, close=1.5,
              tick_volume=10, spread=3, real_volume=0)


def test_read_forming_none(conn):
    assert ls.read_forming(conn, "XAUUSDc", "M5") is None


def test_upsert_then_read_forming(conn):
    ls.upsert_forming(conn, "XAUUSDc", "M5", _BAR, 1_700_000_045_000)
    got = ls.read_forming(conn, "XAUUSDc", "M5")
    assert got == _BAR


def test_forming_overwrites(conn):
    ls.upsert_forming(conn, "XAUUSDc", "M5", _BAR, 1_700_000_045_000)
    newer = Candle(time_msc=1_700_000_040_000, open=1.0, high=9.0, low=0.5,
                   close=8.0, tick_volume=99, spread=3, real_volume=0)
    ls.upsert_forming(conn, "XAUUSDc", "M5", newer, 1_700_000_050_000)
    assert ls.read_forming(conn, "XAUUSDc", "M5") == newer
    assert conn.execute("SELECT COUNT(*) c FROM live_candles").fetchone()["c"] == 1


def test_upsert_forming_rejects_seconds(conn):
    import pytest
    bad = Candle(time_msc=1_700_000_040, open=1.0, high=2.0, low=0.5, close=1.5)
    with pytest.raises(ValueError):
        ls.upsert_forming(conn, "XAUUSDc", "M5", bad, 1_700_000_045_000)


def test_a_quote_is_overwritten_in_place_and_stamps_when_it_was_written(conn):
    ls.upsert_quote(conn, "XAUUSDc", bid=4030.0, ask=4030.5,
                    tick_msc=1_000, now_msc=1_100)
    ls.upsert_quote(conn, "XAUUSDc", bid=4031.0, ask=4031.5,
                    tick_msc=2_000, now_msc=2_100)
    row = ls.read_quote(conn, "XAUUSDc")
    assert (row["bid"], row["ask"]) == (4031.0, 4031.5)
    assert row["tick_msc"] == 2_000
    assert row["updated_msc"] == 2_100
    assert conn.execute("SELECT COUNT(*) FROM live_quotes").fetchone()[0] == 1


def test_an_unseen_symbol_has_no_quote_rather_than_a_zero_one(conn):
    assert ls.read_quote(conn, "BTCUSDc") is None
