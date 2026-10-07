"""web.indicators — the service over stored candles. Pure DB, no bridge.
The replay clip is the server-side half of the no-lookahead guard: a request
carrying a session id never sees a bar the session has not revealed."""
from __future__ import annotations

import pytest

from journal.adapter.base import Candle
from journal.store import candles_store as cs
from journal.store import live_store
from journal.store import training_store as ts
from journal.store.db import connect
from journal.web import indicators as ind

M5 = 300_000
T0 = 1_767_225_600_000           # 2026-01-01 00:00 UTC, a bucket boundary


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def seed(conn, n: int, tf: str = "M5", step: int = M5, start: int = T0) -> list[int]:
    times = [start + i * step for i in range(n)]
    for i, t in enumerate(times):
        px = 100.0 + i
        cs.insert_candle(conn, "XAUUSDc", tf, Candle(
            time_msc=t, open=px, high=px + 1, low=px - 1, close=px,
            tick_volume=10, spread=5, real_volume=0))
    cs.record_coverage(conn, "XAUUSDc", tf, times[0], times[-1])
    conn.commit()
    return times


SMA3 = "plot(sma(close, 3), title='S')"


def test_compute_returns_aligned_times_and_values(conn):
    t = seed(conn, 10)
    out = ind.compute(conn, source=SMA3, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[5], to_ms=t[9])
    assert out["times"] == t[5:10]
    assert out["plots"][0]["values"] == [104.0, 105.0, 106.0, 107.0, 108.0]
    assert out["plots"][0]["title"] == "S" and out["plots"][0]["pane"] == "price"
    assert out["warm_from_msc"] == t[3]          # 3 bars of warm-up needed (sma 3)
    assert out["forming_msc"] is None


def test_warmup_is_loaded_from_before_the_window(conn):
    t = seed(conn, 10)
    out = ind.compute(conn, source=SMA3, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[0], to_ms=t[9])
    assert out["plots"][0]["values"][:2] == [None, None]     # unknown, not 0
    assert out["warm_from_msc"] == t[3]


def test_warmup_crosses_a_market_gap(conn):
    early = seed(conn, 5)
    late = seed(conn, 5, start=early[-1] + 2 * 86_400_000)    # a weekend later
    out = ind.compute(conn, source=SMA3, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=late[0], to_ms=late[-1])
    assert out["plots"][0]["values"][0] is not None


def test_inputs_apply(conn):
    t = seed(conn, 10)
    src = "n = input(3)\nplot(sma(close, n))"
    out = ind.compute(conn, source=src, inputs={"n": 1}, symbol="XAUUSDc",
                      timeframe="M5", from_ms=t[5], to_ms=t[6])
    assert out["plots"][0]["values"] == [105.0, 106.0]


def test_signals_listed_by_time_and_side(conn):
    t = seed(conn, 10)
    src = 'plot(close)\nsignal("long", close > 106)\nsignal("short", close < 101)'
    out = ind.compute(conn, source=src, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[0], to_ms=t[9])
    assert out["signals"] == (
        [{"time_msc": t[0], "side": "short"}]
        + [{"time_msc": x, "side": "long"} for x in t[7:10]]
    )


def test_replay_session_clips_to_the_cursor(conn):
    t = seed(conn, 10)
    sid = ts.create_session(conn, symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M5",
                            range_start_msc=t[0], range_end_msc=t[9], cursor_msc=t[4])
    out = ind.compute(conn, source=SMA3, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[0], to_ms=t[9], session_id=sid, include_forming=True)
    assert out["times"][-1] == t[4]                # nothing past the revealed bar
    assert out["forming_msc"] is None              # forming never leaks into replay


def test_replay_clip_on_a_higher_timeframe_hides_an_unfinished_bar(conn):
    t = seed(conn, 24)                             # M5
    seed(conn, 2, tf="H1", step=3_600_000)         # H1 bars at T0, T0+1h
    sid = ts.create_session(conn, symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M5",
                            range_start_msc=t[0], range_end_msc=t[23], cursor_msc=t[13])
    out = ind.compute(conn, source="plot(close)", inputs={}, symbol="XAUUSDc",
                      timeframe="H1", from_ms=T0, to_ms=T0 + 7_200_000, session_id=sid)
    assert out["times"] == [T0]                    # the 01:00 H1 bar has not closed


