"""M4 poller — change-only snapshot logging, dedup, and the injectable-clock
loop. All under `FakeMT5Client` subclasses returning synthetic `Position`
objects (mirrors `DropDealClient`/`TickClient` in `test_ingest.py`, never the
sparse, live, time-dependent `tests/fixtures/positions.json`).
"""

from __future__ import annotations

import pytest

from journal.adapter.base import Position
from journal.adapter.fake import FakeMT5Client
from journal.ingest.poller import poll_loop, poll_once
from journal.store.db import connect

_LOGIN = 0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


class FakePositionsClient(FakeMT5Client):
    """Returns one scripted batch of `Position`s per call to `positions_get()`,
    advancing through the list; the last batch repeats if called more times
    than scripted (handy for `poll_loop` tests that cycle several times)."""

    def __init__(self, batches: list[list[Position]]):
        super().__init__()
        self._batches = batches
        self._i = 0

    def positions_get(self):
        batch = self._batches[min(self._i, len(self._batches) - 1)]
        self._i += 1
        return batch


def _pos(identifier=555, symbol="XAUUSDc", sl=0.0, tp=0.0, volume=0.1):
    return Position(identifier=identifier, symbol=symbol, sl=sl, tp=tp, volume=volume)


# ------------------------------------------------------------------ poll_once


def test_fresh_position_writes_one_snapshot(conn):
    r = poll_once(FakePositionsClient([[_pos()]]), conn, _LOGIN)
    assert r.snapshots_written == 1
    assert r.positions_seen == 1
    assert r.positions_skipped == 0
    rows = conn.execute("SELECT * FROM sl_tp_snapshots").fetchall()
    assert len(rows) == 1
    assert rows[0]["position_id"] == 555
    assert rows[0]["account_login"] == _LOGIN


def test_unchanged_position_writes_nothing(conn):
    poll_once(FakePositionsClient([[_pos()]]), conn, _LOGIN)
    r2 = poll_once(FakePositionsClient([[_pos()]]), conn, _LOGIN)
    assert r2.snapshots_written == 0
    assert conn.execute("SELECT COUNT(*) FROM sl_tp_snapshots").fetchone()[0] == 1


def test_sl_change_writes_new_row_not_lost_to_pk_collision(conn):
    # The regression this test exists for: two DIFFERENT states for the same
    # position landing in the same millisecond (low clock resolution, or two
    # poll_once calls back to back) must NOT silently collide on the
    # (account_login, position_id, observed_msc) primary key and drop the
    # second, real observation -- that would violate the same append-only
    # guarantee Trap 16 makes for deals_raw.
    poll_once(FakePositionsClient([[_pos(sl=0.0)]]), conn, _LOGIN)
    r2 = poll_once(FakePositionsClient([[_pos(sl=4030.0)]]), conn, _LOGIN)
    assert r2.snapshots_written == 1
    rows = conn.execute(
        "SELECT observed_msc, sl FROM sl_tp_snapshots ORDER BY observed_msc"
    ).fetchall()
    assert len(rows) == 2
    assert [r["sl"] for r in rows] == [0.0, 4030.0]
    assert rows[1]["observed_msc"] > rows[0]["observed_msc"]  # strictly increasing


def test_tp_change_writes_new_row(conn):
    poll_once(FakePositionsClient([[_pos(tp=0.0)]]), conn, _LOGIN)
    r2 = poll_once(FakePositionsClient([[_pos(tp=4060.0)]]), conn, _LOGIN)
    assert r2.snapshots_written == 1


def test_volume_change_writes_new_row(conn):
    # A partial close changes volume without necessarily changing sl/tp.
    poll_once(FakePositionsClient([[_pos(volume=0.10)]]), conn, _LOGIN)
    r2 = poll_once(FakePositionsClient([[_pos(volume=0.06)]]), conn, _LOGIN)
    assert r2.snapshots_written == 1


def test_sl_removed_back_to_zero_is_a_real_transition(conn):
    poll_once(FakePositionsClient([[_pos(sl=4030.0)]]), conn, _LOGIN)
    r2 = poll_once(FakePositionsClient([[_pos(sl=0.0)]]), conn, _LOGIN)
    assert r2.snapshots_written == 1


def test_dedup_survives_independent_poll_once_calls(conn):
    # Simulates a poller restart: dedup is DB-sourced, not in-memory, so a
    # brand new client/call sequence still sees the prior state and skips.
    poll_once(FakePositionsClient([[_pos(sl=4030.0)]]), conn, _LOGIN)
    r2 = poll_once(FakePositionsClient([[_pos(sl=4030.0)]]), conn, _LOGIN)
    r3 = poll_once(FakePositionsClient([[_pos(sl=4030.0)]]), conn, _LOGIN)
    assert r2.snapshots_written == 0
    assert r3.snapshots_written == 0


