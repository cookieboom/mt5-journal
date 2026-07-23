"""M9 Phase 3 — the trade_commands queue (`execute.py`).

Written before the implementation (CLAUDE.md rule 7).

This is the seam that lets the web ask for an order without ever touching the
bridge: the web INSERTs an intent, `journal live` claims it and sends it. The
properties that matter here are not about MT5 at all — they are about a queue
that must never execute the same order twice and must never lose the record of
one it did execute.
"""

from __future__ import annotations

import pytest

from journal.adapter.base import TradeResult, TradeRetcode
from journal.domain.commands import CommandError
from journal.execute import (
    claim_next,
    enqueue,
    get_command,
    list_commands,
    record_result,
    recover_interrupted,
    reject,
)
from journal.store.db import connect

_LOGIN = 257223861


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "j.db")
    c.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, 'USC', 1)",
        (_LOGIN,),
    )
    c.execute(
        """INSERT INTO symbol_specs
           (symbol, symbol_base, digits, point, fetched_at,
            volume_min, volume_max, volume_step, stops_level, trade_mode, filling_mode)
           VALUES ('XAUUSDc','XAUUSD',3,0.001,1, 0.01,200.0,0.01,0,4,3)"""
    )
    c.execute(
        """INSERT INTO open_positions
           (account_login, position_id, symbol, symbol_base, direction, volume,
            open_price, price_current, sl, tp, observed_msc)
           VALUES (?, 111, 'XAUUSDc','XAUUSD','buy', 0.10, 3300.0, 3310.0, 0.0, 0.0, 5)""",
        (_LOGIN,),
    )
    c.commit()
    yield c
    c.close()


# ------------------------------------------------------------------- enqueue


def test_enqueue_writes_one_pending_row(conn):
    cmd_id = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0)
    row = get_command(conn, cmd_id)
    assert row["status"] == "pending"
    assert row["kind"] == "modify_sltp"
    assert row["position_id"] == 111
    assert row["sl"] == 3300.0
    # Rule 4: nothing has happened yet, so every outcome column is unknown.
    assert row["retcode"] is None
    assert row["completed_msc"] is None


def test_enqueue_validates_and_writes_nothing_when_refused(conn):
    """A refused command is not a queue entry nobody will ever run — the human
    is told immediately and the table stays clean. (The 'rejected' STATUS exists
    for the different case where state changed between enqueue and claim; see
    `reject`.)"""
    with pytest.raises(CommandError):
        enqueue(conn, _LOGIN, "add_volume", 111, volume=99.0)
    assert conn.execute("SELECT count(*) FROM trade_commands").fetchone()[0] == 0


def test_enqueue_refuses_a_position_that_is_not_open(conn):
    """Not in `open_positions` means it closed, or was never seen. Either way
    there is nothing to act on, and sending would be acting on a stale view."""
    with pytest.raises(CommandError, match="tidak|not"):
        enqueue(conn, _LOGIN, "close", 999)


def test_enqueue_refuses_when_the_symbol_spec_is_missing(conn):
    """Cannot validate a lot size against a spec that isn't there (rule 4)."""
    conn.execute("DELETE FROM symbol_specs")
    with pytest.raises(CommandError):
        enqueue(conn, _LOGIN, "add_volume", 111, volume=0.01)


def test_enqueue_records_the_requested_time(conn):
    cmd_id = enqueue(conn, _LOGIN, "close", 111)
    assert get_command(conn, cmd_id)["requested_msc"] > 0


def test_enqueue_preserves_the_none_vs_zero_distinction(conn):
    """The whole rule-4 chain is only as strong as its weakest link, and the DB
    is a link: a NULL sl must stay NULL, not become 0.0."""
    cmd_id = enqueue(conn, _LOGIN, "modify_sltp", 111, tp=3320.0)
    row = get_command(conn, cmd_id)
    assert row["sl"] is None
    assert row["tp"] == 3320.0

    cmd_id2 = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=0.0)
    assert get_command(conn, cmd_id2)["sl"] == 0.0


# --------------------------------------------------------------------- claim


def test_claim_next_returns_the_oldest_pending(conn):
    first = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0)
    enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3301.0)
    claimed = claim_next(conn, _LOGIN)
    assert claimed["id"] == first


def test_claim_next_marks_it_claimed(conn):
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    row = get_command(conn, claimed["id"])
    assert row["status"] == "claimed"
    assert row["claimed_msc"] is not None


def test_claim_next_returns_none_when_nothing_is_pending(conn):
    assert claim_next(conn, _LOGIN) is None


def test_a_command_can_only_be_claimed_once(conn):
    """THE property of this queue. Two claims of the same row means one order
    sent twice — real money, twice. The UPDATE's rowcount is the lock."""
    enqueue(conn, _LOGIN, "close", 111)
    first = claim_next(conn, _LOGIN)
    second = claim_next(conn, _LOGIN)
    assert first is not None
    assert second is None