def test_unknown_session_is_an_error(conn):
    with pytest.raises(ValueError, match="session"):
        ind.compute(conn, source=SMA3, inputs={}, symbol="XAUUSDc", timeframe="M5",
                    from_ms=0, to_ms=1, session_id=999)


def test_forming_bar_is_appended_and_flagged(conn):
    t = seed(conn, 10)
    forming_t = t[-1] + M5
    live_store.upsert_forming(conn, "XAUUSDc", "M5", Candle(
        time_msc=forming_t, open=110, high=111, low=109, close=110,
        tick_volume=1, spread=5, real_volume=0), now_msc=forming_t + 1)
    out = ind.compute(conn, source=SMA3, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[7], to_ms=forming_t, include_forming=True)
    assert out["times"][-1] == forming_t
    assert out["forming_msc"] == forming_t
    assert out["plots"][0]["values"][-1] == pytest.approx((108 + 109 + 110) / 3)


def test_signals_on_the_forming_bar_are_not_reported(conn):
    t = seed(conn, 3)
    forming_t = t[-1] + M5
    live_store.upsert_forming(conn, "XAUUSDc", "M5", Candle(
        time_msc=forming_t, open=500, high=500, low=500, close=500,
        tick_volume=1, spread=5, real_volume=0), now_msc=forming_t + 1)
    out = ind.compute(conn, source='plot(close)\nsignal("long", close > 400)', inputs={},
                      symbol="XAUUSDc", timeframe="M5", from_ms=t[0], to_ms=forming_t,
                      include_forming=True)
    assert out["signals"] == []


def test_insufficient_history_reports_warm_from_and_queues_a_fill(conn):
    t = seed(conn, 5)
    out = ind.compute(conn, source="plot(sma(close, 50))", inputs={}, symbol="XAUUSDc",
                      timeframe="M5", from_ms=t[0], to_ms=t[4])
    assert out["warm_from_msc"] is None            # never warm in what we have
    assert out["pending"] is True
    assert conn.execute("SELECT COUNT(*) FROM candle_requests").fetchone()[0] == 1


def test_library_script_by_id(conn):
    t = seed(conn, 30)
    out = ind.compute(conn, script="lib:ema", inputs={"length": 5}, symbol="XAUUSDc",
                      timeframe="M5", from_ms=t[20], to_ms=t[29])
    assert out["plots"][0]["title"] == "EMA"


def test_validate_reports_metadata_or_error():
    ok = ind.validate("n = input(14, min=2, title='Len')\nplot(rsi(close, n), pane='rsi')\nhline(70, pane='rsi')")
    assert ok["ok"] is True
    assert ok["inputs"] == [{"name": "n", "kind": "int", "default": 14, "min": 2.0,
                             "max": None, "step": None, "title": "Len"}]
    assert ok["plots"][0]["pane"] == "rsi" and ok["hlines"][0]["value"] == 70.0
    bad = ind.validate("plot(nope)")
    assert bad == {"ok": False, "error": {"line": 1, "col": 6, "message": "undefined name 'nope'"}}


def test_user_scripts_crud(conn):
    a = ind.save_script(conn, None, name="Mine", source=SMA3)
    assert a["id"].startswith("user:")
    ind.save_script(conn, a["id"], name="Mine 2", source=SMA3)
    listed = ind.list_scripts(conn)
    mine = [s for s in listed if s["id"] == a["id"]]
    assert mine[0]["name"] == "Mine 2" and mine[0]["readonly"] is False
    assert any(s["id"] == "lib:ema" and s["readonly"] for s in listed)
    ind.delete_script(conn, a["id"])
    assert all(s["id"] != a["id"] for s in ind.list_scripts(conn))
    b = ind.save_script(conn, None, name="Next", source=SMA3)
    assert b["id"] != a["id"]                       # ids are never reused


def test_saving_an_invalid_script_is_refused(conn):
    from journal.domain.indicators.lang import ScriptError
    with pytest.raises(ScriptError):
        ind.save_script(conn, None, name="Bad", source="plot(nope)")


def test_library_scripts_are_read_only(conn):
    with pytest.raises(ValueError, match="read-only"):
        ind.save_script(conn, "lib:ema", name="x", source=SMA3)
    with pytest.raises(ValueError, match="read-only"):
        ind.delete_script(conn, "lib:ema")


