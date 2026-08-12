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

from journal.adapter.base import Candle, TradeResult, TradeRetcode
from journal.domain.commands import CommandError
from journal.execute import (
    claim_next,
    enqueue,
    enqueue_open,
    expire_stale,
    get_command,
    list_commands,
    load_open_context,
    record_result,
    recover_interrupted,
    reject,
)
from journal.store import live_store
from journal.store.db import connect, now_ms

_LOGIN = 1_000_001  # placeholder, never the real login (rule 10)


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


# ---------------------------------------------------------------------- open


def _seed_open(conn, *, balance=100_000.0, trade_mode=4, beat_age_ms=0):
    # A beating heartbeat is part of the baseline: an open is only ever placed
    # while `journal live` is running, so every open test seeds one. Pass
    # `beat_age_ms=None` to seed no heartbeat at all.
    if beat_age_ms is not None:
        live_store.beat(conn, now_ms() - beat_age_ms)
    conn.execute(
        "INSERT OR REPLACE INTO accounts (login, currency, balance, margin_mode, "
        "first_seen_at) VALUES (?, 'USC', ?, 2, 1)", (_LOGIN, balance),
    )
    conn.execute(
        "INSERT OR REPLACE INTO symbol_specs (symbol, symbol_base, digits, point, "
        "tick_size, tick_value, fetched_at, volume_min, volume_max, volume_step, "
        "stops_level, freeze_level, trade_mode, filling_mode) VALUES "
        "('XAUUSDc', 'XAUUSD', 3, 0.001, 0.001, 0.1, 1, 0.01, 200.0, 0.01, 0, 0, ?, 3)",
        (trade_mode,),
    )
    conn.commit()


def test_enqueue_open_writes_one_pending_row(conn):
    _seed_open(conn)
    cmd_id = enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                          sl=4030.0, tp=4045.0, volume=0.10, price_ref=4035.0)
    row = get_command(conn, cmd_id)
    assert row["kind"] == "open"
    assert row["status"] == "pending"
    assert row["position_id"] is None
    assert row["symbol"] == "XAUUSDc"
    assert row["direction"] == "buy"
    assert abs(row["price_ref"] - 4035.0) < 1e-9
    assert abs(row["sl"] - 4030.0) < 1e-9
    assert abs(row["tp"] - 4045.0) < 1e-9
    assert abs(row["volume"] - 0.10) < 1e-9


def test_a_refused_open_writes_nothing(conn):
    # 50 USC risk against a 100 USC balance is 50% — far over the ceiling.
    _seed_open(conn, balance=100.0)
    with pytest.raises(CommandError):
        enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                     sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    n = conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0]
    assert n == 0


def test_an_open_on_an_unknown_symbol_is_refused(conn):
    _seed_open(conn)
    with pytest.raises(CommandError, match="sync|spesifikasi|Spesifikasi"):
        enqueue_open(conn, _LOGIN, symbol="GBPUSDc", direction="buy",
                     sl=1.2, tp=0.0, volume=0.10, price_ref=1.25)


# ------------------------------------------------------- open: feed freshness
#
# The lot is derived from `price_ref`, so a reference price the server cannot
# vouch for silently resizes the order: 0.10 lot sized against a 4035 close with
# a 4030 stop is 50 USC of intended risk, but if the market has moved to 4060
# unseen, the same command puts ~300 USC at stake. The frontend already refuses
# to arm the button (`lib/candles.staleEntryReason`); these pin the same refusal
# on the server, which is what actually writes the row.


def _watch(conn, symbol, timeframe, *, expires_in_ms, updated_age_ms):
    now = now_ms()
    live_store.upsert_watch(conn, symbol, timeframe, now, expires_in_ms)
    live_store.upsert_forming(
        conn, symbol, timeframe,
        Candle(time_msc=now - 60_000, open=4035.0, high=4036.0, low=4034.0,
               close=4035.0, tick_volume=10, spread=20, real_volume=0),
        now - updated_age_ms,
    )


def test_an_open_is_refused_when_journal_live_never_beat(conn):
    _seed_open(conn, beat_age_ms=None)
    with pytest.raises(CommandError, match="journal live"):
        enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                     sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_an_open_is_refused_when_the_heartbeat_is_stale(conn):
    _seed_open(conn, beat_age_ms=60_000)
    with pytest.raises(CommandError, match="journal live"):
        enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                     sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_an_open_is_refused_when_an_actively_watched_feed_is_frozen(conn):
    # `journal live` is alive but has not refreshed this symbol's forming bar:
    # the process is up, the price is not moving through it.
    _seed_open(conn)
    _watch(conn, "XAUUSDc", "M1", expires_in_ms=300_000, updated_age_ms=120_000)
    with pytest.raises(CommandError, match="XAUUSDc"):
        enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                     sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_an_expired_watch_does_not_block_an_open(conn):
    # `live_candles` rows are never pruned, so a long-closed chart leaves a stale
    # row behind forever. That is not evidence of a frozen feed -- nobody asked
    # `serve_watches` to refresh it -- and must not refuse an open placed from
    # /live, where no chart is mounted at all.
    _seed_open(conn)
    _watch(conn, "XAUUSDc", "M1", expires_in_ms=-1_000, updated_age_ms=3_600_000)
    cmd_id = enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                          sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert get_command(conn, cmd_id)["status"] == "pending"


