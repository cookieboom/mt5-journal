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

## Architecture & modules

One **store service** is the sole gateway to candle data.

```
src/journal/domain/resample.py        NEW  pure M1 → any-TF aggregation (TDD)
src/journal/store/candles_store.py    NEW  serve(symbol, tf, from, to): fill + coverage + aggregate
src/journal/store/migrations/00X.sql  NEW  candle_coverage table
src/journal/web/api.py                EDIT candles_payload(...) builder
src/journal/web/app.py                EDIT GET /api/candles route
src/journal/render/chart.py           EDIT read candles via the store (behaviour unchanged)
src/journal/ingest/candles.py         EDIT route ingest through the store (unification)
```

- `resample.py` is **pure and I/O-free** — trivially testable (Hard rule 7,
  tests first).
- `candles_store.py` is the only module that talks to both the DB and the
  bridge adapter. It never imports MetaTrader5 (Hard rule 1); it calls the
  `MT5Client` Protocol's existing `copy_rates_range(symbol, timeframe, from, to)`.

### Unit boundaries

- **`resample.py`** — *what:* aggregate a list of M1 `Candle`s into a coarser
  timeframe. *Use:* `resample_m1(m1_bars, "H1") -> list[Candle]`. *Depends on:*
  nothing but the `Candle` dataclass and a bucketing rule.
- **`candles_store.py`** — *what:* return a correct OHLC series for a request,
  filling gaps. *Use:* `serve(conn, client, symbol, timeframe, from_ms, to_ms)
  -> CandleResult`. *Depends on:* the DB connection, the `MT5Client` Protocol,
  `resample`, and the coverage table.
- **`candles_payload` (api.py)** — *what:* shape a `CandleResult` into JSON.
  *Use:* called by the route. *Depends on:* the store.

---

## Data model & migration

- **Keep `candles` unchanged** (multi-timeframe cache):
  `PRIMARY KEY (symbol, timeframe, time_msc)`, OHLC + volumes, `time_msc` = bar
  open time in epoch **ms**, server time.
- **New `candle_coverage`** — records which `[from, to]` ranges have actually
  been fetched, **per (symbol, timeframe)**. This is what distinguishes
  "empty because the market was closed / weekend" (fetched, no bars) from
  "empty because never fetched" (must fetch).

  ```sql
  CREATE TABLE IF NOT EXISTS candle_coverage (
      symbol     TEXT NOT NULL,
      timeframe  TEXT NOT NULL,       -- 'M1','M5','M15','H1','H4','D1'
      from_msc   INTEGER NOT NULL,    -- inclusive, bar-open, server time (ms)
      to_msc     INTEGER NOT NULL,    -- inclusive, bar-open, server time (ms)
      PRIMARY KEY (symbol, timeframe, from_msc)
  );
  ```

  Intervals are **merged on insert**: an incoming `[a, b]` that touches or
  overlaps an existing interval is coalesced, so coverage stays a minimal set of
  disjoint ranges per (symbol, timeframe). This is the "stitch new data onto what
  already exists" behaviour.

- The schema change ships as a **new migration file** under
  `store/migrations/` — `schema.sql` is not edited in place once data exists
  (project rule). The migration bumps the schema version and its test asserts the
  new version, following the existing `test_migrations.py` pattern.

- **Timestamps** stay epoch-ms integers, server time (Hard rule 3). Bucketing for
  aggregation uses server time; `server_utc_offset_s` is **re-measured each fetch**
  (confirmed `0` today → buckets align to UTC midnight, but not assumed).

---

## Store service behaviour

`serve(conn, client, symbol, timeframe, from_ms, to_ms) -> CandleResult`:

1. **Find gaps.** Subtract existing `candle_coverage[(symbol, timeframe)]` from
   the requested `[from, to]` → list of uncovered sub-intervals.
2. **Fill natively.** For each gap, call
   `client.copy_rates_range(symbol, timeframe, gap.from, gap.to)`, upsert the
   returned bars into `candles`, and merge the gap into `candle_coverage`
   (record the *requested* gap range as covered, even if it returned zero bars —
   that is how "genuinely empty" is remembered).
3. **Serve from DB.** Read `candles` for `(symbol, timeframe)` within
   `[from, to]` and return them, ascending by `time_msc`.
