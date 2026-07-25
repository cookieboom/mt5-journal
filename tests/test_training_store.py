"""training_store — pure DB CRUD + §8-gated summaries. No bridge, no MT5."""
from __future__ import annotations

import pytest

from journal.store.db import connect
from journal.store import training_store as ts


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _session(conn):
    return ts.create_session(
        conn, symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M15",
        range_start_msc=1000, range_end_msc=9000, cursor_msc=1000,
    )


def test_session_roundtrip_and_status(conn):
    sid = _session(conn)
    row = ts.get_session(conn, sid)
    assert row["symbol"] == "XAUUSDc" and row["status"] == "active"
    ts.update_cursor(conn, sid, 2000)
    ts.set_session_status(conn, sid, "ended")
    row = ts.get_session(conn, sid)
    assert row["cursor_msc"] == 2000 and row["status"] == "ended"


def test_position_lifecycle(conn):
    sid = _session(conn)
    pid = ts.insert_position(conn, session_id=sid, direction="buy", volume=0.1,
                             decision_msc=1000, sl=3999.0, tp=4002.0)
    assert ts.get_position(conn, pid)["status"] == "pending"
    ts.mark_fill(conn, pid, entry_msc=2000, entry_price=4000.0)
    assert ts.get_position(conn, pid)["status"] == "open"
    ts.mark_close(conn, pid, exit_msc=3000, exit_price=4002.0, exit_reason="tp",
                  net_profit=20.0, r_multiple=2.0, mae=0.5, mfe=2.0,
                  mae_r=0.5, mfe_r=2.0)
    row = ts.get_position(conn, pid)
    assert row["status"] == "closed" and row["exit_reason"] == "tp"
    assert len(ts.active_positions(conn, sid)) == 0


def test_delete_session_cascades_positions(conn):
    sid = _session(conn)
    ts.insert_position(conn, session_id=sid, direction="buy", volume=0.1,
                       decision_msc=1000, sl=0.0, tp=0.0)
    ts.delete_session(conn, sid)
    assert ts.get_session(conn, sid) is None
    assert ts.list_positions(conn, sid) == []


def test_summary_is_section8_gated(conn):
    sid = _session(conn)
    # 3 closed winners → n=3 < 20, so rates/averages are suppressed (null),
    # but total_r and n are always present.
    for _ in range(3):
        pid = ts.insert_position(conn, session_id=sid, direction="buy", volume=0.1,
                                 decision_msc=1000, sl=3999.0, tp=4002.0)
        ts.mark_fill(conn, pid, entry_msc=2000, entry_price=4000.0)
        ts.mark_close(conn, pid, exit_msc=3000, exit_price=4002.0, exit_reason="tp",
                      net_profit=20.0, r_multiple=2.0, mae=0.0, mfe=2.0,
                      mae_r=0.0, mfe_r=2.0)
    s = ts.career_summary(conn)
    assert s["n"] == 3
    assert s["win_rate"] is None and s["avg_r"] is None      # §8: n < 20
    assert abs(s["total_r"] - 6.0) < 1e-9                     # always shown