def test_another_symbols_frozen_feed_does_not_block_an_open(conn):
    _seed_open(conn)
    _watch(conn, "BTCUSDc", "M1", expires_in_ms=300_000, updated_age_ms=120_000)
    cmd_id = enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                          sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert get_command(conn, cmd_id)["status"] == "pending"


def test_a_fresh_actively_watched_feed_allows_the_open(conn):
    _seed_open(conn)
    _watch(conn, "XAUUSDc", "M1", expires_in_ms=300_000, updated_age_ms=1_000)
    cmd_id = enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                          sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert get_command(conn, cmd_id)["status"] == "pending"


# --------------------------------------------- open: price_ref matches the feed
#
# A moving feed proves the SERVER is seeing prices; it never proves the number
# the browser posted came from that feed. The reported failure: the tab's
# /api/candles fetch wedges, `mergeForming` keeps painting the last bar it has,
# and `serve_watches` keeps the same watch fresh server-side — so at M15 the
# frontend's own `staleEntryReason` (2 x timeframe) still arms the button on a
# 30-minute-old bar, and the lot is derived from a 30-minute-old price. Only
# comparing `price_ref` against what the server last saw closes that.


def _watch_at(conn, symbol, timeframe, close, *, updated_age_ms=1_000):
    now = now_ms()
    live_store.upsert_watch(conn, symbol, timeframe, now, 300_000)
    live_store.upsert_forming(
        conn, symbol, timeframe,
        Candle(time_msc=now - 60_000, open=close, high=close + 1, low=close - 1,
               close=close, tick_volume=10, spread=20, real_volume=0),
        now - updated_age_ms,
    )


def test_an_open_sized_against_a_price_the_feed_disagrees_with_is_refused(conn):
    # Stop distance 5.0, so the reference price may sit at most 1.25 from the
    # price the server last saw. The market is at 4060; the frozen tab says 4035.
    _seed_open(conn)
    _watch_at(conn, "XAUUSDc", "M1", 4060.0)
    with pytest.raises(CommandError, match="4060|acuan"):
        enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                     sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_a_price_ref_close_to_the_feed_is_accepted(conn):
    # Half a dollar of drift on a 5.0 stop is a fifth of the intended risk —
    # inside the tolerance, and normal between one tick and the next.
    _seed_open(conn)
    _watch_at(conn, "XAUUSDc", "M1", 4035.5)
    cmd_id = enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                          sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    assert get_command(conn, cmd_id)["status"] == "pending"


def test_load_open_context_builds_a_synthetic_position(conn):
    _seed_open(conn)
    pos, spec = load_open_context(conn, _LOGIN, "XAUUSDc", "buy", 4035.0)
    assert pos["position_id"] is None
    assert pos["symbol"] == "XAUUSDc"
    assert pos["direction"] == "buy"
    assert abs(pos["price_current"] - 4035.0) < 1e-9
    assert spec["symbol"] == "XAUUSDc"


# ------------------------------------------------------------------- expiry


def _age_pending(conn, cmd_id, seconds):
    """Backdate a queued row's `requested_msc` — the only way to write the state
    'this has been waiting for hours' without waiting for hours."""
    conn.execute(
        "UPDATE trade_commands SET requested_msc = ? WHERE id = ?",
        (now_ms() - int(seconds * 1000), cmd_id),
    )
    conn.commit()


def test_expire_stale_refuses_a_command_nobody_claimed(conn):
    """The row the human believes is an SL in flight. Nothing was executing it
    (`journal live` down, or `--no-trading`), so its `price_ref` and the market
    it was validated against are both hours old: refusing is the only reading
    that cannot send a stale order."""
    cmd_id = enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3300.0)
    _age_pending(conn, cmd_id, 3600)

    assert expire_stale(conn, _LOGIN) == 1
    row = get_command(conn, cmd_id)
    assert row["status"] == "rejected"
    assert row["retcode"] is None          # never reached the broker
    assert "kedaluwarsa" in row["error"]
    assert claim_next(conn, _LOGIN) is None   # and not still work


def test_expire_stale_leaves_a_fresh_command_alone(conn):
    """`journal live` claims within a cycle or two; a restart takes longer than
    that and must not cost the human the command they just queued."""
    cmd_id = enqueue(conn, _LOGIN, "close", 111)
    assert expire_stale(conn, _LOGIN) == 0
    assert get_command(conn, cmd_id)["status"] == "pending"


def test_expire_stale_never_touches_a_claimed_or_sent_row(conn):
    """Those are `recover_interrupted`'s, and the distinction is the whole
    point: a `sent` row MAY exist at the broker, so closing it out here — on a
    timer, with no human reading the message — would invent an outcome for a
    real order."""
    enqueue(conn, _LOGIN, "close", 111)
    claimed = claim_next(conn, _LOGIN)
    _age_pending(conn, claimed["id"], 86_400)

    assert expire_stale(conn, _LOGIN) == 0
    assert get_command(conn, claimed["id"])["status"] == "claimed"


def test_expire_stale_is_scoped_to_the_account(conn):
    cmd_id = enqueue(conn, _LOGIN, "close", 111)
    _age_pending(conn, cmd_id, 3600)
    assert expire_stale(conn, _LOGIN + 1) == 0
    assert get_command(conn, cmd_id)["status"] == "pending"
