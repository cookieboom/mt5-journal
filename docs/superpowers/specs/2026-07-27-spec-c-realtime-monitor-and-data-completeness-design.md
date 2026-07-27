# Spec C — Realtime Symbol Monitor + Data Completeness (Design)

**Date:** 2026-07-27
**Status:** Design approved (brainstorm complete) — pending implementation plan
**Roadmap:** `docs/ROADMAP-trade-chart-features.md` idea #1 (Spec C), merged with the
user's data-completeness proposal into a single spec, delivered in 4 phases.

## 1. Problem

Two related problems that meet at the candle store:

1. **No realtime.** The chart is a snapshot. There is no live-updating *forming
   bar*. `candles` is keyed `(symbol, timeframe, time_msc)` with `INSERT OR
   IGNORE`, so once a bar is written later updates are ignored — a forming bar
   cannot be overwritten. This is about **new data going forward**.

2. **No completeness visibility.** `journal live` is the only process that drains
   the `candle_requests` fill queue, and it is **often not running**. So many
   ranges were never fetched → holes in `candle_coverage`. When the user views a
   range with `journal live` down, the chart shows gaps and eventually `gaveup`,
   and there is **no way to tell "not fetched yet" from "market was closed"**.
   This is about **old data / holes already present**.

The system already knows which ranges were never fetched (`candle_coverage` +
`missing_ranges`) and already auto-enqueues a fill when a chart window is
uncovered (`candles_payload`). What is missing is **visibility, classification,
and a deliberate way to see and fill the holes** — plus a way to know whether
`journal live` is even alive.

## 2. Principles held (do not break)

- **Web never touches the bridge (M9).** All bridge fetches happen in `journal
  live`. Web reads the DB only. (Rules 1, 12; `candle_fill.py` never imported by
  `web/`.)
- **`candles` stays an append-only set of *closed* bars, fully rebuildable.**
  The forming bar lives in a separate, overwrite-friendly table. (Rule 6: charts
  are cache; Rule 2: derived data rebuildable.)
- **All timestamps epoch-ms integer UTC.** WIB is display-only. (Rule 3.)
- **The live tables are cache/ephemeral.** `journal rebuild` never touches them
  and still succeeds. Losing them loses nothing that cannot be re-fetched.

## 3. Architecture overview

### Realtime data flow
```
FE chart (normal mode)
  ├─ POST /api/watch {symbol,tf}  ──► live_watches (upsert, TTL)   [kept alive while page open]
  └─ poll GET /api/candles/live ~5s ◄─ live_candles + live_heartbeat

journal live  (the ONLY bridge-toucher)
  every cycle:
    ├─ beat(now)                                   → live_heartbeat = now  (always, even with no watch)
    └─ for each active watch (expires_msc > now):
         fetch last K bars from bridge
           ├─ bar whose period contains now  → forming → UPSERT live_candles
           └─ older, closed bars             → INSERT OR IGNORE candles + record_coverage   (promotion)
```

### Completeness data flow (pure DB read, no bridge)
```
GET /api/candles         → already returns `missing` (uncovered sub-ranges)
GET /api/coverage        → {covered, missing} for an explicit window (panel/ribbon over a period)
FE classifyGaps(bars, missing, window) → segments: covered | unfetched | closed
POST /api/backfill       → request_candles(...)   (existing queue; journal live drains)
```

Gap classification (the single source of truth reused by ribbon, shading, badge,
panel):
- **covered + has bars** → green (data present).
- **uncovered (in `missing`)** → red → *not fetched yet, fixable by backfill*.
- **covered + no bars** → grey → *market genuinely closed* (weekend/holiday/rollover),
  leave alone. (Matches the "chart gaps are genuine market closures" finding.)

### Liveness
`journal live` writes `live_heartbeat.beat_msc = now` every cycle. Web derives
`live = (now - beat_msc) < STALE_MS` (default 15 s). Empty `open_positions`
cannot serve as a heartbeat (no rows when nothing is open), which is why a
dedicated single-row table is required.

## 4. Phases (build order)

Each phase is independently executable and testable. Phase 1 is the foundation
everything else leans on.

1. **Phase 1 — Heartbeat & liveness.** `live_heartbeat` table; `journal live`
   writes it every cycle; `GET /api/live-status`; `useLiveStatus` hook +
   `LiveIndicator` (LIVE / stale / offline, with a copyable `journal live`
   command hint when offline).
