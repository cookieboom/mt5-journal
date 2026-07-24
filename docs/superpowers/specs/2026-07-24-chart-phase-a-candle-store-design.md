# Chart segment — Phase A: Smart Candle Store + JSON candle API

**Date:** 2026-07-24
**Status:** Approved (design), pending implementation plan
**Milestone:** New `chart` segment (TradingView-style interactive chart + training)

---

## Context: the larger feature (4 phases)

The user wants a new **`chart`** segment in the SPA, modelled on TradingView:

- An interactive candlestick chart the user can pan (left/right, up/down) and
  zoom, rendered from MT5 data.
- Symbol info panel + open positions with their SL/TP drawn on the chart.
- Chart settings (background colour, render time range, etc.).
- A **training / replay** mode: a popup configures the session (e.g. number of
  sequences, time range); the chart renders from a random past moment with the
  future hidden; the user advances time bar-by-bar, opens **fake** positions with
  SL/TP, and a sequence ends when the fake position closes (manually or via
  SL/TP). Training results are persisted **separately** from real trades and can
  be reviewed later.

This is decomposed into **four phases**, each with its own spec → plan →
implementation → merge:

- **Phase A (this doc):** foundation — a smart candle store + a JSON candle API.
- **Phase B:** the interactive chart page (candlestick via `lightweight-charts`,
  pan/zoom, symbol/timeframe switching, live-position + SL/TP overlay, basic
  settings).
- **Phase C:** full chart settings panel + persisted preferences.
- **Phase D:** training / replay mode + training tables + results review.

Phases B–D are **out of scope** here and get their own brainstorm later. This
spec commits only to Phase A.

### Decisions carried in from brainstorming

- **Data source: hybrid.** Serve from the DB; fill missing ranges from the MT5
  bridge on demand and cache them, accumulating coverage over time.
- **On-demand fill is queue-mediated.** The web never touches the bridge (M9
  boundary); it enqueues a candle request that `journal live` fulfils. A separate
  `candle_requests` queue, not the money-critical `trade_commands`.
