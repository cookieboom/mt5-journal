"""web.trade_context — my trades split by whether the active script's same-side
signal fired in the N closed bars before them (spec 2026-10-08-indicators-
trade-context §2 A). Real rows §8-gated via `bucket_stat`; replay/paper ungated."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from journal.adapter.base import Candle
from journal.domain.indicators.lang import ScriptError
from journal.store import candles_store as cs
from journal.store import training_store
from journal.store.db import connect
from journal.web import backtest as bt
from journal.web import trade_context as tc
from journal.web.app import create_app

M5 = 300_000
T0 = 1_767_225_600_000           # 2026-01-01 00:00 UTC
# long fires on bar 10 (volume 7), short on bar 20 (volume 8).
SRC = 'signal("long", volume == 7)\nsignal("short", volume == 8)'


def at(bar_close: int) -> int:
    """Epoch ms just after bar `bar_close` closed (inside the next bar)."""
    return T0 + (bar_close + 1) * M5 + 1_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    for i in range(40):
        cs.insert_candle(c, "XAUUSDc", "M5", Candle(
            time_msc=T0 + i * M5, open=100.0, high=101.0, low=99.0, close=100.0,
            tick_volume={10: 7, 20: 8}.get(i, 1), spread=10, real_volume=0))
    cs.record_coverage(c, "XAUUSDc", "M5", T0, T0 + 39 * M5)
    c.execute("INSERT INTO accounts (login, currency, first_seen_at) VALUES (1, 'USC', 0)")
    c.commit()
    yield c
    c.close()


_pid = iter(range(1, 10_000))


def real(conn, direction, ref, net, r=None, symbol="XAUUSDc", status="closed"):
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, direction, "
        "status, open_time_msc, volume, open_price, net_profit, r_multiple, rebuilt_at) "
        "VALUES (1, ?, ?, 'XAUUSD', ?, ?, ?, 0.01, 100, ?, ?, 0)",
        (next(_pid), symbol, direction, status, ref, net, r))
    conn.commit()


def replay(conn, direction, ref, net, r=None, origin="blind"):
    sid = training_store.create_session(
        conn, symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M5",
        range_start_msc=T0, range_end_msc=T0 + 39 * M5, cursor_msc=T0, origin=origin)
    conn.execute(
        "INSERT INTO training_positions (session_id, direction, volume, decision_msc, "
        "status, net_profit, r_multiple, created_at_msc) VALUES (?, ?, 0.01, ?, 'closed', ?, ?, 0)",
        (sid, direction, ref, net, r))
    conn.commit()


def paper(conn, direction, ref, net, r=None):
    conn.execute("INSERT OR IGNORE INTO paper_accounts (id, name, initial_balance, balance, "
                 "leverage, stopout_pct, created_at_msc) VALUES (1, 'p', 1000, 1000, 500, 50, 0)")
    conn.execute(
        "INSERT INTO paper_positions (account_id, symbol, symbol_base, direction, order_kind, "
        "volume, status, requested_msc, net_profit, r_multiple, created_at_msc) "
        "VALUES (1, 'XAUUSDc', 'XAUUSD', ?, 'market', 0.01, 'closed', ?, ?, ?, 0)",
        (direction, ref, net, r))
    conn.commit()


def run(conn, **kw):
    args = dict(source=SRC, inputs={}, symbol="XAUUSDc", timeframe="M5", window=3)
    return tc.run(conn, **(args | kw))


def test_real_trades_split_on_same_side_signal(conn):
    real(conn, "buy", at(11), 50.0, 1.0)     # window bars 9..11 holds the long fire → true
    real(conn, "buy", at(30), -20.0, -1.0)   # bars 28..30, nothing → false
    real(conn, "sell", at(21), 30.0, 0.5)    # bars 19..21 holds the short fire → true
    real(conn, "sell", at(12), -10.0)        # the long fire is not agreement for a sell
    s = run(conn)["sources"]["real"]
    assert (s["true"]["n"], s["false"]["n"], s["n_unknown"]) == (2, 2, 0)
    assert s["true"]["n_r"] == 2 and s["false"]["n_r"] == 1


def test_real_cells_are_gated_below_20(conn):
    real(conn, "buy", at(11), 50.0, 1.0)
    cell = run(conn)["sources"]["real"]["true"]
    assert cell["gated"] is True
    assert cell["win_rate"] is None and cell["avg_r"] is None and cell["total_r"] is None


def test_real_cells_open_at_20(conn):
    for _ in range(20):
        real(conn, "buy", at(11), 50.0, 1.0)
    cell = run(conn)["sources"]["real"]["true"]
    assert cell["n"] == 20 and cell["win_rate"] == pytest.approx(1.0)
    assert cell["avg_r"] == pytest.approx(1.0) and cell["total_r"] == pytest.approx(20.0)


def test_win_rate_and_r_gate_on_their_own_n(conn):
    for i in range(20):                     # 20 closed, only 5 with a known SL
        real(conn, "buy", at(11), 50.0 if i % 2 else -50.0, 1.0 if i < 5 else None)
    cell = run(conn)["sources"]["real"]["true"]
    assert cell["win_rate"] == pytest.approx(0.5)
    assert cell["n_r"] == 5 and cell["avg_r"] is None and cell["total_r"] is None


def test_too_few_bars_before_the_trade_is_unknown(conn):
    real(conn, "buy", at(0), 10.0)          # only bar 0 has closed: window 3 can't fill
    s = run(conn)["sources"]["real"]
    assert s["n_unknown"] == 1 and s["true"]["n"] == s["false"]["n"] == 0


def test_signal_before_warm_up_is_unknown(conn):
    # ema(close, 5) needs 20 bars of warm-up; the first trade sits before that and
    # is not trusted even though its raw signal fires (1.0 on bar 10); the second is.
    src = 'x = ema(close, 5)\nsignal("long", volume == 7 and x > 0)'
    real(conn, "buy", at(11), 10.0)
    real(conn, "buy", at(35), 10.0)
    s = run(conn, source=src)["sources"]["real"]
    assert s["n_unknown"] == 1 and s["false"]["n"] == 1


def test_other_symbols_open_trades_and_other_accounts_are_ignored(conn):
    real(conn, "buy", at(11), 10.0, symbol="BTCUSDc")
    real(conn, "buy", at(11), 10.0, status="open")
    s = run(conn)["sources"]["real"]
    assert s["true"]["n"] + s["false"]["n"] + s["n_unknown"] == 0


def test_replay_is_ungated_and_excludes_study_sessions(conn):
    replay(conn, "buy", at(11), 40.0, 2.0)
    replay(conn, "buy", at(11), 40.0, 2.0, origin="study")
    s = run(conn)["sources"]["replay"]
    assert s["true"] == {"n": 1, "n_r": 1, "win_rate": 1.0, "avg_r": 2.0, "total_r": 2.0,
                         "gated": False}


def test_paper_is_ungated(conn):
    paper(conn, "sell", at(21), -5.0)
    s = run(conn)["sources"]["paper"]
    assert s["true"]["n"] == 1 and s["true"]["win_rate"] == 0.0
    assert s["true"]["avg_r"] is None and s["true"]["gated"] is False
    assert s["true"]["total_r"] is None          # no known R: unknown, not 0 (rule 4)


def test_empty_store_gives_zero_counts(conn):
    out = run(conn)
    for src in ("real", "replay", "paper"):
        assert out["sources"][src]["true"]["n"] == 0 and out["sources"][src]["n_unknown"] == 0
    assert out["window"] == 3 and len(out["source_hash"]) == 64


def test_window_widens_the_net(conn):
    real(conn, "buy", at(14), 10.0)          # fire on bar 10, 5 bars back
    assert run(conn)["sources"]["real"]["false"]["n"] == 1
    assert run(conn, window=5)["sources"]["real"]["true"]["n"] == 1


def test_script_without_signal_is_refused(conn):
    with pytest.raises(ValueError, match="no signal"):
        run(conn, source="plot(close)")


def test_script_error_raises(conn):
    with pytest.raises(ScriptError):
        run(conn, source="signal(")


def test_bar_cap(conn, monkeypatch):
    real(conn, "buy", at(11), 10.0)
    real(conn, "buy", at(35), 10.0)
    monkeypatch.setattr(bt, "MAX_BACKTEST_BARS", 5)
    with pytest.raises(ValueError, match="limit"):
        run(conn)


# --- route ---------------------------------------------------------------------

def test_route(conn, tmp_path):
    real(conn, "buy", at(11), 10.0)
    client = TestClient(create_app(str(tmp_path / "journal.db"), cache_dir=str(tmp_path / "cache")),
                        base_url="http://127.0.0.1", raise_server_exceptions=False)
    body = {"source": SRC, "symbol": "XAUUSDc", "timeframe": "M5"}
    r = client.post("/api/indicators/context", json=body)
    assert r.status_code == 200 and r.json()["sources"]["real"]["true"]["n"] == 1
    assert r.json()["window"] == 3
    r = client.post("/api/indicators/context", json=body | {"source": "signal("})
    assert r.status_code == 400 and "script_error" in r.json()
    r = client.post("/api/indicators/context", json=body | {"window": 0})
    assert r.status_code == 422
    r = client.post("/api/indicators/context", json=body | {"timeframe": "X9"})
    assert r.status_code == 400


def test_bars_the_market_had_but_the_frame_lacks_make_it_unknown(conn):
    # The M5 store stops at bar 39; a trade two bars later reads a quiet window.
    real(conn, "buy", at(41), 10.0)
    assert run(conn)["sources"]["real"]["false"]["n"] == 1
    # M1 shows the market traded through bars 40 and 41: that window is stale.
    for i in (40, 41):
        cs.insert_candle(conn, "XAUUSDc", "M1", Candle(
            time_msc=T0 + i * M5, open=100.0, high=101.0, low=99.0, close=100.0,
            tick_volume=1, spread=10, real_volume=0))
    conn.commit()
    s = run(conn)["sources"]["real"]
    assert s["n_unknown"] == 1 and s["false"]["n"] == 0


def test_m1_bars_the_frame_has_change_nothing(conn):
    real(conn, "buy", at(11), 10.0)
    for i in range(40):
        cs.insert_candle(conn, "XAUUSDc", "M1", Candle(
            time_msc=T0 + i * M5 + 60_000, open=100.0, high=101.0, low=99.0, close=100.0,
            tick_volume=1, spread=10, real_volume=0))
    conn.commit()
    assert run(conn)["sources"]["real"]["true"]["n"] == 1
