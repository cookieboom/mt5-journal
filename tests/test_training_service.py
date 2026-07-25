"""training service — orchestration over cached candles + pure evaluator.
Seeds candles directly (no bridge). Verifies fills, TP resolution, USC P&L, R,
and eod handling end to end."""
from __future__ import annotations

import pytest

from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candles_store as cs
from journal.web import training as tr


def _seed_specs(conn):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, tick_size, tick_value, "
        "contract_size, fetched_at) VALUES ('XAUUSDc','XAUUSD',0.001,0.1,1.0,1)"
    )
    conn.commit()


def _seed_m15(conn, bars):
    """bars: list of (t, o, h, l, c). Records coverage so load_bars reads native."""
    for t, o, h, l, c in bars:
        cs.insert_candle(conn, "XAUUSDc", "M15",
                         Candle(time_msc=t, open=o, high=h, low=l, close=c,
                                tick_volume=1, spread=0, real_volume=0))
    cs.record_coverage(conn, "XAUUSDc", "M15", bars[0][0], bars[-1][0])
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def test_open_step_take_profit_pnl_and_r(conn):
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000  # M15 ms
    bars = [
        (t0,          4000, 4000, 4000, 4000),
        (t0 + tf,     4000, 4001, 3999, 4000),   # fill bar (open 4000)
        (t0 + 2 * tf, 4001, 4003, 4000, 4002),   # high 4003 >= tp 4002 → TP
    ]
    _seed_m15(conn, bars)
    created = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                range_start_msc=t0, range_end_msc=t0 + 2 * tf,
                                cursor_start_msc=t0)
    sid = created["session"]["id"]
    # Decide at the first bar (cursor = t0), SL 3998, TP 4002.
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=3998.0, tp=4002.0)
    tr.step(conn, sid, 1)   # reveal fill bar → fills at 4000
    out = tr.step(conn, sid, 1)   # reveal TP bar → closes at 4002
    pos = out["positions"][0]
    assert pos["status"] == "closed" and pos["exit_reason"] == "tp"
    assert abs(pos["exit_price"] - 4002.0) < 1e-9
    # +2.0 move * (1/0.001) ticks * 0.1 tick_value * 0.1 vol = 20 USC.
    assert abs(pos["net_profit"] - 20.0) < 1e-9
    # R = 2.0 / |4000 - 3998| = 1.0.
    assert abs(pos["r_multiple"] - 1.0) < 1e-9


def test_no_sl_has_null_r_but_has_pnl(conn):
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000
    _seed_m15(conn, [
        (t0,      4000, 4000, 4000, 4000),
        (t0 + tf, 4000, 4000, 4000, 4000),
        (t0 + 2 * tf, 4005, 4005, 4005, 4005),
    ])
    created = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                range_start_msc=t0, range_end_msc=t0 + 2 * tf,
                                cursor_start_msc=t0)
    sid = created["session"]["id"]
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=0.0, tp=0.0)
    tr.step(conn, sid, 1)               # fills at 4000
    tr.step(conn, sid, 1)               # runs to end, still open
    out = tr.end_session(conn, sid)
    pos = out["positions"][0]
    assert pos["exit_reason"] == "eod" and pos["r_multiple"] is None
    assert pos["net_profit"] is None    # unresolved → excluded from stats


def test_create_rejects_bad_timeframe(conn):
    with pytest.raises(ValueError):
        tr.create_session(conn, symbol="XAUUSDc", timeframe="M7",
                          range_start_msc=1, range_end_msc=2, cursor_start_msc=1)


def test_close_position_rejects_foreign_position(conn):
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000
    _seed_m15(conn, [
        (t0,      4000, 4000, 4000, 4000),
        (t0 + tf, 4000, 4000, 4000, 4000),
    ])
    a = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                          range_start_msc=t0, range_end_msc=t0 + tf, cursor_start_msc=t0)["session"]["id"]
    b = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                          range_start_msc=t0, range_end_msc=t0 + tf, cursor_start_msc=t0)["session"]["id"]
    pos = tr.open_position(conn, a, direction="buy", volume=0.1, sl=0.0, tp=0.0)
    with pytest.raises(ValueError):
        tr.close_position(conn, b, pos["id"])   # position belongs to a, not b


def test_step_spans_market_gap(conn):
    """Locks Fix 1: a weekend/holiday gap between revealed bars must NOT be
    mistaken for end-of-range. The reveal window widens past the gap; the cursor
    advances to the post-gap bars and the position fills/evaluates against them
    instead of being stranded open with the cursor slammed to range_end."""
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000  # M15 ms
    # Two bars, then a ~5-day gap (500 buckets), then two more bars.
    bars = [
        (t0,            4000, 4001, 3999, 4000),
        (t0 + tf,       4000, 4001, 3999, 4000),   # fill bar (open 4000)
        (t0 + 500 * tf, 4001, 4020, 4000, 4015),   # post-gap: high 4020 >= tp 4010 → TP
        (t0 + 501 * tf, 4015, 4016, 4014, 4015),
    ]
    _seed_m15(conn, bars)
    created = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                range_start_msc=t0, range_end_msc=t0 + 501 * tf,
                                cursor_start_msc=t0)
    sid = created["session"]["id"]
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=3990.0, tp=4010.0)

    out1 = tr.step(conn, sid, 1)   # reveal fill bar → fills at 4000
    # The cursor must NOT have jumped to range_end here (that is the bug).
    assert out1["cursor_msc"] == t0 + tf
    assert out1["positions"][0]["status"] == "open"

    out2 = tr.step(conn, sid, 1)   # must span the gap, reveal the post-gap TP bar
    assert out2["cursor_msc"] == t0 + 500 * tf   # advanced across the gap, not to range_end
    pos = out2["positions"][0]
    assert pos["status"] == "closed" and pos["exit_reason"] == "tp"
    assert abs(pos["exit_price"] - 4010.0) < 1e-9


def test_session_summary_counts_no_sl_resolved_trade(conn):
    """Locks Fix 2 (+T4): session_summary is net_profit-based and §8-gated, so a
    RESOLVED no-SL trade (net_profit set, r_multiple NULL) counts toward session
    `n` exactly like the career summary — the two definitions must agree."""
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000
    bars = [
        (t0,          4000, 4000, 4000, 4000),
        (t0 + tf,     4000, 4000, 4000, 4000),   # fill bar (open 4000) — no hits
        (t0 + 2 * tf, 4001, 4006, 4000, 4005),   # high 4006 → both TPs hit
    ]
    _seed_m15(conn, bars)
    created = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                range_start_msc=t0, range_end_msc=t0 + 2 * tf,
                                cursor_start_msc=t0)
    sid = created["session"]["id"]
    # A: NO SL (sl=0 → r_multiple NULL, but net_profit set). B: with SL.
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=0.0, tp=4005.0)
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=3998.0, tp=4002.0)
    tr.step(conn, sid, 1)   # both fill at 4000
    tr.step(conn, sid, 1)   # both close at their TP

    view = tr.session_view(conn, sid)
    statuses = {p["status"] for p in view["positions"]}
    assert statuses == {"closed"}
    # r_multiple set only for the SL trade; net_profit set for BOTH.
    r_vals = [p["r_multiple"] for p in view["positions"]]
    assert None in r_vals and any(v is not None for v in r_vals)
    # session_summary is net_profit-based: BOTH resolved trades count.
    assert view["summary"]["n"] == 2
