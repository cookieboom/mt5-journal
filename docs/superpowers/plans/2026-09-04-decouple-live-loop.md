# Decouple position monitoring from symbol-data sync in `journal live` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `journal live`'s single `live_cycle`/`live_loop` into two independently-paced threads — `position_loop` (SL/TP mirror, close-detect, command execution, paper, backup) and `symbol_loop` (candle serving, backfill, ingest-on-close) — so a position close never blocks candle data, and each path gets its own configurable interval.

**Architecture:** `ingest/live.py`'s `live_cycle` is split into `position_cycle` and `symbol_cycle`, connected by a `queue.Queue` for close-event handoff (position side detects+enqueues, symbol side drains+ingests) and a `LockedMT5Client` wrapper serializing bridge access. `live_loop` is split into `position_loop`/`symbol_loop` runners with independent intervals; `cli.py`'s `live` command starts both as threads in one process, each with its own `sqlite3.Connection`.

**Tech Stack:** Python 3, `sqlite3`, `threading`, `queue.Queue`, `typer` (CLI), `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-04-decouple-live-loop-design.md`

## Global Constraints

- Never `import MetaTrader5` outside `adapter/` (CLAUDE.md rule 1) — the lock wrapper and both loops go through the existing `MT5Client` Protocol only.
- All timestamps stay epoch-ms integer UTC (CLAUDE.md rule 3) — `observed_msc` is computed independently by each loop's own `now_ms()` call at the top of its own cycle; the two loops no longer share one timestamp.
- No MT5 constants outside `adapter/live.py` / `adapter/native.py` (CLAUDE.md rule 12) — nothing in this plan touches that boundary.
- No schema changes (spec non-goal) — the close-event handoff is in-memory (`queue.Queue`), not a new table.
- Tests before implementation for anything in `domain/`/`analytics/` (CLAUDE.md rule 7) — not directly touched here, but every new/changed function in `ingest/live.py` still gets a failing test first per this plan's steps.
- `uv run pytest` must pass before any commit (CLAUDE.md, Definition of done).

---

## Task 1: `LockedMT5Client` — serialize bridge access across threads

**Files:**
- Create: `src/journal/ingest/locked_client.py`
- Test: `tests/test_locked_client.py`

**Interfaces:**
- Produces: `LockedMT5Client(inner: MT5Client, lock: threading.Lock | None = None)` implementing the full `MT5Client` Protocol (`account_info`, `symbol_info`, `symbol_info_tick`, `symbols_get`, `copy_rates_range`, `history_deals_get`, `history_orders_get`, `positions_get`, `order_check`, `order_send`). If `lock` is omitted, it creates its own `threading.Lock()`. Every method acquires the lock, calls the same-named method on `inner`, releases, returns the result (exceptions propagate after the lock is released via the `with` block).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_locked_client.py
from __future__ import annotations

import threading
import time

from journal.adapter.fake import FakeMT5Client
from journal.ingest.locked_client import LockedMT5Client


def test_forwards_calls_to_the_inner_client():
    inner = FakeMT5Client()
    locked = LockedMT5Client(inner)
    assert locked.positions_get() == inner.positions_get()
    assert locked.account_info() == inner.account_info()


class _SlowSymbolInfoTick(FakeMT5Client):
    """symbol_info_tick sleeps briefly and records the [start, end] of its own
    call so the test can check for overlap across threads."""

    def __init__(self):
        super().__init__()
        self.spans: list[tuple[float, float]] = []
        self._spans_lock = threading.Lock()

    def symbol_info_tick(self, symbol):
        start = time.monotonic()
        time.sleep(0.05)
        end = time.monotonic()
        with self._spans_lock:
            self.spans.append((start, end))
        return super().symbol_info_tick(symbol)


def test_concurrent_calls_from_two_threads_never_overlap():
    inner = _SlowSymbolInfoTick()
    locked = LockedMT5Client(inner)

    def _call():
        locked.symbol_info_tick("XAUUSDc")

    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(inner.spans) == 2
    (s1, e1), (s2, e2) = inner.spans
    # One call's span must not overlap the other's — the lock serialized them.
    assert e1 <= s2 or e2 <= s1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_locked_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'journal.ingest.locked_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/journal/ingest/locked_client.py
"""Serializes every MT5Client call behind one lock.

`journal live` now runs two threads (position_loop, symbol_loop) sharing one
bridge connection. `NativeMT5Client` (Windows terminal, the `MetaTrader5`
package) has no documented thread-safety guarantee for concurrent calls;
`LiveMT5Client` (Docker/HTTP bridge) is probably safe concurrently but gets
the same treatment for one consistent story. This wraps either adapter
behind a lock so only one thread ever has a call in flight on the real
client at a time — sleep timing, DB writes, and everything else in each
loop stays unblocked; only the bridge round trip itself serializes.
"""

from __future__ import annotations

import threading
from typing import Any

from ..adapter.base import (
    Account,
    Deal,
    Order,
    Position,
    SymbolInfo,
    Tick,
    TradeRequest,
    TradeResult,
)


