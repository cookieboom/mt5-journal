"""Tests for SL/TP modification and session statistics tracking."""
import pytest
from journal.web import training
from journal.store.db import connect, now_ms
from journal.store import training_store as ts


@pytest.fixture
def conn():
    """In-memory database with schema."""
    c = connect(":memory:")
    yield c
    c.close()


def _create_test_session(conn):
    """Helper to create a test training session."""
    return ts.create_session(
        conn,
        symbol="EURUSD",
        symbol_base="EURUSD",
        timeframe="M15",
        range_start_msc=1000000,
        range_end_msc=2000000,
        cursor_msc=1000000
    )


def _create_test_position(conn, session_id, sl=0, tp=0, status="open", direction="buy"):
    """Helper to create a test training position."""
    pos_id = ts.insert_position(
        conn,
        session_id=session_id,
        direction=direction,
        volume=0.1,
        decision_msc=1000000,
        sl=sl,
        tp=tp
    )

    if status == "open":
        conn.execute(
            "UPDATE training_positions SET status = 'open', "
            "entry_msc = ?, entry_price = ? WHERE id = ?",
            (1000000, 1.2500, pos_id)
        )
        conn.commit()
    elif status == "closed":
        conn.execute(
            "UPDATE training_positions SET status = 'closed', "
            "entry_msc = ?, entry_price = ?, exit_msc = ?, exit_price = ? WHERE id = ?",
            (1000000, 1.2500, 1100000, 1.2550, pos_id)
        )
        conn.commit()

    return pos_id


# ---------------------------------------------------------------- modify_sltp tests


def test_modify_sltp_updates_sl_only(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=0, tp=0)

    result = training.modify_sltp(conn, pos_id, sl=1.2450, tp=None)

    assert result["sl"] == 1.2450
    assert result["tp"] == 0


def test_modify_sltp_updates_tp_only(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=0)

    result = training.modify_sltp(conn, pos_id, sl=None, tp=1.2650)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


def test_modify_sltp_updates_both(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id)

    result = training.modify_sltp(conn, pos_id, sl=1.2450, tp=1.2650)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


def test_modify_sltp_remove_sl_sets_to_zero(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450)

    result = training.modify_sltp(conn, pos_id, sl=0, tp=None)

    assert result["sl"] == 0


def test_modify_sltp_closed_position_raises(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, status="closed")

    with pytest.raises(ValueError, match="already closed"):
        training.modify_sltp(conn, pos_id, sl=1.2450)


def test_modify_sltp_nonexistent_position_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        training.modify_sltp(conn, 99999, sl=1.2450)


def test_modify_sltp_no_changes_when_both_none(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    result = training.modify_sltp(conn, pos_id, sl=None, tp=None)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


# --------------------------------------------------- direction-sanity validation


def test_modify_sltp_buy_sl_above_entry_raises(conn):
    """Buy: SL must be below entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="buy")  # entry_price=1.2500

    with pytest.raises(ValueError, match="SL"):
        training.modify_sltp(conn, pos_id, sl=1.2600)


def test_modify_sltp_buy_tp_below_entry_raises(conn):
    """Buy: TP must be above entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="buy")

    with pytest.raises(ValueError, match="TP"):
        training.modify_sltp(conn, pos_id, tp=1.2400)


def test_modify_sltp_sell_sl_below_entry_raises(conn):
    """Sell: SL must be above entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="sell")

    with pytest.raises(ValueError, match="SL"):
        training.modify_sltp(conn, pos_id, sl=1.2400)


def test_modify_sltp_sell_tp_above_entry_raises(conn):
    """Sell: TP must be below entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="sell")

    with pytest.raises(ValueError, match="TP"):
        training.modify_sltp(conn, pos_id, tp=1.2600)


def test_modify_sltp_remove_sl_skips_direction_check(conn):
    """Setting sl=0 (remove) must never be rejected by the direction guard."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, direction="buy")

    result = training.modify_sltp(conn, pos_id, sl=0)

    assert result["sl"] == 0


def test_modify_sltp_valid_buy_values_pass(conn):
    """Sanity check: correctly-sided values are accepted (regression guard —
    a too-strict validator would break the happy path already covered above)."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="buy")

    result = training.modify_sltp(conn, pos_id, sl=1.2450, tp=1.2650)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


def test_open_position_buy_sl_above_entry_raises(conn):
    """Same direction guard applies to open_position, not just modify_sltp."""
    session_id = _create_test_session(conn)
    # entry_price is only known after a bar fills it in step(); open_position
    # itself has no entry_price yet, so the guard here checks sl/tp against
    # each other (sl must be < tp for buy) when both are non-zero.
    with pytest.raises(ValueError, match="SL"):
        training.open_position(conn, session_id, direction="buy", volume=0.1,
                                sl=1.2650, tp=1.2450)


