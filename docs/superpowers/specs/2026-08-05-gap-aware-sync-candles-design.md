# Gap-aware `sync_candles` — keep the live bar streaming during ingest

Date: 2026-08-05
Status: approved, ready for planning

## Problem

When a position closes, `journal live` runs the on-close ingest pipeline
(`ingest/live.py:_run_ingest_pipeline` — sync → rebuild → candles → rebuild)
synchronously inside `live_cycle`. Measured on this account it can take about
five minutes. For that whole stretch the loop is a single serial call, so
everything else in the cycle stops:

- `serve_watches` (`ingest/live_candles.py:23`) does not run — the forming bar
  freezes and `/chart`'s live edge sits still.
- `live_store.beat` does not fire — the liveness indicator reads stale, so the
  UI claims `journal live` is down while it is in fact working.
- `_execute_one_command` does not run — a queued SL/TP or close order waits
  behind history ingestion.
- `claim_next_request` does not run — chart backfill stalls.

The cost is concentrated in one place. `sync_candles`
(`ingest/candles.py:49`) loops over **every** closed trade and issues one
`copy_rates_range` per trade. The DB currently holds **123** closed trades, so
every single position close triggers 123 bridge round trips. At roughly 2.5 s
each that is the five minutes.

Those fetches are almost entirely redundant. `sync_candles` *writes* coverage
(`candles_store.record_fetch`) but never *reads* it, so it re-fetches windows
it already has, every run, forever. The correct pattern already exists one file
over: `ingest/candle_fill.py:fill_range` reads coverage, computes
`candles_store.missing_ranges`, and fetches only the gaps.

## Scope

Make `sync_candles` gap-aware by delegating to `fill_range`, and cap how many
windows one invocation may fetch.

Out of scope, and deliberately so:

- No worker thread. The bridge's thread-safety is unproven and SQLite has one
  WAL writer; concurrency here buys a class of bug we do not need.
- No new job queue, no new table, no migration. The ordering inside
  `live_cycle` is already correct (`serve_watches` and `beat` run at steps 3–4,
  ahead of ingest at step 5) — the fix is to stop ingest being slow, not to
  restructure the loop around its slowness.
- No separate "live only fetches the trade that just closed" code path. One
  behaviour, one code path, for both `journal candles` and the live pipeline.

## Decisions taken

| Question | Decision |
|---|---|
| Target | Make ingest fast, keep the loop serial |
| How | `sync_candles` calls `fill_range` per trade — skip covered ranges |
| Scope of skipping | Coverage-driven only; no symbol/date narrowing, no CLI-vs-live split |
| Safety valve | Hard cap of N *fetched* windows per invocation |
| Cap unit | Count of windows, not elapsed seconds (no clock to inject, deterministic in tests) |
| Who the cap binds | `journal live` only. `sync_candles` takes `max_windows: int \| None = _MAX_FETCH_WINDOWS`; `journal candles` passes `None` and runs uncapped (amended during planning — see below) |
| Trade order | `close_time_msc DESC` — the trade that just closed is always served first |

## Design

### 1. `sync_candles` delegates to `fill_range`

The per-trade body of `sync_candles` — the `copy_rates_range` call, the
`insert_candle` loop, the `stored` list, and `record_fetch` — is deleted and
replaced by a single call:

```python
bars_new += fill_range(client, conn, r["symbol"], tf, from_msc, to_msc, now)
```

`fill_range` already does everything that block did, and does it better:

- reads coverage and fetches only `missing_ranges` — a fully covered trade
  costs one SELECT and **zero** bridge calls;
- two-phase (all network first, then one short write transaction), so it never
  holds SQLite's single WAL writer slot across a bridge round trip — the
  failure mode recorded in memory `candle-fill-write-lock-across-bridge`;
- drops any bar at or after the current bucket, so a still-forming bar is never
  frozen into `candles` where `INSERT OR IGNORE` would keep the real closed bar
  out later.

Window selection (`choose_timeframe`, `window_for`) and the open-trade skip are
unchanged. The `now = now_ms()` clamp stays and is passed through to
`fill_range` as `now_msc`.

Consequence to accept: `fill_range` commits per call rather than once at the
end of `sync_candles`. Each commit is short and follows a completed fetch
phase, which is strictly better for lock contention than one long transaction.

### 2. Ordering and the cap

The trades query gains `ORDER BY close_time_msc DESC`. The freshly closed trade
is therefore processed first in every ingest, so the position that triggered the
pipeline always has its candles — and therefore its MAE/MFE on the second
rebuild — in that same run.

A module constant `_MAX_FETCH_WINDOWS = 5` bounds the work. A window counts
against the cap only when it actually needed a fetch (`missing_ranges`
non-empty); fully covered trades are free and never count. Once the count
reaches the cap the loop stops and the remaining trades are left for the next
invocation.

This gives a hard ceiling of roughly `5 × 2.5 s ≈ 12 s` per ingest no matter how
large the backlog. It covers both known bad cases: the first run after this
change (coverage holds only 11 rows against 123 closed trades) and a restart
after `journal live` has been down for days.

