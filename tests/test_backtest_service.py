"""web.backtest — the Strategy Tester over stored candles. Pure DB, nothing
stored, no bridge (spec 2026-10-07-indicators-strategy-tester §1.4)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from journal.adapter.base import Candle
from journal.domain.indicators.lang import ScriptError
from journal.store import candles_store as cs
from journal.store.db import connect, now_ms
from journal.web import backtest as bt
from journal.web.app import create_app

M5 = 300_000
T0 = 1_767_225_600_000           # 2026-01-01 00:00 UTC
# long fires on bar 2 (volume 7): stop = low − 1, target = high + 1 → risk 2
SRC = 'signal("long", volume == 7, stop=low - 1, target=high + 1)'
NO_COST = {"sl": "none", "tp_r": None}


def seed(conn, n=40, fire=(2,)):
    for i in range(n):
        px = 100.0 + (i // 4)                    # steps up 1 every 4 bars
        cs.insert_candle(conn, "XAUUSDc", "M5", Candle(
            time_msc=T0 + i * M5, open=px, high=px + 1, low=px - 1, close=px,
            tick_volume=7 if i in fire else 1, spread=10, real_volume=0))
    cs.record_coverage(conn, "XAUUSDc", "M5", T0, T0 + (n - 1) * M5)
    conn.execute("INSERT INTO symbol_specs (symbol, symbol_base, point, fetched_at) "
                 "VALUES ('XAUUSDc', 'XAUUSD', 0.01, 0)")
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def run(conn, **kw):
    args = dict(source=SRC, inputs={}, symbol="XAUUSDc", timeframe="M5",
                from_ms=T0, to_ms=T0 + 39 * M5, exits=NO_COST)
    return bt.run(conn, **(args | kw))


def test_seeded_bars_give_the_known_trade(conn):
    seed(conn)
    out = run(conn)
    (t,) = out["trades"]
    # fill bar 3 open 100, stop 98, target 102 → bar 8 (px 102) high 103 hits it
    assert (t["side"], t["entry"], t["sl"], t["tp"], t["reason"]) == ("long", 100, 98, 102, "tp")
    assert t["r_gross"] == pytest.approx(1.0)
    assert t["r"] == pytest.approx(1.0 - 10 * 0.01 / 2)            # spread 10 pts
    assert out["segments"]["all"]["n"] == 1 and out["segments"]["oos"]["n"] == 0
    assert out["split_msc"] == T0 + int(0.7 * 39 * M5)
    assert out["counts"]["rejected"] == 0 and out["pending"] is False
    assert len(out["source_hash"]) == 64 and out["computed_ms"] > 0


def test_source_hash_follows_source_and_inputs(conn):
    seed(conn)
    a = run(conn)["source_hash"]
    assert run(conn)["source_hash"] == a
    assert run(conn, inputs={"x": 1})["source_hash"] != a


def test_bar_cap(conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(bt, "MAX_BACKTEST_BARS", 10)
    with pytest.raises(ValueError, match="40 bars"):
        run(conn)


def test_missing_symbol_specs(conn):
    seed(conn)
    conn.execute("DELETE FROM symbol_specs")
    with pytest.raises(ValueError, match="no symbol specs for XAUUSDc"):
        run(conn)


def test_script_without_signal_is_refused(conn):
    seed(conn)
    with pytest.raises(ValueError, match="signal"):
        run(conn, source="plot(close)")


def test_script_error_propagates(conn):
    seed(conn)
    with pytest.raises(ScriptError):
        run(conn, source='signal("long", sma(close, 0) > 1)')


def test_atr_default_warm_up_is_loaded_before_the_window(conn):
    seed(conn, fire=(30,))
    out = run(conn, source='signal("long", volume == 7)', from_ms=T0 + 30 * M5,
              exits={"sl": "atr", "sl_value": 1.0, "atr_len": 2, "tp_r": None})
    (t,) = out["trades"]
    assert t["sl"] == pytest.approx(t["entry"] - 2.0)     # ATR(2) of range-2 bars


def test_missing_warm_up_is_pending(conn):
    seed(conn)
    out = run(conn, source='signal("long", volume == 7 and sma(close, 30) > 0)',
              from_ms=T0 + 5 * M5)
    assert out["pending"] is True and out["trades"] == []


# --- route --------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    path = tmp_path / "j.db"
    c = connect(path)
    seed(c)
    c.close()
    app = create_app(str(path), cache_dir=str(tmp_path / "cache"))
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


BODY = {"source": SRC, "symbol": "XAUUSDc", "timeframe": "M5", "from_ms": T0,
        "to_ms": T0 + 39 * M5, "exits": NO_COST}


def test_route_runs(client):
    r = client.post("/api/indicators/backtest", json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["trades"][0]["reason"] == "tp"


def test_route_script_error_is_400_with_position(client):
    r = client.post("/api/indicators/backtest",
                    json=BODY | {"source": 'signal("long", sma(close, 0) > 1)'})
    assert r.status_code == 400 and r.json()["script_error"]["line"] == 1


@pytest.mark.parametrize("bad", [
    {"split": 1.5}, {"exits": {"sl": "pips"}}, {"exits": {"sl_value": -1}},
    {"exits": {"max_hold": 0}}, {"timeframe": "M7"},
])
def test_route_rejects_bad_settings(client, bad):
    assert client.post("/api/indicators/backtest", json=BODY | bad).status_code in (400, 422)


# --- replay jump (migration 014) ----------------------------------------------

DAY = 86_400_000


def seed_gap(conn):
    """10 bars, a two-day closure, 10 more."""
    times = [T0 + i * M5 for i in range(10)] + [T0 + 2 * DAY + i * M5 for i in range(10)]
    for t in times:
        cs.insert_candle(conn, "XAUUSDc", "M5", Candle(
            time_msc=t, open=100, high=101, low=99, close=100, tick_volume=1, spread=1,
            real_volume=0))
        cs.record_coverage(conn, "XAUUSDc", "M5", t, t)
    conn.commit()
    return times


def test_replay_jump_counts_bars_across_a_gap_and_is_a_study_session(conn):
    t = seed_gap(conn)
    out = bt.replay_session(conn, symbol="XAUUSDc", timeframe="M5", decision_msc=t[11],
                            exit_msc=t[14], lead_bars=3, tail_bars=2)
    s = out["session"]
    assert s["origin"] == "study"
    assert s["cursor_msc"] == s["range_start_msc"] == t[8]       # 3 bars back, over the gap
    assert s["range_end_msc"] == t[16]                            # 2 bars past the exit


def test_replay_jump_clamps_to_the_store_edges(conn):
    t = seed_gap(conn)
    s = bt.replay_session(conn, symbol="XAUUSDc", timeframe="M5", decision_msc=t[1],
                          exit_msc=None, lead_bars=50, tail_bars=20)["session"]
    assert s["cursor_msc"] == t[0] and s["range_end_msc"] == t[-1]


def test_replay_route_and_career_toggle(client):
    r = client.post("/api/indicators/backtest/replay", json={
        "symbol": "XAUUSDc", "timeframe": "M5", "decision_msc": T0 + 10 * M5,
        "exit_msc": T0 + 12 * M5})
    assert r.status_code == 200, r.text
    assert r.json()["session"]["origin"] == "study"
    assert client.get("/api/training/summary").status_code == 200
    assert client.get("/api/training/summary?include_study=true").status_code == 200


@pytest.mark.parametrize("exit_msc", [T0 + 45 * M5, 10**15])
def test_replay_range_never_ends_past_the_last_stored_bar(conn, exit_msc):
    # The store lags (or the exit is the future): no bar follows the exit, so the
    # range ends at the last stored bar, never at a time no bar can fill.
    seed(conn)
    out = bt.replay_session(conn, symbol="XAUUSDc", timeframe="M5", decision_msc=T0 + 20 * M5,
                            exit_msc=exit_msc)
    assert out["session"]["range_end_msc"] == T0 + 39 * M5


def test_replay_with_no_stored_bars_queues_a_fill_but_never_past_now(conn):
    # Pruned or never-filled bars: the session is still created and its fill
    # queued (`journal live` recovers it) — but the range stops at now, so the
    # fill is one that can complete.
    out = bt.replay_session(conn, symbol="XAUUSDc", timeframe="M5", decision_msc=T0,
                            exit_msc=10**15)
    assert out["pending"] is True
    assert T0 < out["session"]["range_end_msc"] <= now_ms()


def test_replay_end_follows_load_bars_when_native_lags_m1(conn):
    # Native H1 stops early; M1 runs on. The range must end where the replay's
    # own reader (load_bars) ends, never before the cursor.
    seed(conn)
    H1 = 3_600_000
    cs.insert_candle(conn, "XAUUSDc", "H1", Candle(
        time_msc=T0 - 10 * H1, open=1, high=2, low=0.5, close=1, tick_volume=1, spread=1,
        real_volume=0))
    for i in range(0, 600):
        cs.insert_candle(conn, "XAUUSDc", "M1", Candle(
            time_msc=T0 + i * 60_000, open=1, high=2, low=0.5, close=1, tick_volume=1, spread=1,
            real_volume=0))
    cs.record_coverage(conn, "XAUUSDc", "M1", T0, T0 + 600 * 60_000)
    conn.commit()
    out = bt.replay_session(conn, symbol="XAUUSDc", timeframe="H1", decision_msc=T0 + 5 * H1,
                            exit_msc=T0 + 9 * H1, lead_bars=2)
    s = out["session"]
    assert s["range_start_msc"] <= s["range_end_msc"] == T0 + 9 * H1