class LockedMT5Client:
    def __init__(self, inner: Any, lock: threading.Lock | None = None) -> None:
        self._inner = inner
        self._lock = lock if lock is not None else threading.Lock()

    def account_info(self) -> Account | None:
        with self._lock:
            return self._inner.account_info()

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        with self._lock:
            return self._inner.symbol_info(symbol)

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        with self._lock:
            return self._inner.symbol_info_tick(symbol)

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]:
        with self._lock:
            return self._inner.symbols_get(group)

    def copy_rates_range(self, symbol: str, timeframe: str, date_from: Any,
                         date_to: Any):
        with self._lock:
            return self._inner.copy_rates_range(symbol, timeframe, date_from, date_to)

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]:
        with self._lock:
            return self._inner.history_deals_get(date_from, date_to)

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        with self._lock:
            return self._inner.history_orders_get(date_from, date_to)

    def positions_get(self) -> list[Position]:
        with self._lock:
            return self._inner.positions_get()

    def order_check(self, request: TradeRequest) -> TradeResult:
        with self._lock:
            return self._inner.order_check(request)

    def order_send(self, request: TradeRequest) -> TradeResult:
        with self._lock:
            return self._inner.order_send(request)
```

Check `src/journal/adapter/base.py` for the exact names `Account`, `Deal`, `Order`, `Position`, `SymbolInfo`, `Tick`, `TradeRequest`, `TradeResult` before writing the import — adjust the import line to match whatever that module actually exports (they are the same names `ingest/live.py` already imports as `MT5Client, Position`, plus the others used across `adapter/live.py`/`adapter/native.py`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_locked_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/journal/ingest/locked_client.py tests/test_locked_client.py
git commit -m "feat(live): add LockedMT5Client to serialize bridge access across threads"
```

---

## Task 2: Split cycle report dataclasses

**Files:**
- Modify: `src/journal/ingest/live.py:111-131` (the `LiveReport`/`LiveLoopReport` dataclasses)
- Test: `tests/test_live.py` (no new test file — this task has no independently observable behavior yet; it's covered by Task 3/4's tests. Skip the test-first steps for this task and fold it into Task 3 instead.)

This task is folded into Task 3 (below) rather than being its own commit — splitting a dataclass with no behavior yet to observe would fail the "own test cycle" bar for a task. Task 3's first step defines `PositionCycleReport`.

---

## Task 3: Extract `position_cycle` — no more inline ingest, push closes onto a queue

**Files:**
- Modify: `src/journal/ingest/live.py`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `poll_once(client, conn, login, *, positions=...)` (existing, `ingest/poller.py`), `paper_step(client, conn, *, now_msc)` (existing, same file), `_execute_one_command`, `expire_stale`, `_maybe_backup` (existing, same file).
- Produces:
  ```python
  @dataclass(frozen=True)
  class PositionCycleReport:
      account_login: int
      observed_msc: int
      positions_seen: int = 0
      snapshots_written: int = 0
      closed_ids: list[int] = field(default_factory=list)
      command_id: int | None = None
      command_status: str | None = None
      paper_resolved: int = 0

  def position_cycle(
      client: MT5Client,
      conn: sqlite3.Connection,
      login: int,
      closed_queue: "queue.Queue[list[int]]",
      *,
      trading: bool = True,
      on_closing=None,
  ) -> PositionCycleReport: ...
  ```
  Later tasks (position_loop, cli.py) call `position_cycle` with a `queue.Queue` and read `PositionCycleReport`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_live.py` (near the existing close-detection tests, replacing `test_close_triggers_pipeline_once_in_order` / `test_multiple_closes_debounced_to_one_pipeline` / `test_no_close_no_pipeline` — those three move to Task 4 in spirit, since they test *ingest*, not position mirroring; keep them passing against `live_cycle` for now and only remove them in Task 4 once `symbol_cycle` exists to re-home them):

```python
import queue as queue_mod


def test_position_cycle_pushes_closed_ids_onto_the_queue_instead_of_ingesting(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "_run_ingest_pipeline", lambda *a, **k: calls.append(a))
    client = FakeLiveClient(positions=[])
    q: queue_mod.Queue = queue_mod.Queue()
    live.position_cycle(client, conn, 7, q, trading=False)
    # seed one open position, then close it next cycle
    client2 = FakeLiveClient(positions=[_pos(1, symbol="XAUUSDc")])
    live.position_cycle(client2, conn, 7, q, trading=False)
    client3 = FakeLiveClient(positions=[])
    report = live.position_cycle(client3, conn, 7, q, trading=False)

    assert report.closed_ids == [1]
    assert calls == []                      # ingest NEVER called inline
    assert q.get_nowait() == [1]             # instead it's on the queue