2. **Phase 2 — Realtime forming bar.** `live_candles` + `live_watches` tables;
   `journal live` fetch + forming/closed split + promotion; `POST /api/watch` and
   `GET /api/candles/live`; `useLiveForming` hook that upserts the watch, polls,
   and merges the forming bar into the normal-mode chart (disabled in
   replay/training).
3. **Phase 3 — Completeness visuals.** `classifyGaps` util; `CoverageRibbon`
   under the chart + optional canvas shading (same classification); compact badge
   "N unfetched holes in view".
4. **Phase 4 — Data-health panel + backfill.** `DataHealthPanel` per symbol+TF
   (% covered, hole list in WIB); **Backfill** button (default scope: the visible
   window) → `POST /api/backfill`, progress watched by re-polling `/api/coverage`
   until the holes close.

## 5. Data model — migration `007_live_monitor.sql` (SCHEMA_VERSION 6 → 7)

All timestamps are epoch-ms integer UTC.

```sql
-- Single-row liveness beacon. journal live overwrites beat_msc every cycle.
CREATE TABLE IF NOT EXISTS live_heartbeat (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    beat_msc INTEGER NOT NULL
);

-- Demand-driven watch registry. Web upserts (TTL); journal live reads active.
CREATE TABLE IF NOT EXISTS live_watches (
    symbol        TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL,
    expires_msc   INTEGER NOT NULL,       -- watch is active while expires_msc > now
    requested_msc INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

-- At most one forming bar per (symbol, timeframe). Overwritten freely.
-- Column types mirror `candles` EXACTLY (tick_volume/spread/real_volume are
-- INTEGER there); NOT part of the candles append-only contract.
CREATE TABLE IF NOT EXISTS live_candles (
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    time_msc    INTEGER NOT NULL,         -- bar OPEN time (bucket start), server time
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    tick_volume INTEGER,
    spread      INTEGER,
    real_volume INTEGER,
    updated_msc INTEGER NOT NULL,         -- true UTC of last overwrite
    PRIMARY KEY (symbol, timeframe)
) WITHOUT ROWID;
```

`schema.sql` gets the same three tables (kept byte-identical in intent with the
migration, like the existing live section). `SCHEMA_VERSION` → 7. The
`test_migrated_db_matches_a_fresh_db` test must still pass.

**Rebuild safety:** none of these tables are read or written by `journal
rebuild`. They may be empty after a rebuild with no loss — the forming bar is
transient and re-fetched on the next live cycle.

## 6. Store layer — `store/live_store.py` (pure DB, no bridge, importable by web/)

- `beat(conn, now_msc)` — UPSERT the single heartbeat row.
- `read_heartbeat(conn) -> int | None` — `beat_msc` or `None`.
- `upsert_watch(conn, symbol, tf, now_msc, ttl_ms)` — set `expires_msc = now + ttl`.
- `active_watches(conn, now_msc) -> list[(symbol, tf)]` — `WHERE expires_msc > now`.
- `prune_expired(conn, now_msc)` — delete stale watches (housekeeping).
- `upsert_forming(conn, symbol, tf, candle, now_msc)` — overwrite the forming bar.
- `read_forming(conn, symbol, tf) -> Candle | None`.

Same tripwire discipline as `candles_store`: `time_msc` must be ms (>= `_MSC_FLOOR`);
never do a `×1000` here — the adapter boundary already did it (Trap 15).

## 7. `journal live` — additions to `ingest/live.py::live_cycle`

Added as new steps after the existing candle-request drain, keeping the
one-thing-per-cycle discipline so the position heartbeat is never starved:

- **Always:** `live_store.beat(conn, now_ms())` at the end of every cycle.
- **Per active watch** (`live_store.active_watches`, typically 1–2 given
  demand-driven watching):
  - Fetch the last `K` bars: `copy_rates_range(symbol, tf, from=now-(K+1)·tf, to=now)`.
    `K` small (e.g. 3) — enough to cover the bars that closed since the previous
    cycle plus the forming one.
  - Split by `bucket_start(now, tf)`: the bar whose period contains `now` is the
    **forming** bar → `upsert_forming`. Every older bar is **closed** →
    `insert_candle` + `record_coverage` (promotion; idempotent via `INSERT OR
    IGNORE`).
  - One bridge round-trip per watch per cycle; ~1 call / 5 s in the normal
    single-watch case. No loop-timing change (user chose "~5 s is enough").

