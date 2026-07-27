"""M9 Phase 4 — `journal live`, the one process that owns the bridge.

Written before the implementation (CLAUDE.md rule 7). Everything here runs under
`FakeMT5Client` with an injected clock: NO test may need a live bridge.

`live_cycle` is the timing-free unit surface (mirrors `poll_once`); `live_loop`
wraps it in the injectable-clock sleep loop (mirrors `poll_loop`). The three
properties that matter and are all about real state or real money:

  * a position that DISAPPEARS from the feed triggers the ingest pipeline exactly
    once, and a failed ingest never kills the loop;
  * `open_positions` is a wholesale mirror, never a growing log;
  * a queued command is claimed once, sent once, and an interrupted send is never
    re-sent by a machine.
"""

from __future__ import annotations

import pytest

from journal.adapter.base import Position, TradeResult, TradeRetcode
from journal.adapter.fake import FakeMT5Client
from journal.execute import claim_next, enqueue, get_command
from journal.ingest.live import live_cycle, live_loop
from journal.store.db import connect

_LOGIN = 257223861
_MSC_FLOOR = 10**12


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
    c.commit()
    yield c
    c.close()


class FakeLiveClient(FakeMT5Client):
    """Returns one scripted batch of `Position`s per `positions_get()` call,
    advancing through the list and repeating the last batch (so a multi-cycle
    `live_loop` never runs off the end). Inherits `script_results`/`order_check`/
    `order_send`/`sent`/`checked` from the base fake."""

    def __init__(self, batches):
        super().__init__()
        self._batches = batches
        self._i = 0

    def positions_get(self):
        batch = self._batches[min(self._i, len(self._batches) - 1)]
        self._i += 1
        return batch


def _pos(
    identifier=111,
    type=0,
    symbol="XAUUSDc",
    sl=0.0,
    tp=0.0,
    volume=0.10,
    price_open=3300.0,
    price_current=3310.0,
    profit=150.0,
    swap=-2.0,
    magic=0,
    time_msc=1_700_000_000_000,
):
    return Position(
        identifier=identifier,
        type=type,
        symbol=symbol,
        sl=sl,
        tp=tp,
        volume=volume,
        price_open=price_open,
        price_current=price_current,
        profit=profit,
        swap=swap,
        magic=magic,
        time_msc=time_msc,
    )


class FakeLiveClientWithRates(FakeLiveClient):
    """Extends `FakeLiveClient` (rather than adding a second parallel fake) so
    `copy_rates_range` returns one scripted bar regardless of the range asked —
    just enough for a candle-fulfilment cycle to write a real row."""

    def __init__(self, batches, bar):
        super().__init__(batches)
        self._bar = bar

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        return [self._bar]


def _spy_pipeline(monkeypatch):
    """Record the pipeline order without touching real ingest. Returns the list
    the four stages append their names to as they run."""
    calls: list[str] = []
    monkeypatch.setattr("journal.ingest.deals.sync", lambda client, conn: calls.append("sync"))
    monkeypatch.setattr("journal.domain.reconstruct.rebuild", lambda conn: calls.append("rebuild"))
    monkeypatch.setattr("journal.ingest.candles.sync_candles", lambda client, conn: calls.append("candles"))
    return calls


# ---------------------------------------------------------------- open_positions


def test_open_positions_mirrored_from_feed(conn):
    client = FakeLiveClient([[_pos(identifier=111, type=0, sl=3290.0, tp=3350.0)]])
    r = live_cycle(client, conn, _LOGIN)
    assert r.positions_seen == 1
    row = conn.execute(
        "SELECT * FROM open_positions WHERE position_id = 111"
    ).fetchone()
    assert row["direction"] == "buy"      # type 0
    assert row["sl"] == 3290.0
    assert row["tp"] == 3350.0
    assert row["symbol"] == "XAUUSDc"
    assert row["symbol_base"] == "XAUUSD"
    assert row["profit"] == 150.0
    assert row["open_time_msc"] == 1_700_000_000_000  # broker server time, verbatim