def test_two_hedged_positions_tracked_independently(conn):
    p1 = _pos(identifier=111, sl=4030.0)
    p2 = _pos(identifier=222, sl=4040.0)
    r1 = poll_once(FakePositionsClient([[p1, p2]]), conn, _LOGIN)
    assert r1.snapshots_written == 2

    # Only p1 changes -- p2 must not get a redundant row.
    p1b = _pos(identifier=111, sl=4025.0)
    r2 = poll_once(FakePositionsClient([[p1b, p2]]), conn, _LOGIN)
    assert r2.snapshots_written == 1
    rows = conn.execute(
        "SELECT position_id, sl FROM sl_tp_snapshots WHERE position_id = 222"
    ).fetchall()
    assert len(rows) == 1  # p2 untouched


def test_malformed_position_skipped_not_crashed(conn):
    bad = Position(identifier=None, sl=1.0)
    r = poll_once(FakePositionsClient([[bad]]), conn, _LOGIN)
    assert r.positions_skipped == 1
    assert r.snapshots_written == 0
    assert conn.execute("SELECT COUNT(*) FROM sl_tp_snapshots").fetchone()[0] == 0


def test_commits_every_cycle_visible_from_fresh_connection(conn, tmp_path):
    poll_once(FakePositionsClient([[_pos()]]), conn, _LOGIN)
    # A separate connection to the SAME file proves the write was committed,
    # not just buffered in this connection's transaction (Trap 16: once
    # captured locally it must not be losable to a killed process).
    fresh = connect(tmp_path / "journal.db")
    try:
        assert fresh.execute("SELECT COUNT(*) FROM sl_tp_snapshots").fetchone()[0] == 1
    finally:
        fresh.close()


def test_no_open_positions_writes_nothing(conn):
    r = poll_once(FakePositionsClient([[]]), conn, _LOGIN)
    assert r.positions_seen == 0
    assert r.snapshots_written == 0


# ------------------------------------------------------------------ poll_loop


def test_loop_once_runs_exactly_one_cycle(conn):
    r = poll_loop(FakePositionsClient([[_pos()]]), conn, _LOGIN, once=True)
    assert r.cycles == 1
    assert r.stopped_by == "once"
    assert r.snapshots_written == 1


def test_loop_duration_stops_after_deadline_with_injected_clock(conn):
    clock = {"t": 0.0}
    sleeps = []

    def fake_monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    r = poll_loop(
        FakePositionsClient([[_pos()]]),
        conn, _LOGIN,
        interval=1.0, duration=2.5,
        sleep=fake_sleep, monotonic=fake_monotonic,
    )
    assert r.stopped_by == "duration"
    # deadline checked AFTER each cycle, so it runs one cycle past 2.5s of
    # elapsed (fake) sleep time: t=0,1,2,3 -> 4 cycles, 3 sleeps in between.
    assert r.cycles == 4
    assert sleeps == [1.0, 1.0, 1.0]


def test_loop_keyboard_interrupt_stops_cleanly_with_partial_totals(conn):
    def kb_sleep(seconds):
        raise KeyboardInterrupt

    r = poll_loop(FakePositionsClient([[_pos()]]), conn, _LOGIN, sleep=kb_sleep)
    assert r.stopped_by == "interrupt"
    assert r.cycles == 1
    assert r.snapshots_written == 1  # the one cycle that DID complete is counted


def test_loop_once_takes_priority_over_duration(conn):
    # If both are somehow set, `once` must win -- one cycle, not a timed loop.
    def exploding_sleep(seconds):
        raise AssertionError("sleep should never be called when once=True")

    r = poll_loop(
        FakePositionsClient([[_pos()]]), conn, _LOGIN,
        once=True, duration=100.0, sleep=exploding_sleep,
    )
    assert r.cycles == 1
    assert r.stopped_by == "once"


def test_loop_never_sleeps_after_the_final_cycle(conn):
    # The deadline check happens before sleep(), so a duration run must not
    # pay for one extra, wasted sleep after its last poll_once.
    clock = {"t": 0.0}
    sleep_calls = {"n": 0}

    def fake_monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        sleep_calls["n"] += 1
        clock["t"] += seconds

    r = poll_loop(
        FakePositionsClient([[_pos()]]), conn, _LOGIN,
        interval=1.0, duration=0.5,
        sleep=fake_sleep, monotonic=fake_monotonic,
    )
    # cycle1 at t=0 (< 0.5 deadline) -> sleep -> t=1
    # cycle2 at t=1 (>= 0.5 deadline) -> stop, no further sleep
    assert r.cycles == 2
    assert sleep_calls["n"] == 1


def test_loop_on_cycle_fires_once_per_cycle_with_that_cycles_report(conn):
    # The CLI's only source of live feedback for a long-running poll (no
    # --once) -- without it the process looks hung until Ctrl+C, since
    # log.info alone is invisible with no handler configured.
    seen = []
    poll_loop(
        FakePositionsClient([[_pos(sl=0.0)], [_pos(sl=4030.0)], [_pos(sl=4030.0)]]),
        conn, _LOGIN, once=False, duration=0.0,
        sleep=lambda s: None, monotonic=lambda: 1.0,  # first check already past deadline
        on_cycle=seen.append,
    )
    assert len(seen) == 1  # duration=0 with monotonic already past -> exactly one cycle
    assert seen[0].snapshots_written == 1