`LiveReport` gains optional fields for observability (watches served, forming
bars written) mirroring the existing candle-request fields — not required for
correctness.

## 8. API (web, DB-read only)

- `GET  /api/live-status` → `{ "live": bool, "beat_msc": int|null, "age_ms": int|null }`.
- `POST /api/watch` `{symbol, timeframe}` → upsert watch with `expires = now + TTL`
  (TTL ~30 s). Returns `{ "ok": true }`.
- `GET  /api/candles/live?symbol&timeframe` →
  `{ "forming": {time_msc,o,h,l,c,v}|null, "beat_msc": int|null, "live": bool }`.
  Carries liveness too so the poll doubles as the staleness signal.
- `GET  /api/coverage?symbol&timeframe&from&to` → `{ "covered": [[lo,hi]...],
  "missing": [[lo,hi]...] }` (for the panel/ribbon over an explicit period).
- `POST /api/backfill` `{symbol, timeframe, from, to}` → `request_candles(...)`;
  returns `{ "request_id": int, "queued": bool }`. Progress is observed by
  re-polling `/api/coverage` (or `/api/candles`) until `missing` shrinks.

`candles_payload` is unchanged — it already returns `missing` and auto-enqueues.

## 9. Frontend (React SPA)

- **Phase 1:** `useLiveStatus()` polls `/api/live-status` ~5 s. `LiveIndicator`
  renders a dot + `LIVE` / `stale` / `offline`; when offline shows a copyable
  `journal live` command hint (web must not spawn processes). Mounted on the
  chart page.
- **Phase 2:** `useLiveForming(symbol, tf, enabled)` — `enabled = normal mode`
  (off in replay/training). On mount and every ~10–15 s it `POST /api/watch`;
  it polls `/api/candles/live` ~5 s and merges the forming bar into the candle
  array (same `time_msc` → replace last bar; newer → append). Reuses
  `CandleChart` / `useChartData` unchanged; the Spec B measure gesture keeps
  working automatically. Like TradingView, the last bar stays live even when
  scrolled left (updates off-screen).
- **Phase 3:** pure `classifyGaps(bars, missing, window)` → segments
  `covered | unfetched | closed`. `CoverageRibbon` (thin strip under the chart)
  + optional canvas shading, both driven by the same classification. Compact
  badge: "N unfetched holes in view".
- **Phase 4:** `DataHealthPanel` (per symbol+TF: % covered, hole list in WIB) +
  **Backfill** button (default scope = visible window) → `POST /api/backfill`,
  then re-poll `/api/coverage` until the holes close.

## 10. Testing (test-first, per project norm)

**Backend (pytest):**
- `live_store`: beat/read; upsert + `active_watches` respects `expires_msc`;
  `prune_expired`; `upsert_forming` overwrites; `read_forming`.
- `live_cycle` with `FakeMT5Client` returning bars incl. a forming bar:
  heartbeat written; forming bar upserted to `live_candles`; closed bars land in
  `candles` + coverage (promotion); expired watches ignored; empty-watch cycle
  still beats.
- API tests for each new endpoint (shapes, liveness threshold, backfill enqueues).
- `test_migrated_db_matches_a_fresh_db` updated for migration 007.

**Frontend (vitest):**
- `classifyGaps`: the three segment kinds and their boundaries.
- forming-bar merge: replace-vs-append by `time_msc`.
- `useLiveForming` / `useLiveStatus` with fake timers + mocked fetch.
- Component tests: `LiveIndicator`, `CoverageRibbon`, `DataHealthPanel`.

## 11. Definition of done

- `uv run pytest` green (output pasted).
- `npm run test` (vitest) green, `npm run build` exits 0, `tsc` 0.
- `uv run journal rebuild` still succeeds.
- `graphify update .` run after code changes.
- Human visual pass in-browser (realtime forming bar ticks; offline indicator
  when `journal live` is down; ribbon/shading distinguish unfetched vs closed;
  backfill fills a hole while `journal live` runs).

## 12. Out of scope (YAGNI)

- Faster-than-5 s cadence / `interval_watch` loop tuning (user chose ~5 s).
- Multi-symbol monitor dashboard / separate `/monitor` page.
- Backfill of arbitrary long periods / "everything since first trade" as the
  default (default is the visible window; a period selector may be a later add).
- Any signal/recommendation feature (Rule 9).
- Web auto-starting `journal live` (informational hint only).