def test_position_cycle_does_not_call_serve_watches(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "serve_watches", lambda *a, **k: calls.append(a))
    client = FakeLiveClient(positions=[])
    q: queue_mod.Queue = queue_mod.Queue()
    live.position_cycle(client, conn, 7, q, trading=False)
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_live.py -k position_cycle -v`
Expected: FAIL — `AttributeError: module 'journal.ingest.live' has no attribute 'position_cycle'`

- [ ] **Step 3: Implement `position_cycle`**

In `src/journal/ingest/live.py`:

1. Add near the top, with the other imports:
   ```python
   import queue as queue_mod
   ```
2. Add `PositionCycleReport` (exact fields shown in Interfaces above) right after the existing `LiveReport`/`LiveLoopReport` block — leave `LiveReport`/`LiveLoopReport` in place for now (Task 6 removes them once nothing references them).
3. Add `position_cycle`, built from the FIRST HALF of the current `live_cycle` body (`ingest/live.py:504-541` plus the command/expire block `571-586`), with two changes:
   - Drop the `serve_watches(...)` call (currently line 526) and its surrounding comment — that line moves verbatim into `symbol_cycle` in Task 4.
   - Replace the ingest block (currently lines 543-569: `if closed_ids: ... _run_ingest_pipeline(...) ...`) with:
     ```python
     if closed_ids:
         log.info("live: %d position(s) closed: %s", len(closed_ids), closed_ids)
         if on_closing is not None:
             on_closing(closed_ids)
         closed_queue.put(closed_ids)
     ```
     (No more try/except around `_run_ingest_pipeline`, no second beacon beat here — both move to `symbol_cycle`, which now owns the pipeline and therefore owns absorbing its failure and re-beating around it.)
   - Keep the beacon beat (`live_store.beat(conn, now_ms())`, currently line 531) — position_cycle still beats every cycle; it's the fast loop.
   - Return `PositionCycleReport(...)` instead of `LiveReport(...)`, dropping `ingest_ran`, `candle_request_id`, `candle_bars_written` from the constructor call (those belong to `SymbolCycleReport` in Task 4).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_live.py -k position_cycle -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "refactor(live): extract position_cycle, hand closed ids off via a queue"
```

---

## Task 4: Extract `symbol_cycle` — owns serve_watches, candle backfill, and ingest-on-close

**Files:**
- Modify: `src/journal/ingest/live.py`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `serve_watches(client, conn, now_msc, *, lookback_bars=3)` (existing, `ingest/live_candles.py`), `claim_next_request`/`fulfill_request` (existing), `_run_ingest_pipeline` (existing, unchanged body).
- Produces:
  ```python
  @dataclass(frozen=True)
  class SymbolCycleReport:
      observed_msc: int
      forming_bars_written: int = 0
      ingest_ran: bool = False
      closed_ids: list[int] = field(default_factory=list)
      candle_request_id: int | None = None
      candle_bars_written: int | None = None

  def symbol_cycle(
      client: MT5Client,
      conn: sqlite3.Connection,
      closed_queue: "queue.Queue[list[int]]",
  ) -> SymbolCycleReport: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_symbol_cycle_drains_the_queue_and_runs_ingest(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "_run_ingest_pipeline", lambda *a, **k: calls.append(a))
    client = FakeLiveClient(positions=[])
    q: queue_mod.Queue = queue_mod.Queue()
    q.put([1])
    q.put([2, 3])                      # two separate position_cycle closes

    report = live.symbol_cycle(client, conn, q)

    assert len(calls) == 1              # coalesced into ONE pipeline run
    assert report.ingest_ran is True
    assert sorted(report.closed_ids) == [1, 2, 3]


def test_symbol_cycle_with_empty_queue_does_not_ingest(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "_run_ingest_pipeline", lambda *a, **k: calls.append(a))
    client = FakeLiveClient(positions=[])
    q: queue_mod.Queue = queue_mod.Queue()

    report = live.symbol_cycle(client, conn, q)

    assert calls == []
    assert report.ingest_ran is False
    assert report.closed_ids == []


def test_symbol_cycle_calls_serve_watches(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "serve_watches", lambda *a, **k: calls.append(a) or 0)
    client = FakeLiveClient(positions=[])
    q: queue_mod.Queue = queue_mod.Queue()
    live.symbol_cycle(client, conn, q)
    assert len(calls) == 1


def test_symbol_cycle_fulfils_one_candle_request(conn):
    # Reuses the existing seeding pattern from test_live_cycle_fulfils_one_candle_request
    # (see that test above for how a candle_requests row + symbol_specs are seeded);
    # call live.symbol_cycle(client, conn, queue_mod.Queue()) in place of live_cycle
    # and assert on report.candle_request_id / report.candle_bars_written instead of
    # LiveReport's fields.
    ...


def test_symbol_cycle_ingest_failure_does_not_raise(conn, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("bridge gone")
    monkeypatch.setattr(live, "_run_ingest_pipeline", _boom)
    client = FakeLiveClient(positions=[])
    q: queue_mod.Queue = queue_mod.Queue()
    q.put([1])

    report = live.symbol_cycle(client, conn, q)  # must not raise

    assert report.ingest_ran is False
```

