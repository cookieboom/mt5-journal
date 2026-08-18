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


def test_trade_png_prefs_roundtrip(conn):
    assert ps.get_trade_png_prefs(conn) is None
    ts = ps.set_trade_png_prefs(conn, {"theme": "nightclouds", "pad_bars": 40})
    assert isinstance(ts, int) and ts > 0
    assert ps.get_trade_png_prefs(conn) == {"theme": "nightclouds", "pad_bars": 40}
    assert ps.get_pref(conn, ps.TRADE_PNG_KEY) is not None


def test_drawings_key_uses_symbol_base_not_the_raw_symbol(conn):
    # Rule 11: the broker suffix never reaches a storage key.
    assert ps.drawings_key("XAUUSDc", None) == "drawings:XAUUSD"
    assert ps.drawings_key("XAUUSD", None) == "drawings:XAUUSD"


def test_drawings_key_for_a_replay_session_is_separate_from_live():
    # A replay session is symbol-bound by construction, so its key carries the
    # session id instead of the symbol. Live drawings must never leak in.
    assert ps.drawings_key("XAUUSDc", 42) == "drawings:replay:42"
    assert ps.drawings_key("XAUUSDc", 42) != ps.drawings_key("XAUUSDc", None)


def test_drawings_roundtrip_parses_json(conn):
    assert ps.get_drawings(conn, "XAUUSDc", None) is None
    blob = {"v": 1, "items": [{"id": "d1", "kind": "hline", "price": 2415.5}]}
    ts = ps.set_drawings(conn, "XAUUSDc", None, blob)
    assert isinstance(ts, int) and ts > 0
    assert ps.get_drawings(conn, "XAUUSDc", None) == blob


def test_drawings_are_isolated_per_symbol_and_per_session(conn):
    gold = {"v": 1, "items": [{"id": "g", "kind": "hline", "price": 2415.5}]}
    btc = {"v": 1, "items": [{"id": "b", "kind": "hline", "price": 61000.0}]}
    replay = {"v": 1, "items": [{"id": "r", "kind": "hline", "price": 2400.0}]}
    ps.set_drawings(conn, "XAUUSDc", None, gold)
    ps.set_drawings(conn, "BTCUSDc", None, btc)
    ps.set_drawings(conn, "XAUUSDc", 7, replay)

    assert ps.get_drawings(conn, "XAUUSDc", None) == gold
    assert ps.get_drawings(conn, "BTCUSDc", None) == btc
    assert ps.get_drawings(conn, "XAUUSDc", 7) == replay
    assert ps.get_drawings(conn, "XAUUSDc", 8) is None


def test_set_drawings_upserts_one_row_per_key(conn):
    ps.set_drawings(conn, "XAUUSDc", None, {"v": 1, "items": []})
    ps.set_drawings(conn, "XAUUSDc", None, {"v": 1, "items": [{"id": "x", "kind": "hline", "price": 1.0}]})
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM app_prefs WHERE key = 'drawings:XAUUSD'"
    ).fetchone()
    assert row["n"] == 1


def test_paper_prefs_round_trip_under_their_own_key(conn):
    assert ps.get_paper_prefs(conn) is None
    ps.set_paper_prefs(conn, {"mode": "paper", "accountId": 3})
    assert ps.get_paper_prefs(conn) == {"mode": "paper", "accountId": 3}
    # Its own key: turning paper on must not disturb the chart's appearance.
    assert ps.get_chart_prefs(conn) is None