- **Chart library (Phase B): `lightweight-charts`** (TradingView's MIT library).
  Not used in Phase A, but the API shape is chosen to feed it cleanly.
- **Training persistence (Phase D): separate tables**, fully isolated from real
  `trades`. Not built in Phase A.
- **Unified store:** the new store is the single source of candle data; the
  existing per-timeframe `candles` table and the trade PNG renderer are migrated
  onto it (no second candle system).
- **Wide high-timeframe views:** store M1 as the fine-grain source **and** cache
  native higher-timeframe bars fetched from the bridge, so zooming out on H1/H4/
  D1 over long ranges does not force fetching millions of M1 bars.

---

## Phase A goal

A single **candle store service** that any consumer (the new chart, training,
the legacy trade renderer) asks for `(symbol, timeframe, from, to)` and gets back
a correct, gap-aware OHLC series — filling missing ranges from the bridge on
demand, caching them, and never producing a silently-wrong aggregated bar.

Plus the HTTP surface (`GET /api/candles`) that Phase B's chart consumes.

---

## Non-goals (Phase A)

- No interactive chart UI (Phase B).
- No `lightweight-charts` dependency added yet (Phase B).
- No training tables or fake positions (Phase D).
- No new real-time streaming; fills are request-driven, range-based pulls.
- No change to how deals/trades are reconstructed. Candles remain **cache, not
  data** (Hard rule 6) — everything in the store must be re-fetchable.

---

## Architecture boundary: who talks to the bridge

**The web process must never touch the MT5 bridge.** M9 established this: only
the single `journal live` process holds the bridge connection, and `web/` reads
from DB tables that `live` fills. This is what keeps Hard rules 1 and 12 literally
true inside `web/` (see the `open_positions` / `trade_commands` comments in
`schema.sql`). Two processes hitting the MT5 terminal at once is exactly what
that boundary avoids.

Therefore on-demand fill is **queue-mediated**, mirroring M9's command pattern:
the web enqueues a *candle request*; `journal live` claims it, fetches via the
bridge, and writes candles + coverage; the browser re-polls the read-only API and
sees coverage grow.

**A separate, simpler queue — NOT `trade_commands`.** `trade_commands` is
money-critical: claim-once, write-once intent, and `recover_interrupted` marks any
orphaned row `failed` because re-sending an order can duplicate a real position.
Candle fetches are **idempotent and safe to retry**, so they must not inherit any
of that. They get their own `candle_requests` table whose orphan-recovery simply
**re-queues**, never fails-with-scary-message.

---

## Architecture & modules

Split by responsibility: a **pure aggregator**, a **pure DB store**, a **fill
engine** (the only part that touches the bridge), a **request queue**, and the
web/CLI/live wiring.

```
src/journal/domain/resample.py        NEW  pure M1 → any-TF aggregation + bucket math (TDD)
src/journal/store/candles_store.py    NEW  pure DB: read_candles / coverage read+merge / insert / missing_ranges
src/journal/ingest/candle_fill.py     NEW  fill_range(client, conn, ...): bridge fetch → insert → record coverage
src/journal/store/candle_queue.py     NEW  candle_requests queue: request / claim / fulfill / requeue_orphaned
src/journal/store/migrations/003_*.sql NEW  candle_coverage + candle_requests tables
src/journal/web/api.py                EDIT candles_payload(...) builder (DB-read + enqueue)
src/journal/web/app.py                EDIT GET /api/candles route
src/journal/ingest/live.py            EDIT live_cycle fulfils one candle request per cycle
src/journal/cli.py                    EDIT candles-warm / candles-coverage commands
src/journal/render/chart.py           EDIT read candles via candles_store.read_candles (behaviour unchanged)
src/journal/ingest/candles.py         EDIT insert + record coverage via the shared store (unification)
```

- `resample.py` — **pure, I/O-free** (Hard rule 7, tests first).
- `candles_store.py` — **pure DB**, no bridge. Safe to import from `web/` and
  from `render/` (which stays pure-DB).
- `candle_fill.py` — the **only** new module that calls the `MT5Client` Protocol
  (`copy_rates_range`). Used by `journal live` and by `candles-warm`. Never
  imported by `web/`.
- `candle_queue.py` — pure DB queue ops. `request` is called by `web/`; `claim` /
  `fulfill` / `requeue_orphaned` are called by `journal live`.

### Key signatures (locked so tasks agree)

```python
# resample.py
def bucket_start(time_msc: int, timeframe: str) -> int      # server-time bucket open
def resample_m1(m1: list[Candle], timeframe: str,
                covered: list[tuple[int, int]] | None = None) -> list[Candle]

# candles_store.py  (pure DB)
def read_candles(conn, symbol, timeframe, from_ms, to_ms) -> list[sqlite3.Row]
def read_coverage(conn, symbol, timeframe) -> list[tuple[int, int]]        # merged, ascending
def record_coverage(conn, symbol, timeframe, from_ms, to_ms) -> None       # merge + rewrite
def missing_ranges(covered: list[tuple[int, int]], want: tuple[int, int]) -> list[tuple[int, int]]
def insert_candle(conn, symbol, timeframe, c: Candle) -> int               # INSERT OR IGNORE, Trap-15 tripwire

# candle_fill.py  (touches bridge)
def fill_range(client, conn, symbol, timeframe, from_ms, to_ms) -> int     # returns bars_new

# candle_queue.py  (pure DB)
def request_candles(conn, symbol, timeframe, from_ms, to_ms) -> int        # dedupes; returns request id
def claim_next_request(conn) -> sqlite3.Row | None
def fulfill_request(client, conn, req) -> int                              # calls fill_range; returns bars_new
def requeue_orphaned(conn) -> int                                          # claimed→pending on startup
```

---

## Data model & migration (003)

- **Keep `candles` unchanged** (multi-timeframe cache):
  `PRIMARY KEY (symbol, timeframe, time_msc)`, OHLC + volumes, `time_msc` = bar
  open time in epoch **ms**, server time.

- **New `candle_coverage`** — which `[from, to]` ranges have actually been
  fetched, **per (symbol, timeframe)**. Distinguishes "empty because the market
  was closed / weekend" (fetched, no bars) from "empty because never fetched".

  ```sql
  CREATE TABLE IF NOT EXISTS candle_coverage (
      symbol     TEXT NOT NULL,
      timeframe  TEXT NOT NULL,       -- 'M1','M5','M15','H1','H4','D1'
      from_msc   INTEGER NOT NULL,    -- inclusive, bar-open, server time (ms)
      to_msc     INTEGER NOT NULL,    -- inclusive, bar-open, server time (ms)
      PRIMARY KEY (symbol, timeframe, from_msc)
  );
  ```

  Intervals are **merged on insert** (touching/overlapping ranges coalesced) so
  coverage stays a minimal disjoint set per (symbol, timeframe) — the "stitch new
  data onto what already exists" behaviour.

- **New `candle_requests`** — the on-demand fill queue the web writes and `live`
  drains. Simpler than `trade_commands`: idempotent, retry-safe.

  ```sql
  CREATE TABLE IF NOT EXISTS candle_requests (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol        TEXT NOT NULL,
      timeframe     TEXT NOT NULL,
      from_msc      INTEGER NOT NULL,
      to_msc        INTEGER NOT NULL,
      status        TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|done|failed
      requested_msc INTEGER NOT NULL,
      claimed_msc   INTEGER,
      completed_msc INTEGER,
      bars_written  INTEGER,
      error         TEXT
  );
  ```

  `request_candles` **dedupes**: if an identical `(symbol, timeframe, from, to)`
  row is already `pending`/`claimed`, or the range is already covered, it returns
  that id / a sentinel instead of piling on duplicates.

- Ships as **migration `003_candle_store.sql`**; the **same DDL is added to
  `schema.sql`** for fresh DBs (both paths must match —
  `test_migrations.py::test_migrated_db_matches_a_fresh_db` compares them).
  `SCHEMA_VERSION` in `store/db.py` bumps `2 → 3`;
  `test_migrations.py::test_schema_version_is_2` updates to `…_is_3`.

- **Timestamps** stay epoch-ms integers, server time (Hard rule 3). Bucketing uses
  server time; `server_utc_offset_s` re-measured (confirmed `0` today → buckets
  align to UTC midnight, but not assumed).

---

## Fill engine (`candle_fill.fill_range`)

The one place that fetches from the bridge. For `(symbol, timeframe, from, to)`:

1. `covered = read_coverage(...)`; `gaps = missing_ranges(covered, (from, to))`.
2. For each gap: `client.copy_rates_range(symbol, timeframe, dt(gap.from),
   dt(gap.to))` → `insert_candle` each bar → `record_coverage(gap.from, gap.to)`
   **even if zero bars returned** (that is how genuinely-empty ranges are
   remembered and never re-fetched).
3. Return `bars_new`.

Idempotent: re-running fills nothing already covered.

---

## Read + aggregation (`candles_store` + `resample`)

The read path is **pure DB** (no bridge), used by the web API and the renderer:

1. `native = read_candles(conn, symbol, timeframe, from, to)`.
2. If `timeframe != "M1"` **and** `native` is empty for a sub-range **and** M1 is
   covered there → derive from M1: `resample_m1(read_candles(...,"M1",...),
   timeframe, covered=read_coverage(...,"M1"))`.
3. **Correctness guard (in `resample_m1`):** with `covered` given, a bucket is
   emitted only if its entire `[bucket_start, next_bucket_start)` span lies inside
   one covered interval. A partially-covered bucket is **omitted**, never emitted
   with a wrong high/low/close. (Tested directly at the `resample_m1` level.)

### Aggregation rule

Per server-time bucket: `open` = first M1 open, `high` = max high, `low` = min
low, `close` = last close, `tick_volume`/`real_volume` = sums, `spread` dropped
(charts don't need it; keep the `Candle.spread` field `None`).

---

## HTTP API (read-only + enqueue)

**`GET /api/candles?symbol=XAUUSDc&timeframe=M5&from=<ms>&to=<ms>`**

The route **never touches the bridge**. It:

1. Validates (`timeframe` ∈ `TIMEFRAMES`; `from`/`to` present & integer; else 400).
2. Serves candles from the DB (native, else M1-aggregation).
3. Computes `missing = missing_ranges(read_coverage(...), (from, to))`.
4. If `missing` is non-empty, calls `request_candles(...)` to **enqueue** the fill
   (deduped) and reports it so the client can poll again shortly.

Response (always **200**):

```json
{
  "symbol": "XAUUSDc",
  "timeframe": "M5",
  "candles": [
    { "time_msc": 1690000000000, "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.1, "v": 42 }
  ],
  "missing": [[1690000000000, 1690003600000]],
  "pending": true
}
```

- `missing` = ranges not yet cached; `pending` = a fill was (or already is)
  queued. When `missing` is empty, `pending` is false and the client stops polling.
- Timestamps stay **epoch ms** (Hard rule 3); the Phase B client divides by 1000
  for `lightweight-charts` (UNIX seconds).
- **Response cap:** a maximum bar count per response; if the range exceeds it,
  clamp to the most recent N within `[from, to]` (Phase B pages by re-requesting
  narrower windows on pan — the standard lightweight-charts lazy-load pattern).

---

## `journal live` integration

`live_cycle` gains a step: after the existing command step, **fulfil one candle
request per cycle** — `req = claim_next_request(conn); if req: fulfill_request(
client, conn, req)`. One per cycle keeps the loop's heartbeat responsive (same
reasoning as one trade-command per cycle). `requeue_orphaned(conn)` runs at
`live` startup so a request left `claimed` by a crash is retried, not lost.
`LiveReport` gains `candle_request_id` / `candle_bars_written` for observability.

---

## CLI

- **`journal candles-warm <symbol> <timeframe> --from <ms> --to <ms>`** — eager
  fill (pre-warm before an offline session). Constructs a client like the other
  live commands and calls `fill_range` directly. (Named `candles-warm`, not
  `candles warm`, to avoid restructuring the existing `journal candles` command
  into a group.)
- **`journal candles-coverage [--symbol S]`** — print stored coverage ranges per
  (symbol, timeframe) via `read_coverage`.

---

## Unification with existing code

- `render/chart.py`: replace the inline `SELECT … FROM candles …` (currently
  ~L216) with `candles_store.read_candles(...)`. **Pure DB, no bridge** — the
  renderer stays fill-free. Output must be **byte-identical**; existing render
  tests stay green.
- `ingest/candles.py`: reuse `candles_store.insert_candle` (move the Trap-15
  `_insert_candle` logic there) and additionally call `record_coverage` for each
  fetched window, so legacy per-trade ingest also populates coverage — one unified
  store, one coverage source of truth.
- **Definition of done** (project): all tests pass (paste real pytest output);
  `journal rebuild` still succeeds.

---

## Testing strategy

- **`resample.py`:** TDD, fixtures in `tests/fixtures/`. Cover: clean
  aggregation, `bucket_start` boundaries per timeframe in server time,
  weekend/holiday gaps, and the partial-bucket correctness guard (a
  partially-covered bucket must not be emitted).
- **`candles_store.py`:** pure-DB unit tests. Cover: `missing_ranges` subtraction,
  `record_coverage` coalescing touching/overlapping intervals, `read_candles`
  ordering/bounds, `insert_candle` Trap-15 tripwire + PK dedupe.
- **`candle_fill.py`:** driven by `adapter/fake.py` — **no live MT5** (Hard rule
  1). Cover: gap-only fetch, coverage recorded for empty (market-closed) ranges,
  idempotent re-run inserts nothing.
- **`candle_queue.py`:** dedupe of an already-pending/covered request, claim-once,
  `fulfill_request` writes bars + marks `done`, `requeue_orphaned` resets
  `claimed`→`pending`.
- **`live_cycle`:** with a fake client + a pending request, one cycle fulfils it
  and reports it.
- **API:** extend `tests/test_api.py` / `tests/test_web.py` — JSON shape,
  validation 400s, `missing`/`pending` when uncovered, and that the route
  **enqueues** without ever constructing a bridge client.
- **Regression:** existing chart-render and candle-ingest tests stay green after
  unification; `journal rebuild` still succeeds.

---

## Risks & mitigations

- **Wrong aggregated bars from partial M1** → correctness guard in `resample_m1`
  (only emit a fully-covered bucket) + coverage-based gap detection.
- **Massive M1 fetch on wide high-TF zoom-out** → native higher-timeframe caching
  (fetch H1/H4/D1 directly) instead of always aggregating from M1.
- **Web accidentally touching the bridge** → the fill engine lives in
  `ingest/candle_fill.py`, never imported by `web/`; an API test asserts the route
  enqueues rather than fetches.
- **Duplicate fill work / queue flooding** → `request_candles` dedupes on an
  identical pending/claimed range and on already-covered ranges.
- **Crash mid-fetch** → idempotent fills + `requeue_orphaned` (re-queue, never
  fail-with-scary-message, unlike `trade_commands`).
- **Server-time / DST drift** → re-measure `server_utc_offset_s`; bucket in server
  time; never assume offset 0.
- **Unification regressions in the trade renderer** → keep output identical, lean
  on existing render tests, verify `journal rebuild`.