(The `test_symbol_cycle_fulfils_one_candle_request` body: copy the seeding logic from the existing `test_live_cycle_fulfils_one_candle_request` at `tests/test_live.py:657` verbatim, then swap the call from `live_cycle(...)` to `live.symbol_cycle(client, conn, queue_mod.Queue())` and the assertions from `r.candle_request_id`/`r.candle_bars_written` to `report.candle_request_id`/`report.candle_bars_written`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_live.py -k symbol_cycle -v`
Expected: FAIL — `AttributeError: module 'journal.ingest.live' has no attribute 'symbol_cycle'`

- [ ] **Step 3: Implement `symbol_cycle`**

In `src/journal/ingest/live.py`, add `SymbolCycleReport` next to `PositionCycleReport`, then add:

```python
def symbol_cycle(
    client: MT5Client,
    conn: sqlite3.Connection,
    closed_queue: "queue_mod.Queue[list[int]]",
) -> SymbolCycleReport:
    """One symbol-data cycle: serve forming bars, drain any closed-position ids
    handed off by position_cycle and run the ingest pipeline once for all of
    them coalesced, then fulfil one queued candle backfill request.

    Runs on its own interval, independent of position_cycle's — see
    docs/superpowers/specs/2026-09-04-decouple-live-loop-design.md. A close
    detected by position_cycle is ingested here on THIS loop's next tick, not
    inline in the cycle that detected it: that's the whole point of the split.
    """
    observed_msc = now_ms()

    forming_bars_written = serve_watches(client, conn, observed_msc)

    closed_ids: list[int] = []
    while True:
        try:
            closed_ids.extend(closed_queue.get_nowait())
        except queue_mod.Empty:
            break

    ingest_ran = False
    if closed_ids:
        log.info("live: symbol_cycle ingesting closed position(s): %s", closed_ids)
        try:
            _run_ingest_pipeline(client, conn)
            ingest_ran = True
        except Exception:
            log.exception(
                "live: ingest pipeline failed for closed position(s) %s — "
                "symbol_cycle continues, position_cycle is unaffected", closed_ids,
            )

    candle_request_id: int | None = None
    candle_bars_written: int | None = None
    req = claim_next_request(conn)
    if req is not None:
        candle_request_id = int(req["id"])
        try:
            candle_bars_written = fulfill_request(client, conn, req, observed_msc)
        except Exception:
            log.exception(
                "live: candle request %d failed — marked failed, will not "
                "auto-retry this exact row", candle_request_id,
            )

    return SymbolCycleReport(
        observed_msc=observed_msc,
        forming_bars_written=forming_bars_written,
        ingest_ran=ingest_ran,
        closed_ids=closed_ids,
        candle_request_id=candle_request_id,
        candle_bars_written=candle_bars_written,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_live.py -k symbol_cycle -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Remove the now-redundant old tests**

Delete `test_close_triggers_pipeline_once_in_order`, `test_multiple_closes_debounced_to_one_pipeline`, `test_no_close_no_pipeline`, `test_on_close_callback_receives_closed_ids`, `test_on_closing_fires_before_the_ingest_pipeline`, `test_failed_ingest_does_not_kill_the_loop`, `test_live_cycle_fulfils_one_candle_request`, `test_cycle_order_chart_first_bulk_backfill_last`, `test_beat_refreshed_after_the_ingest_pipeline_runs` from `tests/test_live.py` — their behavior is now covered by the Task 3 + Task 4 tests (the queue push/drain tests above) plus Task 5/6's loop-level tests. `live_cycle`/`live_loop` themselves are removed in Task 6 once `cli.py` no longer calls them (Task 7) — until then leave `live_cycle` in place unmodified so these deletions don't break anything that still calls it.

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS, with the deleted test names no longer collected.

- [ ] **Step 6: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "refactor(live): extract symbol_cycle, owning serve_watches/backfill/ingest-on-close"
```

---

## Task 5: `position_loop` runner

**Files:**
- Modify: `src/journal/ingest/live.py`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `position_cycle` (Task 3), `recover_interrupted`, `_maybe_backup`, `pending_count` (all existing, unchanged).
- Produces:
  ```python
  def position_loop(
      client: MT5Client,
      conn: sqlite3.Connection,
      login: int,
      closed_queue: "queue_mod.Queue[list[int]]",
      *,
      interval_idle: float = 5.0,
      interval_busy: float = 1.0,
      trading: bool = True,
      once: bool = False,
      duration: float | None = None,
      backup_every_s: float | None = 86_400.0,
      backup_keep: int = 7,
      sleep=time.sleep,
      monotonic=time.monotonic,
      on_cycle=None,
      on_closing=None,
      stop_event: threading.Event | None = None,
  ) -> LiveLoopReport: ...
  ```
  `stop_event`, if given and set, ends the loop before the next sleep — same effect as `KeyboardInterrupt` in the current code, but usable from a controlling thread (Task 7 needs this: the main thread catches Ctrl+C and must be able to stop BOTH loop threads, and only one of them can catch the signal directly).

- [ ] **Step 1: Write the failing tests**

Copy these existing `live_loop` tests from `tests/test_live.py`, renaming `live_loop(` → `live.position_loop(` and adding a `queue_mod.Queue()` positional arg after `conn, login,` (or `conn, 7,` per each test's login value) in every call:
`test_loop_recovers_interrupted_at_startup_and_never_resends`,
`test_loop_once_runs_exactly_one_cycle`,
`test_loop_busy_interval_when_commands_pending`,
`test_loop_idle_interval_when_nothing_pending`,
`test_loop_keyboard_interrupt_stops_cleanly`,
`test_loop_once_takes_priority_over_duration`,
`test_loop_snapshots_the_database_when_none_has_been_taken`,
`test_loop_does_not_snapshot_again_until_the_interval_has_passed`,
`test_loop_can_be_told_not_to_back_up`,
`test_loop_skips_the_snapshot_while_a_trade_command_is_pending`,
`test_loop_survives_a_failing_backup`,
`test_loop_survives_the_bridge_going_away_and_resumes`,
`test_a_failed_cycle_leaves_no_open_write_transaction`,
`test_once_returns_even_when_the_cycle_fails`,
`test_a_locked_database_still_escapes_the_loop`,
`test_the_backup_still_runs_while_the_bridge_is_down`,
`test_live_cycle_writes_heartbeat` (rename to `test_position_loop_writes_heartbeat`, call `position_cycle` not `live_cycle`),
`test_loop_records_the_code_it_actually_loaded`.

Add one NEW test for the stop_event addition:

```python
def test_position_loop_stops_via_stop_event(conn):
    client = FakeLiveClient(positions=[])
    stop_event = threading.Event()
    stop_event.set()
    r = live.position_loop(client, conn, 7, queue_mod.Queue(), stop_event=stop_event)
    assert r.stopped_by == "interrupt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_live.py -k "loop and not symbol" -v`
Expected: FAIL — `AttributeError: module 'journal.ingest.live' has no attribute 'position_loop'`

- [ ] **Step 3: Implement `position_loop`**

Copy the body of the current `live_loop` (`ingest/live.py:658-758`) to a new `position_loop`, with these changes:
- Signature gains `closed_queue` (positional, after `login`) and `stop_event: threading.Event | None = None` (keyword).
- The call `live_cycle(client, conn, login, trading=trading, on_closing=on_closing)` becomes `position_cycle(client, conn, login, closed_queue, trading=trading, on_closing=on_closing)`.
- `requeue_orphaned(conn)` (currently line 702-704) is REMOVED — candle request orphans are symbol-domain, this moves to `symbol_loop` in Task 6.
- Add a stop check: right after the existing `if once: return _report("once")` check and the `duration` deadline check, add:
  ```python
  if stop_event is not None and stop_event.is_set():
      return _report("interrupt")
  ```
- The `except KeyboardInterrupt:` handler at the bottom stays — `position_loop` run standalone (as in every test above) still needs to stop on Ctrl+C; Task 7 additionally sets `stop_event` from the CLI's own signal handling so `symbol_loop` (which doesn't receive the KeyboardInterrupt directly, since only the thread the interpreter delivers the signal to does) stops too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_live.py -k "loop and not symbol" -v`
Expected: PASS (all copied tests + the new stop_event test)

- [ ] **Step 5: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "refactor(live): add position_loop runner with injectable stop_event"
```

---

## Task 6: `symbol_loop` runner, remove `live_cycle`/`live_loop`

**Files:**
- Modify: `src/journal/ingest/live.py`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `symbol_cycle` (Task 4), `requeue_orphaned` (existing, moved here from Task 5's removal).
- Produces:
  ```python
  @dataclass(frozen=True)
  class SymbolLoopReport:
      cycles: int = 0
      requeued: int = 0
      failed_cycles: int = 0
      stopped_by: str = "duration"

  def symbol_loop(
      client: MT5Client,
      conn: sqlite3.Connection,
      closed_queue: "queue_mod.Queue[list[int]]",
      *,
      interval: float = 5.0,
      once: bool = False,
      duration: float | None = None,
      sleep=time.sleep,
      monotonic=time.monotonic,
      on_cycle=None,
      stop_event: threading.Event | None = None,
  ) -> SymbolLoopReport: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_symbol_loop_requeues_orphaned_candle_requests_at_startup(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "requeue_orphaned", lambda c: calls.append(c) or 2)
    client = FakeLiveClient(positions=[])
    live.symbol_loop(client, conn, queue_mod.Queue(), once=True)
    assert len(calls) == 1


def test_symbol_loop_once_runs_exactly_one_cycle(conn):
    client = FakeLiveClient(positions=[])
    r = live.symbol_loop(client, conn, queue_mod.Queue(), once=True)
    assert r.cycles == 1
    assert r.stopped_by == "once"


def test_symbol_loop_stops_via_stop_event(conn):
    client = FakeLiveClient(positions=[])
    stop_event = threading.Event()
    stop_event.set()
    r = live.symbol_loop(client, conn, queue_mod.Queue(), stop_event=stop_event)
    assert r.stopped_by == "interrupt"


def test_symbol_loop_survives_a_failing_cycle(conn, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("bridge gone")
    monkeypatch.setattr(live, "symbol_cycle", _boom)
    client = FakeLiveClient(positions=[])
    r = live.symbol_loop(client, conn, queue_mod.Queue(), once=True)
    assert r.failed_cycles == 1
    assert r.cycles == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_live.py -k symbol_loop -v`
Expected: FAIL — `AttributeError: module 'journal.ingest.live' has no attribute 'symbol_loop'`

- [ ] **Step 3: Implement `symbol_loop`, remove `live_cycle`/`live_loop`/`LiveReport`/`LiveLoopReport`**

Add `SymbolLoopReport` next to `SymbolCycleReport`. Add `symbol_loop`, structured like `position_loop` (Task 5) but simpler — no `pending_count`-based interval switch (symbol_loop has one flat `interval`), no backup call, no `recover_interrupted` (order recovery is position-domain), startup does `requeued = requeue_orphaned(conn)` instead. Same `stop_event`/`once`/`duration`/`KeyboardInterrupt` handling shape as `position_loop`.

Then delete `live_cycle`, `live_loop`, `LiveReport`, `LiveLoopReport` — nothing calls them anymore (Task 3/4 already replaced their bodies' logic; Task 7 rewires `cli.py` off them in the same PR sequence, but by this task nothing in `ingest/live.py`'s own test file references them either, since Task 3/4/5 already migrated every test that used them). Grep to confirm before deleting:

```bash
grep -rn "live_cycle\|LiveReport\b\|\blive_loop\b\|LiveLoopReport" src/ tests/ | grep -v test_live.py
```

If this returns hits in `cli.py`, do NOT delete yet — do Task 7 first, then come back and delete in this task's Step 3 (reorder Task 6/7 if needed; the important invariant is "delete only after nothing references it").

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS, `live_cycle`/`live_loop` no longer imported or referenced anywhere in `tests/test_live.py`.

- [ ] **Step 5: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "refactor(live): add symbol_loop runner, remove the now-unused live_cycle/live_loop"
```

---

## Task 7: Wire `journal live` CLI to two threads and two interval flags

**Files:**
- Modify: `src/journal/cli.py:713-836` (the `live` command)
- Test: `tests/test_cli.py` or a new `tests/test_cli_live.py` if `test_cli.py` doesn't already cover `journal live` — check first with `grep -n "def test.*live" tests/test_cli.py`; if it's untested at the CLI layer today, add a smoke test rather than skip one (the CLI wiring is the one place this plan doesn't already have unit coverage from Tasks 1-6).

**Interfaces:**
- Consumes: `position_loop`, `symbol_loop` (Tasks 5, 6), `LockedMT5Client` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_live.py (new file if journal live has no CLI-level test yet)
from __future__ import annotations

from typer.testing import CliRunner

from journal.cli import app

runner = CliRunner()


def test_live_once_reports_both_loops(tmp_path, monkeypatch):
    # Reuses the fixture-seeding helper pattern already used in test_cli.py for
    # `journal sync`/`journal doctor` — a temp DB with one account row (see
    # `_one_account_login` in cli.py for what "already known to the store" means)
    # and a FakeMT5Client swapped in for `adapter.select.get_client`.
    ...
    result = runner.invoke(app, [
        "live", "--once", "--db", str(db_path),
        "--interval-positions", "0.01", "--interval-symbols", "0.01",
        "--no-trading",
    ])
    assert result.exit_code == 0
    assert "position cycles" in result.stdout
    assert "symbol cycles" in result.stdout
```

Fill in the seeding `...` by copying whatever `tests/test_cli.py` already does to get `journal doctor`/`journal sync` past `_one_account_login`'s friendly-exit check — that helper and its expectations are unchanged by this plan.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_live.py -v`
Expected: FAIL — old `--interval` flag doesn't accept `--interval-positions`/`--interval-symbols`, or output text doesn't match yet.

- [ ] **Step 3: Rewrite the `live` command**

Replace `interval: float = typer.Option(5.0, ...)` with two options:

```python
interval_positions: float = typer.Option(
    5.0, "--interval-positions",
    help="Idle seconds between position-monitoring cycles (drops to 1s while a command is queued).",
),
interval_symbols: float = typer.Option(
    5.0, "--interval-symbols",
    help="Seconds between symbol-data cycles (candle serving, backfill, ingest-on-close).",
),
```

Replace the body from `conn = connect(db)` through the final `typer.echo` block:

```python
import queue as queue_mod
import threading

from .ingest.live import position_loop, symbol_loop
from .ingest.locked_client import LockedMT5Client

conn_positions = connect(db)
conn_symbols = connect(db)
try:
    login = _one_account_login(conn_positions)
    raw_client = get_client()
    client = LockedMT5Client(raw_client)
    mode = "TRADING ON — will send real orders" if trading else "ingest only (--no-trading)"
    typer.echo(
        f"live: {mode}; position interval {interval_positions}s, "
        f"symbol interval {interval_symbols}s"
        + ("; auto-backup off" if no_auto_backup else "; daily auto-backup")
        + ("" if once else " — Ctrl+C to stop" + (f", max {duration}s" if duration else ""))
    )

    closed_queue: queue_mod.Queue = queue_mod.Queue()
    stop_event = threading.Event()

    position_report_holder: list = []
    symbol_report_holder: list = []

    def _run_position_loop():
        r = position_loop(
            client, conn_positions, login, closed_queue,
            interval_idle=interval_positions, trading=trading,
            once=once, duration=duration,
            backup_every_s=None if no_auto_backup else 86_400.0,
            on_cycle=_echo_cycle, on_closing=_echo_closing,
            stop_event=stop_event,
        )
        position_report_holder.append(r)

    def _run_symbol_loop():
        r = symbol_loop(
            client, conn_symbols, closed_queue,
            interval=interval_symbols, once=once, duration=duration,
            stop_event=stop_event,
        )
        symbol_report_holder.append(r)

    t_position = threading.Thread(target=_run_position_loop, name="position_loop")
    t_symbol = threading.Thread(target=_run_symbol_loop, name="symbol_loop")
    t_position.start()
    t_symbol.start()
    try:
        while t_position.is_alive() or t_symbol.is_alive():
            t_position.join(timeout=0.5)
            t_symbol.join(timeout=0.5)
    except KeyboardInterrupt:
        stop_event.set()
        t_position.join()
        t_symbol.join()

except sqlite3.OperationalError as e:
    if "locked" in str(e).lower():
        typer.echo(
            "live: database TERKUNCI — kemungkinan ada `journal live` lain yang "
            "sedang menulis DB ini. Jalankan HANYA SATU `journal live` sekaligus "
            "(satu proses saja yang boleh memegang bridge). `journal serve` boleh "
            "jalan bersamaan."
        )
        raise typer.Exit(1)
    raise
finally:
    conn_positions.close()
    conn_symbols.close()

r = position_report_holder[0] if position_report_holder else None
rs = symbol_report_holder[0] if symbol_report_holder else None
typer.echo("== live ==")
if r is not None:
    typer.echo(f"position cycles: {r.cycles}")
    if r.failed_cycles:
        typer.echo(f"  failed:        {r.failed_cycles} cycle(s) raised (see the log)")
    typer.echo(f"  recovered:     {r.recovered} interrupted command(s) at startup")
    typer.echo(f"  trading:       {'on' if trading else 'off (--no-trading)'}")
    typer.echo(f"  stopped by:    {r.stopped_by}")
if rs is not None:
    typer.echo(f"symbol cycles:   {rs.cycles}")
    if rs.failed_cycles:
        typer.echo(f"  failed:        {rs.failed_cycles} cycle(s) raised (see the log)")
    typer.echo(f"  requeued:      {rs.requeued} orphaned candle request(s) at startup")
    typer.echo(f"  stopped by:    {rs.stopped_by}")
```

Note the `sqlite3.OperationalError` "locked" handling still works the same way: it's raised by whichever loop hits it first, inside its own thread's run function — but a raise inside a `threading.Thread`'s target does NOT propagate to the main thread by default. Add:

```python
def _run_position_loop():
    try:
        r = position_loop(...)
        position_report_holder.append(r)
    except Exception as e:
        position_report_holder.append(e)

# ... same pattern for _run_symbol_loop, then after both threads join:

for holder, name in ((position_report_holder, "position"), (symbol_report_holder, "symbol")):
    if holder and isinstance(holder[0], Exception):
        raise holder[0]
```

replacing the bare `position_report_holder.append(r)` / `symbol_report_holder.append(r)` lines above with this try/except form so the existing `except sqlite3.OperationalError` block outside the thread-start code still catches the re-raised exception on the main thread.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_live.py -v`
Expected: PASS

- [ ] **Step 5: Full regression check**

Run: `uv run pytest -v`
Expected: PASS, no test anywhere still references `live_cycle`/`live_loop`/`LiveReport`/`LiveLoopReport`/the old single `--interval` flag.

- [ ] **Step 6: Commit**

```bash
git add src/journal/cli.py tests/test_cli_live.py
git commit -m "feat(live): run position and symbol loops as independent threads with separate intervals"
```

---

## Task 8: Lock/queue integration test + docs

**Files:**
- Test: `tests/test_live.py`
- Modify: `docs/HANDOFF.md` (§ CURRENT STATE — append one entry)

**Interfaces:** none new — this task only adds coverage across the seam Tasks 1-7 built, and records the change in the project's own current-state log per the project's own convention (see `docs/HANDOFF.md` header for the expected entry shape).

- [ ] **Step 1: Write the integration test**

```python
def test_close_detected_by_position_cycle_is_ingested_by_a_later_symbol_cycle(conn, monkeypatch):
    """End-to-end proof of the spec's core claim: a close never runs ingest
    inline in the cycle that detected it."""
    calls = []
    monkeypatch.setattr(live, "_run_ingest_pipeline", lambda *a, **k: calls.append(a))
    q: queue_mod.Queue = queue_mod.Queue()

    client_open = FakeLiveClient(positions=[_pos(1, symbol="XAUUSDc")])
    live.position_cycle(client_open, conn, 7, q, trading=False)

    client_closed = FakeLiveClient(positions=[])
    report = live.position_cycle(client_closed, conn, 7, q, trading=False)
    assert report.closed_ids == [1]
    assert calls == []                          # NOT ingested yet

    sym_report = live.symbol_cycle(client_closed, conn, q)
    assert calls == [(client_closed, conn)] or len(calls) == 1  # ingested NOW
    assert sym_report.closed_ids == [1]


def test_position_loop_and_symbol_loop_share_a_locked_client_without_deadlock(conn, monkeypatch):
    """Smoke test: both loops ticking concurrently against a LockedMT5Client
    wrapping a client with an injected delay must not deadlock and both must
    make progress."""
    import time as time_mod

    class _SlowClient(FakeLiveClient):
        def positions_get(self):
            time_mod.sleep(0.01)
            return super().positions_get()

        def copy_rates_range(self, *a, **k):
            time_mod.sleep(0.01)
            return super().copy_rates_range(*a, **k) if hasattr(super(), "copy_rates_range") else []

    from journal.ingest.locked_client import LockedMT5Client
    client = LockedMT5Client(_SlowClient(positions=[]))
    q: queue_mod.Queue = queue_mod.Queue()
    stop_event = threading.Event()

    def _stop_soon():
        time_mod.sleep(0.2)
        stop_event.set()

    threading.Thread(target=_stop_soon).start()
    r1 = live.position_loop(client, conn, 7, q, interval_idle=0.01, interval_busy=0.01,
                            trading=False, stop_event=stop_event)
    assert r1.cycles > 0
```

(Adapt `_SlowClient.copy_rates_range` to whatever `FakeLiveClient`'s existing base actually implements — check `journal.adapter.fake.FakeMT5Client.copy_rates_range`'s real signature/return before writing the `hasattr` fallback; it likely already returns `[]` or a fixed bar list, in which case just call it directly without the `hasattr` guard.)

- [ ] **Step 2: Run tests to verify they fail, then pass after Steps 3-6 of Tasks 1-7 are all in place**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS — this task adds no new production code, only tests, so if any of these fail, it means an earlier task's implementation has a gap; fix the earlier task, don't add workaround code here.

- [ ] **Step 3: Update `docs/HANDOFF.md`**

Add one entry to § CURRENT STATE (top of the list, following the file's existing entry format — read a couple of existing entries first to match the shape exactly) stating: `journal live` now runs `position_loop`/`symbol_loop` as two threads with independently configurable `--interval-positions`/`--interval-symbols`; a position close hands off to symbol_cycle via an in-memory queue instead of ingesting inline, so candle serving and backfill are no longer blocked by ingest-on-close. Reference this plan's spec path.

- [ ] **Step 4: Full test suite + `journal rebuild` sanity check**

Run: `uv run pytest -v` — paste the full pass/fail summary.
Run: `uv run journal rebuild` against a real or fixture DB — confirm it still succeeds (CLAUDE.md Definition of Done).

- [ ] **Step 5: Commit**

```bash
git add tests/test_live.py docs/HANDOFF.md
git commit -m "test(live): integration coverage for close-handoff and lock contention; update HANDOFF"
```

---

## Self-Review Notes

- **Spec coverage:** "Two threads, one process" → Tasks 5-7. "Close-event handoff: in-memory queue" → Tasks 3, 4, 8. "Bridge access: one lock" → Task 1, wired in Task 7. "SQLite write concurrency" → no new code (relies on existing `store/db.py` `busy_timeout` + fetch-then-write pattern, called out in Task-level comments, not a separate task per spec's own framing — "no new pattern needed"). "Error isolation" → Task 5/6 (each loop's own try/except, unchanged shape from today's `live_loop`) + Task 7 (thread exceptions re-raised on join). "Unaffected by this change" → verified `code_fingerprint()` needs no update (Task 6's `mark_started` call stays in `position_loop`, unchanged from today).
- **Placeholder scan:** Task 7's CLI seeding test has a `...` — this is the one spot deliberately deferring to an existing, already-written pattern in `tests/test_cli.py` rather than inventing new fixture code blind; the plan tells the implementer exactly where to copy from. Task 4's candle-request test and Task 8's `_SlowClient.copy_rates_range` similarly point at exact existing code to copy/check rather than describing invented behavior.
- **Type consistency:** `PositionCycleReport`/`SymbolCycleReport`/`SymbolLoopReport` field names checked against every call site across Tasks 3-8 (`closed_ids`, `command_id`, `command_status`, `paper_resolved` on position; `ingest_ran`, `candle_request_id`, `candle_bars_written`, `forming_bars_written` on symbol) — consistent throughout.