4. **Aggregation fallback.** If `timeframe` is not natively covered **and** M1 is
   covered for the range (or the bridge is unreachable), derive the series from
   M1 via `resample.py`. M1 stays the fine-grain source. **Correctness guard:** an
   aggregated bar is only emitted for a bucket whose *entire* M1 span is covered
   — a partially-covered bucket is omitted, never emitted with a wrong high/low/
   close.
5. **Offline-friendly.** If the bridge is unreachable while gaps remain, serve
   whatever is covered and set `bridge_status = "unreachable"`. Never raise purely
   because the bridge is down.

`CandleResult` carries: the bar list, `bridge_status` (`"ok"` | `"unreachable"`),
and the list of ranges that remain unfilled (`gaps`).

### Aggregation rule (`resample.py`)

For each coarse bucket (bucket boundaries computed in server time):
`open` = first M1 open, `high` = max M1 high, `low` = min M1 low,
`close` = last M1 close, `tick_volume`/`real_volume` = sums, `spread` dropped or
averaged (decide in plan; charts don't need it). Missing M1 within a bucket ⇒
that bucket is **not** produced (see correctness guard above).

---

## HTTP API

**`GET /api/candles?symbol=XAUUSDc&timeframe=M5&from=<ms>&to=<ms>`**

Response:

```json
{
  "symbol": "XAUUSDc",
  "timeframe": "M5",
  "candles": [
    { "time_msc": 1690000000000, "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.1, "v": 42 }
  ],
  "bridge_status": "ok",
  "gaps": []
}
```

- Timestamps stay **epoch ms** (Hard rule 3); the Phase B client divides by 1000
  for `lightweight-charts` (which wants UNIX seconds).
- **Response cap:** enforce a maximum bar count per response. If the requested
  range would exceed it, clamp to the most recent N within `[from, to]` and note
  it (Phase B pages by re-requesting narrower windows on pan — the standard
  lightweight-charts lazy-load pattern).
- **Validation:** `timeframe` must be in `TIMEFRAMES`; unknown symbol/timeframe →
  400. Missing/invalid `from`/`to` → 400.
- `bridge_status: "unreachable"` is a **200** with partial data, not an error.

---

## CLI

- **`journal candles warm <symbol> <timeframe> --from <ms> --to <ms>`** —
  eagerly fill a range (pre-warm before an offline session). Thin wrapper over
  `serve`.
- **`journal candles coverage [--symbol S]`** — print stored coverage ranges per
  (symbol, timeframe) so the user can see what is cached.

Both are conveniences; the core path is lazy fill via the store.

---

## Unification with existing code

- `render/chart.py` currently reads candles directly for the trade PNG. Migrate
  it to request via `candles_store` (aggregating/ filling as needed). Behaviour
  and output must be **unchanged** — the existing chart tests stay green.
- `ingest/candles.py` currently ingests per-trade candle windows. Route it
  through the store so there is a single fill/coverage path.
- **Definition of done** (project): all tests pass (paste real pytest output),
  and `journal rebuild` still succeeds.

---

## Testing strategy

- **`resample.py`:** TDD, tests first, fixtures in `tests/fixtures/`. Cover:
  clean aggregation, bucket boundaries in server time, weekend/holiday gaps,
  and the partial-bucket correctness guard (a partially-covered bucket must not
  be emitted).
- **`candles_store.py`:** driven by `adapter/fake.py` — **no live MT5** (Hard
  rule 1). Cover: gap detection, native fill + coverage merge, coverage merge
  coalescing touching/overlapping intervals, aggregation fallback, empty-range
  (market-closed) memory, and bridge-unreachable offline behaviour.
- **API:** extend `tests/test_api.py` / `tests/test_web.py` — JSON shape,
  validation (400s), and the `bridge_status:"unreachable"` 200-with-partial case.
- **Regression:** existing chart-render and candle-ingest tests must remain green
  after the unification; `journal rebuild` must still succeed.

---

## Risks & mitigations

- **Wrong aggregated bars from partial M1** → the correctness guard (only emit a
  bucket whose full M1 span is covered) plus coverage-based gap detection.
- **Massive M1 fetch on wide high-TF zoom-out** → native higher-timeframe caching
  (fetch H1/H4/D1 directly from the bridge for those views) instead of always
  aggregating from M1.
- **Server-time / DST drift** → re-measure `server_utc_offset_s` each fetch;
  bucket in server time; never assume offset 0.
- **Unification regressions in the trade renderer** → keep output identical, lean
  on existing render tests, verify `journal rebuild`.
