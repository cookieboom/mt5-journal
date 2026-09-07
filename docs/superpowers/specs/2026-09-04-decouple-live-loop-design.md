# Decouple position monitoring from symbol-data sync in `journal live`

**Status:** approved, ready for implementation plan
**Author:** Claude (session 01ULoqg23eHg2H2YsJAhrsDs), with cookieboom

## Problem

`live_cycle` in `src/journal/ingest/live.py` runs seven jobs serially, once per
sleep tick (`interval_idle=5s` / `interval_busy=1s`, both hard-coded and
shared):

1. `positions_get()` once, feeds `poll_once` (SL/TP snapshots)
2. close detection + `open_positions` mirror replace
3. `serve_watches` (forming-bar streaming for `/chart`)
4. liveness beacon
4b. `paper_step` (paper-trading tick)
5. **on close: `_run_ingest_pipeline`** — `deals.sync` → `reconstruct.rebuild`
   → `candles.sync_candles` → `reconstruct.rebuild` — synchronous, can run
   seconds to minutes (documented history of a ~4-minute freeze, since
   mitigated but not eliminated)
6. execute one `trade_commands` row
7. fulfil one `candle_requests` row (browser-triggered backfill)

This is two genuinely different concerns sharing one cadence and one thread:

- **Position monitoring** (jobs 1, 2, 4b, 6): safety-critical, wants a short,
  predictable interval. SL/TP snapshot loss or delayed command execution is
  real-money risk.
- **Symbol data sync** (jobs 3, 5, 7): market data plumbing. Candle bars only
  change once per timeframe bucket; backfill has no deadline.

Two concrete costs of the coupling:

- **No independent tuning.** Both concerns are forced to the same
  `interval_idle`/`interval_busy`, even though they have different natural
  cadences.
- **Ingest-on-close blocks symbol data.** Step 5 runs inline in the same
  cycle as steps 3/6/7. A closed position freezes candle serving, command
  execution, and backfill fulfillment for however long the sync+rebuild+
  candle pipeline takes — the loop only "beats" a second time to fake
  liveness (`live.py` lines 21-24, 559-567); it does not keep candles or the
  command queue moving.

## Goals

- Position monitoring and symbol-data sync run on independently configurable
  intervals.
- A position close never blocks candle serving, backfill fulfillment, or
  command execution — the ingest pipeline (`_run_ingest_pipeline`) must not
  run inline in the position path.
- No new process, no new bridge connection to supervise. `journal live`
  stays the single thing that owns the bridge and issues orders (CLAUDE.md /
  `live.py` header invariant).

## Non-goals

- Changing what any single job does (poll_once, serve_watches,
  `_run_ingest_pipeline`, command execution semantics) — this is a
  scheduling/threading change, not a logic change.
- Multi-account or multi-bridge-connection support.
- Changing the DB schema.

## Design

### Two threads, one process

`ingest/live.py` splits into two loop functions, both started by
`journal live` in the same process:

- **`position_loop`** — jobs 1, 2, 4 (beacon), 4b, 6, plus `_maybe_backup`
  (unchanged: it already refuses while a command is pending, which only the
  position loop can see cheaply). Interval: `--interval-positions`
  (default matches today's `interval_idle`/`interval_busy` pair: 5s idle /
  1s busy).
- **`symbol_loop`** — job 3 (`serve_watches`), job 7 (candle_request
  fulfillment), and job 5 (`_run_ingest_pipeline`, moved here — see below).
  Interval: `--interval-symbols` (default looser, e.g. 5s — candle bars
  only change once per bucket, so this can run less eagerly than the
  position loop without losing data).

Both intervals are plain CLI flags on `journal live`, independently settable.
No new process, no new bridge login: both threads share the single
`MT5Client` instance `journal live` already logs in with at startup (all
calls to it go through the lock below). Each thread opens its own
`sqlite3.Connection` against the same DB file — normal sqlite practice for
concurrent writers, and how the project's other concurrent-write paths
already work (see SQLite write concurrency below).

### Close-event handoff: in-memory queue

`position_loop` still does the close detection it does today (`prior_ids -
live_ids`, read before `_replace_open_positions`). Instead of calling
`_run_ingest_pipeline` inline, it puts `closed_ids` onto a `queue.Queue`
shared between the two threads.

`symbol_loop`, each cycle, drains the queue non-blockingly
(`queue.get_nowait()` in a loop until `Empty`) and — if anything was
drained — runs `_run_ingest_pipeline` once, coalesced across everything
drained that cycle (same coalescing behavior as today, just decoupled from
the position cadence that discovered the close).