Deciding whether a window needs a fetch must not cost a second coverage read.
`fill_range` returns `bars_new`, which cannot distinguish "already covered"
from "fetched and the range was genuinely empty". So `sync_candles` computes
`missing_ranges` itself for the cap decision and skips the call entirely when
the list is empty:

```python
covered = candles_store.read_coverage(conn, symbol, tf)
if not candles_store.missing_ranges(covered, (from_msc, to_msc)):
    continue                      # free, does not count against the cap
if windows_fetched >= _MAX_FETCH_WINDOWS:
    windows_pending += 1
    continue                      # backlog — next ingest takes it
bars_new += fill_range(...)
windows_fetched += 1
```

`fill_range` recomputes coverage internally. That duplicate read is one cheap
SELECT and keeps `fill_range`'s signature and single-responsibility intact; a
"tell me what's missing" out-parameter would be worse.

### 3. Report and CLI

`CandlesReport` drops `bars_seen` and gains two fields:

| Field | Meaning |
|---|---|
| `windows_fetched` | Windows that hit the bridge this run |
| `windows_pending` | Closed trades left untouched because the cap closed |

`bars_seen` is removed rather than kept: with gap-aware fetching, "bars the
bridge returned" no longer approximates "bars we looked at", and its only
consumer is one CLI line. No test asserts it.

`trades_seen` keeps its name but now means *trades processed this run* —
including the ones skipped as already covered, excluding the ones the cap
deferred. `trades_skipped_open` is unchanged.

`cli.py:369` currently prints:

```
bars:           {r.bars_new} new, {r.bars_seen - r.bars_new} already had
```

It becomes a bars line plus a backlog line, so a capped run is visible rather
than silently partial:

```
bars:           {r.bars_new} new from {r.windows_fetched} window(s) fetched
pending:        {r.windows_pending} window(s) left for the next run
```

## Testing

TDD, per CLAUDE.md rule 7. All under `FakeMT5Client`, no bridge.
New tests in `tests/test_candles.py`:

1. **Second run makes zero bridge calls.** Wrap the fake client with a counter
   on `copy_rates_range`. Run `sync_candles` twice; assert the second run's
   count is 0 and `windows_fetched == 0`. This is the test that proves the
   five-minute stall is gone; it fails against the current code.
2. **The cap holds.** Seed more closed trades than `_MAX_FETCH_WINDOWS` with no
   coverage. Assert exactly `_MAX_FETCH_WINDOWS` bridge fetches,
   `windows_pending > 0`, and that the fetched windows are the ones with the
   newest `close_time_msc`.
3. **The backlog drains.** Run `sync_candles` repeatedly until
   `windows_pending == 0`; assert every window ends up covered and no window is
   fetched twice.

Existing tests that must stay green unchanged: `test_sync_candles_is_idempotent`,
`test_sync_candles_writes_ms_not_seconds`,
`test_sync_candles_rejects_seconds_leaked_as_msc`,
`test_sync_candles_skips_open_trades`, `test_sync_candles_populates_coverage`,
`test_sync_candles_does_not_claim_coverage_past_the_bars_it_got`.

Verification beyond the unit tests: full `uv run pytest`, and `journal rebuild`
must still succeed (definition of done).

## Accepted trade-offs

- With a large backlog, MAE/MFE for **older** trades completes over several
  ingest runs rather than one. The trade that just closed is never affected —
  `close_time_msc DESC` puts it first.
- `_MAX_FETCH_WINDOWS = 5` is a tuning knob for real hardware and a real broker,
  not a derived constant. It lives as a named module constant so it can be
  raised or lowered against a measured round-trip time.
- The loop stays serial. If a *single* `fill_range` call ever blocks for
  minutes on its own, this design does not help; nothing observed suggests it
  does, and the queue-per-cycle redesign remains available if that changes.

## Amendments made during planning

Two things only surfaced when the plan was checked line-by-line against the
code. Both are recorded here so spec and plan agree.

**1. The cap needs an off switch.** As first written the cap bound every caller,
including `journal candles`. With 118 uncovered windows against a cap of 5, that
made priming the backlog a ~24-invocation chore. `sync_candles` therefore takes
`max_windows: int | None = _MAX_FETCH_WINDOWS`, and the CLI passes `None`. The
fetch logic itself stays single-path — which is what the "no CLI-vs-live split"
decision above was protecting — while the foreground command a human is watching
can drain a backlog in one deliberate run.

**2. The zero-refetch test needs its own fixture.** `adapter/fake.py`'s
`copy_rates_range` ignores `date_from`/`date_to` and returns every bar under the
fixture key. The existing `_write_rates` helper writes bars that stop 20 minutes
after the trade opens, short of the window's end at `close + PAD_BARS`
(`PAD_BARS = 15`). `record_fetch` claims only to the last bar returned, so under
the fake that tail can never seal and a second run still fetches once. The test
uses a new `_write_rates_multi` helper that writes bars past `to_msc`. This is a
fake-client artifact, not a production behaviour: the real bridge honours the
requested range, so an old trade's tail seals on the run after its first.

## Files touched

- `src/journal/ingest/candles.py` — the change
- `src/journal/cli.py` — the two report lines
- `tests/test_candles.py` — three new tests