def test_direction_from_type_sell(conn):
    client = FakeLiveClient([[_pos(identifier=222, type=1)]])
    live_cycle(client, conn, _LOGIN)
    row = conn.execute("SELECT direction FROM open_positions WHERE position_id = 222").fetchone()
    assert row["direction"] == "sell"


def test_open_positions_replaced_wholesale(conn):
    # cycle 1 shows 111; cycle 2 shows only 222 -> 111 must be GONE, not lingering.
    client = FakeLiveClient([[_pos(identifier=111)], [_pos(identifier=222)]])
    live_cycle(client, conn, _LOGIN)
    live_cycle(client, conn, _LOGIN)
    ids = [r["position_id"] for r in conn.execute(
        "SELECT position_id FROM open_positions ORDER BY position_id"
    ).fetchall()]
    assert ids == [222]


def test_malformed_position_skipped(conn):
    client = FakeLiveClient([[Position(identifier=None, symbol="XAUUSDc"), _pos(identifier=111)]])
    r = live_cycle(client, conn, _LOGIN)
    ids = [row["position_id"] for row in conn.execute(
        "SELECT position_id FROM open_positions"
    ).fetchall()]
    assert ids == [111]                    # malformed one never mirrored
    assert r.positions_seen == 2           # raw feed count, poll_once semantics


def test_snapshots_still_written_via_poll_once(conn):
    # live_cycle reuses poll_once for sl_tp_snapshots; a fresh position => 1 row.
    client = FakeLiveClient([[_pos(identifier=111, sl=3290.0)]])
    r = live_cycle(client, conn, _LOGIN)
    assert r.snapshots_written == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM sl_tp_snapshots WHERE position_id = 111"
    ).fetchone()[0] == 1