def test_claiming_skips_finished_commands(conn):
    cmd_id = enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    record_result(conn, claimed["id"], TradeResult(retcode=TradeRetcode.DONE))
    assert claim_next(conn, _LOGIN) is None
    assert get_command(conn, cmd_id)["status"] == "done"


# -------------------------------------------------------------------- result


def test_record_result_writes_the_broker_verdict(conn):
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    record_result(
        conn, claimed["id"],
        TradeResult(retcode=TradeRetcode.DONE, deal=777, order=888,
                    volume=0.10, price=3311.0, comment="Request executed"),
    )
    row = get_command(conn, claimed["id"])
    assert row["status"] == "done"
    assert row["retcode"] == 10009
    assert row["result_deal"] == 777
    assert row["result_volume"] == 0.10
    assert row["broker_comment"] == "Request executed"
    assert row["completed_msc"] is not None


def test_record_result_stores_the_actual_partial_fill(conn):
    """A DONE_PARTIAL filled less than asked. Recording the requested volume
    instead would make the journal describe a position that does not exist."""
    enqueue(conn, _LOGIN, "close_partial", 111, volume=0.06)
    claimed = claim_next(conn, _LOGIN)
    record_result(conn, claimed["id"], TradeResult(retcode=TradeRetcode.DONE_PARTIAL, volume=0.02))
    row = get_command(conn, claimed["id"])
    assert row["status"] == "done"      # it DID change the account
    assert row["volume"] == 0.06        # what was asked, untouched
    assert row["result_volume"] == 0.02  # what actually happened


def test_record_result_marks_a_rejection_failed(conn):
    enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0)
    claimed = claim_next(conn, _LOGIN)
    record_result(conn, claimed["id"], TradeResult(retcode=TradeRetcode.INVALID_STOPS))
    row = get_command(conn, claimed["id"])
    assert row["status"] == "failed"
    assert row["retcode"] == 10016


def test_record_result_never_rewrites_the_intent(conn):
    """The audit log's whole value. If a result write could touch kind/sl/tp/
    volume, the record of what was ASKED FOR becomes a record of what
    happened — and the two differing is exactly what you go to the log to find
    out."""
    cmd_id = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0, tp=3320.0)
    before = dict(get_command(conn, cmd_id))
    claimed = claim_next(conn, _LOGIN)
    record_result(conn, claimed["id"], TradeResult(retcode=TradeRetcode.DONE, price=9999.0))
    after = get_command(conn, cmd_id)
    for field in ("kind", "sl", "tp", "volume", "position_id", "requested_msc"):
        assert after[field] == before[field], field


def test_record_result_keeps_the_raw_dump(conn):
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    record_result(
        conn, claimed["id"],
        TradeResult(retcode=TradeRetcode.DONE, raw={"retcode": 10009, "future_field": 1}),
    )
    assert "future_field" in get_command(conn, claimed["id"])["raw_json"]


# -------------------------------------------------------------------- reject


def test_reject_marks_it_without_sending(conn):
    """For a command that validated at enqueue time but no longer does when the
    executor picks it up — the position closed in between, say."""
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    reject(conn, claimed["id"], "posisi sudah tertutup")
    row = get_command(conn, claimed["id"])
    assert row["status"] == "rejected"
    assert "tertutup" in row["error"]
    assert row["retcode"] is None      # never reached the broker


# ------------------------------------------------------------------ recovery


def test_recover_interrupted_fails_orphans_without_retrying(conn):
    """A process that died between claiming and recording leaves a row whose
    order MAY have reached the broker. It is marked failed with an explanation
    and is NEVER auto-retried — re-sending an order that might already exist is
    how you end up with two."""
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)

    n = recover_interrupted(conn, _LOGIN)
    assert n == 1
    row = get_command(conn, claimed["id"])
    assert row["status"] == "failed"
    assert "MT5" in row["error"]       # tells the human to go look
    # Not back in the queue.
    assert claim_next(conn, _LOGIN) is None


def test_recover_interrupted_leaves_pending_rows_alone(conn):
    """A pending row was never sent, so it is not an orphan — it is work."""
    enqueue(conn, _LOGIN, "close", 111)
    assert recover_interrupted(conn, _LOGIN) == 0
    assert claim_next(conn, _LOGIN) is not None


def test_recover_interrupted_leaves_finished_rows_alone(conn):
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    record_result(conn, claimed["id"], TradeResult(retcode=TradeRetcode.DONE))
    assert recover_interrupted(conn, _LOGIN) == 0
    assert get_command(conn, claimed["id"])["status"] == "done"


# -------------------------------------------------------------------- listing


def test_list_commands_is_newest_first(conn):
    a = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0)
    b = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3301.0)
    rows = list_commands(conn, _LOGIN)
    assert [r["id"] for r in rows] == [b, a]


def test_list_commands_respects_a_limit(conn):
    for i in range(5):
        enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0 + i)
    assert len(list_commands(conn, _LOGIN, limit=2)) == 2