# ---------------------------------------------------------------- session stats tests


def test_get_session_stats_initializes_if_missing(conn):
    session_id = _create_test_session(conn)

    stats = training.get_session_stats(conn, session_id)

    assert stats["session_id"] == session_id
    assert stats["total_closed"] == 0
    assert stats["sl_hits"] == 0
    assert stats["tp_hits"] == 0
    assert stats["manual_closes"] == 0
    assert stats["sl_hit_rate"] is None
    assert stats["tp_hit_rate"] is None


def test_get_session_stats_calculates_rates_correctly(conn):
    session_id = _create_test_session(conn)

    conn.execute(
        "INSERT INTO training_session_stats "
        "(session_id, total_closed, sl_hits, tp_hits, manual_closes, updated_at_msc) "
        "VALUES (?, 10, 3, 5, 2, ?)",
        (session_id, now_ms())
    )
    conn.commit()

    stats = training.get_session_stats(conn, session_id)

    assert stats["total_closed"] == 10
    assert stats["sl_hit_rate"] == 0.30
    assert stats["tp_hit_rate"] == 0.50


def test_get_session_stats_with_avg_r(conn):
    session_id = _create_test_session(conn)

    for reason, r in [("sl", -1.0), ("sl", -0.5), ("tp", 2.0), ("tp", 3.0)]:
        pos_id = _create_test_position(conn, session_id, status="pending")
        conn.execute(
            "UPDATE training_positions SET status = 'closed', "
            "exit_reason = ?, r_multiple = ? WHERE id = ?",
            (reason, r, pos_id)
        )
    conn.commit()

    conn.execute(
        "INSERT INTO training_session_stats "
        "(session_id, total_closed, sl_hits, tp_hits, manual_closes, updated_at_msc) "
        "VALUES (?, 4, 2, 2, 0, ?)",
        (session_id, now_ms())
    )
    conn.commit()

    stats = training.get_session_stats(conn, session_id)

    assert stats["avg_r_per_sl"] == -0.75
    assert stats["avg_r_per_tp"] == 2.5


# ---------------------------------------------------------------- integration test


def test_close_position_updates_stats(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    from journal.domain import replay_eval as ev
    state = ev.PositionState(
        id=pos_id, direction="buy", volume=0.1, decision_msc=1000000,
        sl=1.2450, tp=1.2650, status="open",
        entry_msc=1000000, entry_price=1.2500,
        close_requested_msc=None, exit_msc=1100000, exit_price=1.2450,
        exit_reason="sl",
    )

    training._resolve_close(conn, "EURUSD", "M15", state)

    stats = training.get_session_stats(conn, session_id)
    assert stats["total_closed"] == 1
    assert stats["sl_hits"] == 1
    assert stats["tp_hits"] == 0


def test_close_with_tp_reason_increments_tp_counter(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    from journal.domain import replay_eval as ev
    state = ev.PositionState(
        id=pos_id, direction="buy", volume=0.1, decision_msc=1000000,
        sl=1.2450, tp=1.2650, status="open",
        entry_msc=1000000, entry_price=1.2500,
        close_requested_msc=None, exit_msc=1100000, exit_price=1.2650,
        exit_reason="tp",
    )

    training._resolve_close(conn, "EURUSD", "M15", state)

    stats = training.get_session_stats(conn, session_id)
    assert stats["total_closed"] == 1
    assert stats["tp_hits"] == 1


def test_manual_close_increments_manual_counter(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    from journal.domain import replay_eval as ev
    state = ev.PositionState(
        id=pos_id, direction="buy", volume=0.1, decision_msc=1000000,
        sl=1.2450, tp=1.2650, status="open",
        entry_msc=1000000, entry_price=1.2500,
        close_requested_msc=None, exit_msc=1100000, exit_price=1.2550,
        exit_reason="manual",
    )

    training._resolve_close(conn, "EURUSD", "M15", state)

    stats = training.get_session_stats(conn, session_id)
    assert stats["manual_closes"] == 1


def test_end_session_eod_close_updates_stats(conn):
    """end_session closing open positions via EOD must update training_session_stats."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    # Position is open; end_session should close it via EOD and update stats
    training.end_session(conn, session_id)

    stats = training.get_session_stats(conn, session_id)
    assert stats["total_closed"] == 1
    # EOD is not sl or tp, so it goes to manual_closes
    assert stats["manual_closes"] == 1
    assert stats["sl_hits"] == 0
    assert stats["tp_hits"] == 0
