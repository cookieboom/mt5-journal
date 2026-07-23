"""Poll live MT5 positions for their SL/TP into `sl_tp_snapshots` (M4).

`positions_get()` only returns CURRENTLY OPEN positions -- this can never help
the 62 historical discretionary trades whose SL was never captured; it only
benefits trades open WHILE THE POLLER RUNS (docs/mt5-deal-model.md Trap 6).
Once a position closes it disappears from the API forever, so snapshot
collection is naturally bounded to the position's open lifetime -- no special
handling needed to "stop" tracking a closed position.

`poll_once` is the timing-free cycle (one snapshot pass, easily tested under
FakeMT5Client with synthetic Position lists); `poll_loop` wraps it in a sleep
loop with an injectable clock so `--once`/`--duration`/Ctrl+C are all
unit-testable without a real wall-clock wait. Change-only logging: a row is
written only when (sl, tp, volume) differs from the most recent DB row for
that position_id -- at a 5s interval the longest measured trade (11h25m) would
otherwise produce ~8200 near-identical rows; logging transitions instead keeps
the table small AND makes every row a real event (Rule 9), not a heartbeat.

`sl_tp_snapshots` is append-only, exactly like `deals_raw` (Trap 16's "once
captured locally it can't be lost" applies here too) -- `poll_once` commits
every cycle, never batches across ticks, so a killed process loses at most the
IN-FLIGHT cycle, never a previously-written one.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from ..adapter.base import MT5Client, Position
from ..store.db import now_ms

log = logging.getLogger(__name__)

# Float comparison tolerance for change detection (CLAUDE.md rule 5 -- never ==).
_VOL_TOL = 1e-9


@dataclass(frozen=True)
class PollReport:
    account_login: int
    observed_msc: int
    positions_seen: int = 0
    positions_skipped: int = 0   # malformed: identifier is None
    snapshots_written: int = 0   # new rows -- change-only, so an unchanged
                                  # position costs nothing beyond one SELECT


@dataclass(frozen=True)
class LoopReport:
    cycles: int = 0
    snapshots_written: int = 0
    stopped_by: str = "duration"   # 'once' | 'duration' | 'interrupt'


def _floats_equal(a: float | None, b: float | None) -> bool:
    """None-safe, tolerance-based comparison. Live `positions_get()` never
    actually returns None for sl/tp/volume -- MT5 uses 0.0 for "unset" -- but
    the `Position` dataclass defaults to None for an absent fixture key, so
    this stays defensive rather than assuming the live shape everywhere."""
    if a is None or b is None:
        return a is b
    return abs(a - b) < _VOL_TOL


def _changed(last: tuple, p: Position) -> bool:
    last_sl, last_tp, last_vol = last
    return not (
        _floats_equal(last_sl, p.sl)
        and _floats_equal(last_tp, p.tp)
        and _floats_equal(last_vol, p.volume)
    )


def _last_snapshot(conn: sqlite3.Connection, login: int, position_id: int) -> sqlite3.Row | None:
    """Most recent stored row for this position, or None if never observed
    before. Read from the DB, not in-memory state, so dedup survives a poller
    restart for free -- served by the existing ix_sltp_pos index."""
    return conn.execute(
        "SELECT observed_msc, sl, tp, volume FROM sl_tp_snapshots "
        "WHERE account_login = ? AND position_id = ? "
        "ORDER BY observed_msc DESC LIMIT 1",
        (login, position_id),
    ).fetchone()


def poll_once(
    client: MT5Client,
    conn: sqlite3.Connection,
    login: int,
    *,
    positions: list[Position] | None = None,
) -> PollReport:
    """One snapshot cycle: read every currently-open position, write a row for
    each one whose (sl, tp, volume) differs from its most recently stored state
    (change-only logging). Commits before returning -- see module docstring.

    `positions` lets a caller that has ALREADY fetched the open positions this
    cycle pass the same list in, so `positions_get()` is called exactly once per
    cycle rather than once per consumer. `journal live` (ingest/live.py) needs
    the very same list for snapshots AND for close detection; fetching twice
    would risk the two seeing different worlds a few ms apart. `None` (the
    default) preserves the standalone `journal poll` behaviour -- fetch here."""
    if positions is None:
        positions = client.positions_get()
    ts = now_ms()
    written = 0
    skipped = 0

    for p in positions:
        if p.identifier is None:
            skipped += 1
            continue
        last = _last_snapshot(conn, login, p.identifier)
        if last is not None and not _changed(
            (last["sl"], last["tp"], last["volume"]), p
        ):
            continue
        # The PK is (account_login, position_id, observed_msc). Two genuinely
        # DIFFERENT states for the same position landing in the same
        # millisecond (low clock resolution, or two poll_once calls back to
        # back) would otherwise collide and INSERT OR IGNORE would silently
        # DROP the second, real observation -- exactly the data loss Trap 16
        # forbids. Force strictly-increasing observed_msc per position instead.
        observed_msc = ts
        if last is not None and observed_msc <= last["observed_msc"]:
            observed_msc = last["observed_msc"] + 1
        cur = conn.execute(
            "INSERT OR IGNORE INTO sl_tp_snapshots "
            "(account_login, position_id, observed_msc, sl, tp, volume) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (login, p.identifier, observed_msc, p.sl, p.tp, p.volume),
        )
        written += cur.rowcount

    conn.commit()
    return PollReport(
        account_login=login,
        observed_msc=ts,
        positions_seen=len(positions),
        positions_skipped=skipped,
        snapshots_written=written,
    )


def poll_loop(
    client: MT5Client,
    conn: sqlite3.Connection,
    login: int,
    *,
    interval: float = 5.0,
    once: bool = False,
    duration: float | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
    on_cycle=None,
) -> LoopReport:
    """Repeatedly call `poll_once` every `interval` seconds. `sleep`/`monotonic`
    are injectable so `--once`/`--duration`/Ctrl+C are all testable without a
    real wall-clock wait -- tests pass a fake `sleep` that raises
    KeyboardInterrupt on cue and/or advances a fake `monotonic` clock.

    `on_cycle`, if given, is called with each cycle's `PollReport` right after
    it completes. This is the CLI's only way to give live feedback: a
    long-running `journal poll` is a FOREGROUND command a human watches, and
    `log.info` alone is invisible in a terminal with no handler configured --
    without this hook the process would look hung until Ctrl+C even while
    working correctly.

    Always runs at least one cycle. `once` takes priority over `duration` if
    both are given. The deadline is checked right after a cycle and before the
    next sleep, so a `duration` run never sleeps after its final cycle."""
    cycles = 0
    total_written = 0
    deadline = monotonic() + duration if duration is not None else None

    try:
        while True:
            r = poll_once(client, conn, login)
            cycles += 1
            total_written += r.snapshots_written
            if r.snapshots_written:
                log.info(
                    "poll: %d new snapshot(s), %d open position(s)",
                    r.snapshots_written, r.positions_seen,
                )
            if on_cycle is not None:
                on_cycle(r)
            if once:
                return LoopReport(
                    cycles=cycles, snapshots_written=total_written, stopped_by="once"
                )
            if deadline is not None and monotonic() >= deadline:
                return LoopReport(
                    cycles=cycles, snapshots_written=total_written, stopped_by="duration"
                )
            sleep(interval)
    except KeyboardInterrupt:
        return LoopReport(
            cycles=cycles, snapshots_written=total_written, stopped_by="interrupt"
        )
