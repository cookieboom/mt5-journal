"""app_prefs store — pure DB, no bridge. Roundtrip, upsert, and the chart-JSON
convenience wrappers. Mirrors tests/test_candles_store.py: seeded tmp DB, no HTTP."""
from __future__ import annotations

from journal.store import prefs_store as ps
from journal.store.db import connect

import pytest


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def test_get_pref_unknown_key_is_none(conn):
    assert ps.get_pref(conn, "nope") is None


def test_set_then_get_roundtrips_raw_text(conn):
    ps.set_pref(conn, "k", '{"a":1}', updated_ms=111)
    assert ps.get_pref(conn, "k") == '{"a":1}'


def test_set_pref_upserts_same_key_and_bumps_updated_ms(conn):
    ps.set_pref(conn, "k", "v1", updated_ms=100)
    ts = ps.set_pref(conn, "k", "v2", updated_ms=200)
    assert ps.get_pref(conn, "k") == "v2"
    assert ts == 200
    row = conn.execute("SELECT COUNT(*) AS n FROM app_prefs WHERE key='k'").fetchone()
    assert row["n"] == 1  # upsert, not a second row


def test_chart_prefs_roundtrip_parses_json(conn):
    assert ps.get_chart_prefs(conn) is None
    ts = ps.set_chart_prefs(conn, {"version": 1, "theme": "light"})
    assert isinstance(ts, int) and ts > 0
    assert ps.get_chart_prefs(conn) == {"version": 1, "theme": "light"}
    # stored under the reserved chart key
    assert ps.get_pref(conn, ps.CHART_KEY) is not None


def test_chart_prefs_envelope_shape(conn):
    # GET envelope: {"prefs": null} before any save, {"prefs": {...}} after.
    assert {"prefs": ps.get_chart_prefs(conn)} == {"prefs": None}
    ps.set_chart_prefs(conn, {"version": 1, "theme": "dark", "grid": True})
    assert {"prefs": ps.get_chart_prefs(conn)} == {
        "prefs": {"version": 1, "theme": "dark", "grid": True}
    }


def test_replay_prefs_roundtrip_parses_json(conn):
    assert ps.get_replay_prefs(conn) is None
    ts = ps.set_replay_prefs(conn, {"version": 1, "symbol": "BTCUSDc", "speed": 7})
    assert isinstance(ts, int) and ts > 0
    assert ps.get_replay_prefs(conn) == {"version": 1, "symbol": "BTCUSDc", "speed": 7}
    assert ps.get_pref(conn, ps.REPLAY_KEY) is not None


def test_replay_and_chart_prefs_do_not_collide(conn):
    ps.set_chart_prefs(conn, {"version": 1, "theme": "dark"})
    ps.set_replay_prefs(conn, {"version": 1, "symbol": "EURUSDc"})
    assert ps.get_chart_prefs(conn) == {"version": 1, "theme": "dark"}
    assert ps.get_replay_prefs(conn) == {"version": 1, "symbol": "EURUSDc"}