def test_positions_get_called_once_per_cycle(conn):
    # poll_once + snapshots + close-detection all share ONE fetch.
    class Counting(FakeLiveClient):
        calls = 0

        def positions_get(self):
            Counting.calls += 1
            return super().positions_get()

    client = Counting([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    assert Counting.calls == 1


# ---------------------------------------------------------------- close detection


def test_close_triggers_pipeline_once_in_order(conn, monkeypatch):
    calls = _spy_pipeline(monkeypatch)
    client = FakeLiveClient([[_pos(identifier=111)], []])
    r1 = live_cycle(client, conn, _LOGIN)
    assert r1.ingest_ran is False          # position still open
    assert calls == []
    r2 = live_cycle(client, conn, _LOGIN)  # 111 gone -> closed
    assert r2.ingest_ran is True
    assert r2.closed_ids == [111]
    assert calls == ["sync", "rebuild", "candles", "rebuild"]


def test_multiple_closes_debounced_to_one_pipeline(conn, monkeypatch):
    calls = _spy_pipeline(monkeypatch)
    client = FakeLiveClient([[_pos(identifier=111), _pos(identifier=222)], []])
    live_cycle(client, conn, _LOGIN)
    r2 = live_cycle(client, conn, _LOGIN)
    assert sorted(r2.closed_ids) == [111, 222]
    # ONE pipeline run, not one per closed position.
    assert calls.count("sync") == 1
    assert calls == ["sync", "rebuild", "candles", "rebuild"]


def test_no_close_no_pipeline(conn, monkeypatch):
    calls = _spy_pipeline(monkeypatch)
    client = FakeLiveClient([[_pos(identifier=111)], [_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    r2 = live_cycle(client, conn, _LOGIN)
    assert r2.ingest_ran is False
    assert calls == []


def test_on_close_callback_receives_closed_ids(conn, monkeypatch):
    _spy_pipeline(monkeypatch)
    seen = []
    client = FakeLiveClient([[_pos(identifier=111)], []])
    live_cycle(client, conn, _LOGIN, on_close=seen.append)
    live_cycle(client, conn, _LOGIN, on_close=seen.append)
    assert seen == [[111]]  # fired ONCE, only on the cycle with a close


def test_on_closing_fires_before_the_ingest_pipeline(conn, monkeypatch):
    # The CLI uses on_closing to warn the human BEFORE the (blocking) ingest, so
    # the heartbeat pause never reads as a freeze. It must run before sync.
    calls = _spy_pipeline(monkeypatch)
    client = FakeLiveClient([[_pos(identifier=111)], []])
    live_cycle(conn=conn, client=client, login=_LOGIN)  # cycle 1: position open
    live_cycle(
        conn=conn, client=client, login=_LOGIN,
        on_closing=lambda ids: calls.append(f"closing{ids}"),
    )  # cycle 2: it closed
    assert calls == ["closing[111]", "sync", "rebuild", "candles", "rebuild"]


def test_failed_ingest_does_not_kill_the_loop(conn, monkeypatch):
    # Losing the poller loop loses unrecoverable SL history; a broken sync must
    # be caught and logged, never propagated.
    def boom(client, conn):
        raise RuntimeError("bridge died mid-sync")

    monkeypatch.setattr("journal.ingest.deals.sync", boom)
    monkeypatch.setattr("journal.domain.reconstruct.rebuild", lambda conn: None)
    monkeypatch.setattr("journal.ingest.candles.sync_candles", lambda client, conn: None)

    client = FakeLiveClient([[_pos(identifier=111)], []])
    live_cycle(client, conn, _LOGIN)
    r2 = live_cycle(client, conn, _LOGIN)  # must NOT raise
    assert r2.closed_ids == [111]
    assert r2.ingest_ran is False          # it was attempted but failed


# ---------------------------------------------------------------- command execution


def test_pending_command_claimed_and_sent_once(conn):
    # Populate open_positions, then queue a close against it.
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    cmd_id = enqueue(conn, _LOGIN, "close", 111)

    r = live_cycle(client, conn, _LOGIN)
    assert len(client.sent) == 1           # exactly one real order
    assert r.command_id == cmd_id
    assert get_command(conn, cmd_id)["status"] == "done"

    # A racing/second cycle finds nothing pending -> no second send.
    live_cycle(client, conn, _LOGIN)
    assert len(client.sent) == 1
    assert claim_next(conn, _LOGIN) is None


def test_order_check_precedes_send(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    enqueue(conn, _LOGIN, "close", 111)
    live_cycle(client, conn, _LOGIN)
    assert len(client.checked) == 1        # dry-run happened
    assert len(client.sent) == 1


def test_no_trading_leaves_pending_untouched(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    cmd_id = enqueue(conn, _LOGIN, "close", 111)

    r = live_cycle(client, conn, _LOGIN, trading=False)
    assert client.sent == []
    assert client.checked == []
    assert get_command(conn, cmd_id)["status"] == "pending"
    assert r.command_id is None


def test_command_whose_position_vanished_is_rejected(conn, monkeypatch):
    _spy_pipeline(monkeypatch)  # keep the close from running real ingest
    # 111 open, queue a close, then the feed drops it before the command runs.
    client = FakeLiveClient([[_pos(identifier=111)], []])
    live_cycle(client, conn, _LOGIN)
    cmd_id = enqueue(conn, _LOGIN, "close", 111)

    live_cycle(client, conn, _LOGIN)       # feed empty -> 111 removed, then claim
    row = get_command(conn, cmd_id)
    assert row["status"] == "rejected"
    assert row["retcode"] is None          # never reached the broker
    assert client.sent == []


def test_only_one_command_per_cycle(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    a = enqueue(conn, _LOGIN, "close", 111)
    enqueue(conn, _LOGIN, "modify_sltp", 111, sl=3290.0)

    r = live_cycle(client, conn, _LOGIN)
    assert len(client.sent) == 1           # serial: one per cycle
    assert r.command_id == a               # the oldest


def test_order_send_raising_does_not_kill_loop_or_mark_done(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    cmd_id = enqueue(conn, _LOGIN, "close", 111)
    # order_check pops the DONE; order_send pops the Exception and raises.
    client.script_results(TradeResult(retcode=TradeRetcode.DONE), RuntimeError("bridge died"))

    r = live_cycle(client, conn, _LOGIN)   # must NOT raise
    row = get_command(conn, cmd_id)
    assert row["status"] == "sent"         # evidence of possible broker contact
    assert row["status"] != "done"
    assert r.command_status == "sent"

    # The loop keeps going, and the 'sent' row is NEVER re-sent by another cycle.
    before = len(client.sent)
    live_cycle(client, conn, _LOGIN)
    assert len(client.sent) == before


# ---------------------------------------------------------------- live_loop


def test_loop_recovers_interrupted_at_startup_and_never_resends(conn):
    # A row left 'sent' by a crashed process: recover_interrupted marks it failed,
    # and no cycle re-sends it.
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    cmd_id = enqueue(conn, _LOGIN, "close", 111)
    conn.execute("UPDATE trade_commands SET status = 'sent' WHERE id = ?", (cmd_id,))
    conn.commit()

    r = live_loop(client, conn, _LOGIN, once=True)
    assert r.recovered == 1
    assert get_command(conn, cmd_id)["status"] == "failed"
    assert client.sent == []               # never re-sent


def test_loop_once_runs_exactly_one_cycle(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])
    r = live_loop(client, conn, _LOGIN, once=True)
    assert r.cycles == 1
    assert r.stopped_by == "once"


def test_loop_busy_interval_when_commands_pending(conn):
    # trading OFF keeps the pending row pending, so pending_count stays > 0 and
    # the loop must pick the BUSY interval.
    client = FakeLiveClient([[_pos(identifier=111)]])
    live_cycle(client, conn, _LOGIN)
    enqueue(conn, _LOGIN, "close", 111)

    sleeps = []
    clock = {"t": 0.0}
    r = live_loop(
        client, conn, _LOGIN, trading=False,
        interval_idle=5.0, interval_busy=1.0, duration=0.5,
        sleep=lambda s: (sleeps.append(s), clock.__setitem__("t", clock["t"] + s)),
        monotonic=lambda: clock["t"],
    )
    assert r.stopped_by == "duration"
    assert sleeps and all(s == 1.0 for s in sleeps)   # busy interval


def test_loop_idle_interval_when_nothing_pending(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])
    sleeps = []
    clock = {"t": 0.0}
    live_loop(
        client, conn, _LOGIN,
        interval_idle=5.0, interval_busy=1.0, duration=0.5,
        sleep=lambda s: (sleeps.append(s), clock.__setitem__("t", clock["t"] + s)),
        monotonic=lambda: clock["t"],
    )
    assert sleeps and all(s == 5.0 for s in sleeps)   # idle interval


def test_loop_keyboard_interrupt_stops_cleanly(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])

    def kb_sleep(_):
        raise KeyboardInterrupt

    r = live_loop(client, conn, _LOGIN, sleep=kb_sleep)
    assert r.stopped_by == "interrupt"
    assert r.cycles == 1


def test_loop_once_takes_priority_over_duration(conn):
    client = FakeLiveClient([[_pos(identifier=111)]])

    def exploding_sleep(_):
        raise AssertionError("sleep must not be called when once=True")

    r = live_loop(client, conn, _LOGIN, once=True, duration=100.0, sleep=exploding_sleep)
    assert r.cycles == 1
    assert r.stopped_by == "once"


# ---------------------------------------------------------------- candle requests


def test_live_cycle_fulfils_one_candle_request(conn):
    from journal.store import candle_queue as q
    from journal.adapter.base import Candle

    BASE = 1_700_000_000_000
    M1 = 60_000
    bar = Candle(
        time_msc=BASE + M1, open=1, high=2, low=0.5, close=1.5,
        tick_volume=1, spread=1, real_volume=1,
    )
    client = FakeLiveClientWithRates([[]], bar)
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3 * M1)

    r = live_cycle(client, conn, _LOGIN, trading=True)

    assert r.candle_request_id is not None
    assert r.candle_bars_written == 1
    row = conn.execute(
        "SELECT status FROM candle_requests WHERE id = ?", (r.candle_request_id,)
    ).fetchone()
    assert row["status"] == "done"


# ---------------------------------------------------------------- heartbeat


def test_live_cycle_writes_heartbeat(conn):
    from journal.store import live_store as ls
    client = FakeLiveClient([[]])          # no positions is fine
    assert ls.read_heartbeat(conn) is None
    live_cycle(client, conn, _LOGIN)
    beat = ls.read_heartbeat(conn)
    assert beat is not None and beat >= _MSC_FLOOR  # real ms, always written


# ---------------------------------------------------------------- serve_watches


def test_serve_watches_splits_forming_from_closed(conn):
    from journal.ingest.live_candles import serve_watches
    from journal.store import live_store as ls
    from journal.store import candles_store as cs
    from journal.adapter.base import Candle

    tf = "M5"; size = 300_000
    now = 1_700_000_000_000
    now = now - (now % size) + 120_000          # 2 min into the current bucket
    cur_bucket = now - (now % size)
    prev_bucket = cur_bucket - size
    closed = Candle(time_msc=prev_bucket, open=1, high=2, low=0.5, close=1.5,
                    tick_volume=5, spread=2, real_volume=0)
    forming = Candle(time_msc=cur_bucket, open=1.5, high=3, low=1.4, close=2.9,
                     tick_volume=7, spread=2, real_volume=0)

    class C(FakeLiveClient):
        def copy_rates_range(self, symbol, timeframe, date_from, date_to):
            return [closed, forming]

    client = C([[]])
    ls.upsert_watch(conn, "XAUUSDc", tf, now, ttl_ms=30_000)
    written = serve_watches(client, conn, now)

    assert written == 1
    # forming bar is in live_candles, NOT candles
    assert ls.read_forming(conn, "XAUUSDc", tf).time_msc == cur_bucket
    assert cs.read_candles(conn, "XAUUSDc", tf, cur_bucket, cur_bucket) == []
    # closed bar promoted to candles + coverage recorded
    rows = cs.read_candles(conn, "XAUUSDc", tf, prev_bucket, prev_bucket)
    assert len(rows) == 1
    assert cs.read_coverage(conn, "XAUUSDc", tf) != []


def test_serve_watches_noop_without_active_watch(conn):
    from journal.ingest.live_candles import serve_watches
    client = FakeLiveClientWithRates([[]], bar=None)  # never asked
    assert serve_watches(client, conn, 1_700_000_000_000) == 0


def test_serve_watches_records_coverage_when_bridge_returns_no_bars(conn):
    # A watch left open across a weekend/holiday: the bridge has no closed and
    # no forming bar for the window. Coverage over the closed span must still
    # be recorded, or the same empty slice gets re-fetched every live cycle.
    from journal.ingest.live_candles import serve_watches
    from journal.store import live_store as ls
    from journal.store import candles_store as cs

    tf = "M5"; size = 300_000
    now = 1_700_000_000_000
    now = now - (now % size) + 120_000          # 2 min into the current bucket

    class C(FakeLiveClient):
        def copy_rates_range(self, symbol, timeframe, date_from, date_to):
            return []

    client = C([[]])
    ls.upsert_watch(conn, "XAUUSDc", tf, now, ttl_ms=30_000)
    written = serve_watches(client, conn, now)

    assert written == 0
    assert cs.read_coverage(conn, "XAUUSDc", tf) != []