def test_compute_a_user_script_by_id(conn):
    t = seed(conn, 10)
    a = ind.save_script(conn, None, name="Mine", source=SMA3)
    out = ind.compute(conn, script=a["id"], inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[5], to_ms=t[6])
    assert out["plots"][0]["values"] == [104.0, 105.0]


# --- tf(): higher-timeframe bars, closed only (spec 2026-10-07-indicators-mtf) ---

H1 = 3_600_000
TF_CLOSE = 'plot(tf("H1", close))'


def test_tf_steps_once_per_closed_htf_bar(conn):
    t = seed(conn, 36)                              # M5 00:00 .. 02:55
    seed(conn, 3, tf="H1", step=H1)                 # H1 closes 100, 101, 102
    out = ind.compute(conn, source=TF_CLOSE, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[0], to_ms=t[35])
    v = out["plots"][0]["values"]
    assert v[:11] == [None] * 11                    # H1 00:00 not closed before 00:55's close
    assert v[11:23] == [100.0] * 12 and v[23:35] == [101.0] * 12 and v[35] == 102.0


def test_tf_in_replay_never_shows_the_unfinished_htf_bar(conn):
    t = seed(conn, 36)
    seed(conn, 3, tf="H1", step=H1)
    sid = ts.create_session(conn, symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M5",
                            range_start_msc=t[0], range_end_msc=t[35], cursor_msc=t[17])
    out = ind.compute(conn, source=TF_CLOSE, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[0], to_ms=t[35], session_id=sid)
    assert out["times"][-1] == t[17]
    assert out["plots"][0]["values"][-1] == 100.0   # 01:25: the 01:00 H1 bar is still forming


def test_tf_on_the_forming_chart_bar_waits_for_the_htf_close(conn):
    t = seed(conn, 11)                              # 00:00 .. 00:50
    seed(conn, 1, tf="H1", step=H1)
    forming_t = t[-1] + M5                          # 00:55, closes with H1 00:00
    live_store.upsert_forming(conn, "XAUUSDc", "M5", Candle(
        time_msc=forming_t, open=110, high=111, low=109, close=110,
        tick_volume=1, spread=5, real_volume=0), now_msc=forming_t + 1)
    out = ind.compute(conn, source=TF_CLOSE, inputs={}, symbol="XAUUSDc", timeframe="M5",
                      from_ms=t[0], to_ms=forming_t, include_forming=True)
    assert out["forming_msc"] == forming_t and out["plots"][0]["values"][-1] is None


def test_tf_warm_from_waits_for_the_htf_warmup(conn):
    t = seed(conn, 36)
    seed(conn, 3, tf="H1", step=H1)
    out = ind.compute(conn, source='plot(tf("H1", sma(close, 2)))', inputs={},
                      symbol="XAUUSDc", timeframe="M5", from_ms=t[0], to_ms=t[35])
    assert out["warm_from_msc"] == t[35]   # same rule as the chart TF: bar [lookback], H1 02:00
    assert out["plots"][0]["values"][23] == 100.5


def test_tf_missing_htf_history_queues_a_fill(conn):
    t = seed(conn, 36)
    out = ind.compute(conn, source='plot(tf("H1", sma(close, 2)))', inputs={},
                      symbol="XAUUSDc", timeframe="M5", from_ms=t[0], to_ms=t[35])
    assert out["pending"] is True and out["warm_from_msc"] is None
    tfs = [r[0] for r in conn.execute("SELECT timeframe FROM candle_requests")]
    assert "H1" in tfs


def test_tf_lower_than_the_chart_is_a_script_error(conn):
    t = seed(conn, 3, tf="H1", step=H1)
    with pytest.raises(ind.ScriptError, match="higher timeframes"):
        ind.compute(conn, source='plot(tf("M5", close))', inputs={}, symbol="XAUUSDc",
                    timeframe="H1", from_ms=t[0], to_ms=t[2])


def test_validate_lookback_is_none_where_tf_does_not_apply():
    out = ind.validate('plot(tf("H1", ema(close, 50)))')
    assert out["lookback"] == {"M1": 60, "M5": 12, "M15": 4, "H1": None, "H4": None, "D1": None}