Effect: a closed position's SL/TP mirror update and command-queue processing
are unaffected by ingest latency. The trade-off, stated plainly: the
freshly-closed trade's `trades` row lags by up to one `interval_symbols`
tick instead of appearing the instant the same cycle's ingest finishes. This
is visible today through `journal status`'s existing "unrebuilt trades"
check — no new visibility gap, just a slightly longer normal window for it.

### Bridge access: one lock

`NativeMT5Client` (Windows terminal, `adapter/native.py`) has no documented
thread-safety guarantee for concurrent calls — the underlying `MetaTrader5`
package talks to one terminal connection per process and is not verified
safe under concurrent access from two threads. `LiveMT5Client` (the
Docker/HTTP bridge) is probably fine concurrently (stateless HTTP calls) but
gets the same treatment for consistency and to avoid a two-tier safety
story.

A single module-level `threading.Lock` in `ingest/live.py` wraps every
`client.*` call from either loop (`positions_get`, `symbol_info_tick`,
`copy_rates_range`, `order_check`, `order_send`, etc.). This serializes
bridge round trips but NOT sleep timing, DB writes, or anything else — a
loop blocked waiting for the lock is still just waiting on the bridge, which
is the same real-world constraint that exists today (one bridge, one
connection), just made explicit instead of implicit-via-single-thread.

### SQLite write concurrency

Two threads now write concurrently: `position_loop` writes
`open_positions`/`sl_tp_snapshots`/`trade_commands`/`live_heartbeat`;
`symbol_loop` writes `candles`/`deals_raw`/`trades`/`candle_requests`. This
is the same "fetch from bridge, then take a short write transaction" pattern
the project already uses to avoid holding the WAL writer across a bridge
round trip (see `candle-fill-write-lock-across-bridge` and
`deals-sync-holds-write-lock` fixes) — no new pattern, just two callers of
an already-hardened one. `store/db.py`'s `busy_timeout` handles ordinary
lock contention between the two threads' short write transactions.

### Error isolation

Each loop keeps its own try/except-per-cycle retry logic (mirrors today's
`live_loop`: a cycle that raises is rolled back, logged, retried next tick —
see `live.py` lines 717-745). The two loops now fail independently: a
`symbol_loop` crash (e.g., a bad candle response) no longer has any way to
take down `position_loop`, and vice versa. `journal live` as a whole is
considered down only if both loops die (top-level supervisor joins both
threads; if either thread's run method exits without `KeyboardInterrupt`,
same "second `journal live` on this DB" / sqlite `locked` re-raise behavior
applies, restart the whole process).

### Unaffected by this change

- `code_fingerprint()` (`store/health.py`) hashes whatever's in
  `sys.modules` generically — no hardcoded module list to update when
  `live.py` splits into more functions/files.
- `poller.py` (`poll_once`/`poll_loop`, the SL/TP-only standalone poller) is
  a separate entry point, untouched.
- Schema, CLI commands other than `journal live`'s new flags, adapter
  Protocol.

## Testing

- Existing `test_live.py` coverage for `live_cycle` splits along the new
  seam: `position_cycle` tests (mirror, close-detect, command exec, paper)
  and `symbol_cycle` tests (serve_watches, candle_request, ingest pipeline)
  become separate unit tests, each still using the existing fakes
  (`FakeMT5Client`, `FakeLiveClientWithRates`, `FakeOpenClient`, etc.) — no
  new fake infrastructure needed.
- New: one integration-style test asserting the queue handoff — a close
  detected in `position_cycle` does NOT run `_run_ingest_pipeline` inline;
  a subsequent `symbol_cycle` call drains the queue and runs it.
  `FlakyClient` (already in `test_live.py`) reused to assert a `symbol_loop`
  failure doesn't affect `position_loop`'s next cycle, and vice versa.
- New: a lock-contention smoke test — both loops "ticking" against a fake
  client with an injected delay does not deadlock and both make progress.

## Rollout

Single branch, single spec — this is one cohesive change (loop split +
queue handoff + lock + CLI flags all serve the same goal and are not
independently shippable). Implementation plan (`writing-plans`) will break
it into commit-sized tasks, but no partial/flagged rollout: `journal live`
switches from one loop to two in one release, defaults chosen so a user who
doesn't pass the new flags sees no behavior change beyond "ingest no longer
blocks candles."
