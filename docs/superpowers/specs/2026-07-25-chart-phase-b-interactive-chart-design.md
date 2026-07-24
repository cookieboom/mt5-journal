# Chart Phase B — Interactive Chart Page · Design

**Date:** 2026-07-25
**Branch:** `chart-phase-b-interactive-chart` (from Phase A tip; Phase A = PR #7, still OPEN — must merge before B)
**Segment:** Chart, phase 2 of 4 (A done → **B** → C → D)

## Context

Phase A shipped the data foundation: a smart candle store and a read-only,
queue-mediated JSON API. Phase B builds the **interactive, TradingView-style
chart page** in the React SPA that consumes it. The web layer NEVER touches the
MT5 bridge (rule 1 / M9 boundary); it reads the DB and enqueues fills that only
`journal live` drains.

What Phase A provides that B consumes:

```
GET /api/candles?symbol=XAUUSDc&timeframe=M5&from=<ms>&to=<ms>   (always 200)
→ { symbol, timeframe,
    candles: [{time_msc, o, h, l, c, v}, ...],   // epoch ms, server/UTC
    missing: [[lo_ms, hi_ms], ...],              // ranges NOT yet cached
    pending: true|false }                        // a fill was enqueued
GET /api/live   → open positions (sl/tp/open_price/price_current/direction/volume)
```

Valid timeframes: `M1 M5 M15 H1 H4 D1`. Symbols: `XAUUSDc BTCUSDc EURUSDc`
(suffix `c` only). Account currency **USC** (never print a bare `$`). Timestamps
are epoch **ms, integer, broker-server = UTC** (`server_utc_offset_s = 0`);
convert to **WIB (UTC+7) at display time only**. SL/TP obey **rule 4**: `NULL` =
unknown, `0.0` = none set — neither is ever drawn.

This chart page is **display only** (+ training in Phase D). It never places real
orders — real trading already lives in M9 `/live`.

## Goals / Non-goals

**Goals**

- A `/chart` page in the SPA: candlestick chart with pan/zoom, symbol + timeframe
  switching, a live-position overlay, a symbol/live info panel, and a minimal
  settings gear.
- Robust UX for the Phase A fill model: partial cache, uncached ranges, and
  `journal live` not running — no infinite spinner.
- Close out the non-blocking Phase A review items first (they finalize Phase A).

**Non-goals (deferred)**

- Full settings panel + full preference persistence → **Phase C**.
- Chart training / replay interactions → **Phase D**.
- Any order placement from this page (real trading is M9 `/live`).
- Native TF-native coverage for aggregated frames (Phase A already aggregates
  from M1; `pending` may stay `true` even when bars are shown — handled in UX).

---

## Part 0 — Backend cleanup (do FIRST, finalizes Phase A)

Small, test-backed tasks that land before any UI work.

1. **Migration 004 — additive `CHECK` on `candle_requests.status`.**
   Constrain `status IN ('pending','claimed','done','failed')`, mirroring the
   `trade_commands` pattern. SQLite cannot add a `CHECK` to an existing column in
   place, so 004 does the standard **table rebuild**: create
   `candle_requests_new` with the constraint, `INSERT … SELECT` the rows, drop
   old, rename. Mirror the constrained DDL into `schema.sql` (fresh DBs). Bump
   `SCHEMA_VERSION` **3 → 4** in `store/db.py`. Update
   `tests/test_migrations.py` (incl. `test_migrated_db_matches_a_fresh_db`) so a
   v3 DB migrates to a schema identical to a fresh v4.

2. **`record_coverage` guard.** In `store/candles_store.py`, reject/skip
   `from_ms > to_ms` (mirror the guard already in `missing_ranges`). Test in
   `tests/test_candles*.py`.

3. **Ingest coverage cross-producer test.** Prove `ingest/candles.sync_candles`
   (the legacy ingest path) now writes `candle_coverage` — currently only true by
   code inspection. Add a small test in `tests/test_candles.py`.

4. **F541 cosmetic.** `cli.py` `candles-warm` banner is an f-string with no
   interpolation → plain string.

5. *(optional, no new deps)* Assert `/api/candles` route metadata: the
   `Query(alias="from")` / `Query(alias="to")` aliases exist on the route object.
   Light assertion only (`test_web.py` intentionally has no httpx).

6. *(optional)* `max_bars` truncation test for `candles_payload` (the tail-slice
   path). **Do NOT** re-test the bucket-boundary aggregation bug — already fixed
   in Phase A (bucket-aligned M1 read).

`uv run journal rebuild` must still succeed after the migration.

---

## Part 1 — Page shell, routing, selection state

- **Files:** new `frontend/src/pages/Chart.tsx`; route `<Route path="/chart" …>`
  in `App.tsx`; sidebar entry `{ to: "/chart", label: "Chart" }` in
  `Sidebar.tsx` (placed after "Live").
- **Selection = URL query params** via react-router `useSearchParams`:
  `/chart?symbol=XAUUSDc&tf=M5`. Defaults when absent: **`XAUUSDc` / `M5`**.
  Invalid/unknown symbol or tf falls back to the default. Reload, bookmark, and
  browser back/forward all restore the exact view. Changing the symbol or
  timeframe replaces the query params (does not push history spam — use
  `setSearchParams(..., { replace: true })` for TF flips).
- **Layout:** top **toolbar** (`[symbol ▾] [M1 M5 M15 H1 H4 D1] … [Jump to now] [⚙]`),
  a candlestick **chart** filling the remaining space, and a **collapsible right
  info panel**. Follows existing SPA styling (tailwind tokens: `panel-border`,
  `glass`, `text-ink`, `text-muted`, `pos`, `neg`, `violet`, `cyan`).

## Part 2 — Chart & data layer

- **New frontend dependency:** `lightweight-charts` (TradingView, MIT) — the one
  approved new dep for Phase B. Pin an **exact** latest version in
  `frontend/package.json`. The series-creation API differs between v4
  (`chart.addCandlestickSeries(...)`) and v5 (`chart.addSeries(CandlestickSeries, ...)`);
  the implementing task pins the version and uses that version's API — the exact
  call is confirmed at implementation, not guessed.
- **Time conversion:** payload stays epoch-ms. lightweight-charts wants **UNIX
  seconds** → divide `time_msc` by 1000 when feeding the series. All human-facing
  time labels (crosshair, panel) render in **WIB (UTC+7)**. "Now" = `Date.now()`
  (broker UTC, offset 0 — they coincide).
- **New client module `frontend/src/lib/candles.ts`** (pure, unit-tested):
  - `timeframeMs(tf)` — ms per bar, mirrors `domain/resample.timeframe_ms`.
  - `fetchCandles(symbol, tf, fromMs, toMs)` — typed wrapper over `/api/candles`.
  - `mergeCandles(existing, incoming)` — merge by `time_msc` (Map-keyed),
    return sorted array; newer wins on collision.
  - `nextWindow(...)` / initial-window helpers — compute the `[from,to]` to
    request for the initial load and for a pan extension.
  - `isNowVisible(visibleRange, lastBarTime)` — predicate gating the live
    overlay.
  - `liveLines(position)` — returns the price lines to draw, **filtering out any
    `0.0` or `null`** SL/TP/entry (rule 4).
  - `formatWib(ms)` — WIB display formatter for the crosshair/panel.
- **Types** in `frontend/src/lib/types.ts`: `Candle { time_msc,o,h,l,c,v }`,
  `CandlesResponse { symbol,timeframe,candles,missing,pending }`.
- **State model** (a `useChartData` hook or equivalent inside `Chart.tsx`):
  - Holds a `Map<time_msc, Candle>` plus the loaded `[from,to]` window.
  - **Initial window:** last **~300 bars** of the active TF ending at now.
  - **Lazy-load on pan:** subscribe to
    `timeScale().subscribeVisibleLogicalRangeChange`; when the left edge nears the
    oldest loaded bar (barsBefore below a threshold), fetch an **older** window by
    time, `mergeCandles`, and re-`setData` with the merged sorted array. Server
    `max_bars` (5000) caps a single response → paginate by time range. Cap
    in-memory candles (drop far-past beyond a bound) to keep `setData` cheap.
  - Switching symbol/TF resets the map and window.

## Part 3 — On-demand fill UX (the Phase A queue model)

After each `fetchCandles`, if `missing` is non-empty the server has already
enqueued a fill; the client **polls the same range** to pick up the result:

- **Bounded poll:** re-fetch every **~2s**, up to **~5 tries (~10s)**. Then stop.
- **Bars already shown** (including aggregated-from-M1, where `pending` can be
  `true` even though candles render): show a **subtle, non-blocking** banner
  `⌛ memuat data…` — never a blocking spinner over visible candles. Treat
  "candles cover the visible range" as good enough so it doesn't look stuck.
- **Zero bars cached** for the range: a **centered empty-state** while polling.
- **Give up** (still `missing` after the window): stop auto-poll and show a hint
  — *"Data belum lengkap untuk rentang ini. Jalankan `journal live`."* — with a
  **[Coba lagi]** button that restarts one bounded poll cycle.
- Bridge down / `journal live` off is **not a hard error**: the endpoint still
  returns 200 with whatever is cached; we simply surface the hint.

## Part 4 — Live overlay

- Source: `GET /api/live`, polled every **2500ms** via the existing `useApi` hook
  (unchanged).
- **Shown only when** the chart symbol matches an open position **AND**
  `isNowVisible(...)` is true (latest bar / right edge in view).
- **Draws horizontal price lines**, one set **per open position** (hedging allows
  several on one symbol): **SL** (`neg`/red), **TP** (`pos`/green), **entry**
  (grey), each labelled. `liveLines()` **omits any `NULL` or `0.0`** value.
- **Pan into history → lines hide** (they represent levels that only exist now).
  The toolbar **[Jump to now]** button snaps the time scale back to the right edge
  (`timeScale().scrollToRealTime()` / fit last bars).

## Part 5 — Right info panel (collapsible, stacked top→bottom)

1. **Crosshair OHLC readout** — subscribe to `subscribeCrosshairMove`; show the
   hovered candle's O/H/L/C/V + time (WIB). Falls back to the latest candle when
   not hovering.
2. **Last price + change** — big last-candle **close** with change vs the prior
   close (absolute + %). Labelled honestly as *last-candle close, not a live
   tick* (only fresh if candles are current).
3. **Live position block** — when an open position exists on this symbol:
   direction, volume, entry, current price, SL, TP, and floating P&L (USC via
   `money()`), one row per position.
4. **Symbol/TF meta** — `symbol_base`, active TF, bars loaded, and
   cached-range / partial-coverage status.

## Part 6 — Settings gear

- Small popover from the `[⚙]` toolbar button:
  - **Chart theme:** `Dark` (match app palette — bg, grid, text, up=`pos`,
    down=`neg`) / `Light`.
  - **Grid lines:** on / off.
- **Persisted in `localStorage`** (scoped to these two chart-appearance settings;
  the full settings panel + broader preference persistence remain Phase C).

---

## Testing & Definition of Done

**Frontend (vitest)** — pure logic only; the canvas chart component stays a thin,
untested shell:

- `timeframeMs` for every TF.
- `mergeCandles` (dedupe, sort, newer-wins).
- `nextWindow` / initial-window computation.
- `isNowVisible` predicate (now visible vs panned-away).
- `liveLines` filtering (skips `0.0` and `null` SL/TP/entry).
- `formatWib` (UTC ms → WIB label).

**Backend (pytest)** — the Part 0 cleanup: migration 004 + `test_migrations`
lockstep, `record_coverage` reverse-range guard, ingest→coverage cross-producer
test, and (optional) alias-metadata / `max_bars` tests.

**DoD** (per project rules):

- `uv run pytest` green — **paste the actual output**.
- `npm --prefix frontend test` (vitest) green.
- `npm --prefix frontend run build` → **0 errors**.
- `uv run journal rebuild` still succeeds.
- `graphify update .` after code changes.

## Risks / open notes

- **lightweight-charts major version API drift** (v4 vs v5 series creation) —
  mitigated by pinning an exact version and confirming the call at implementation.
- **`pending` true while bars render** (no TF-native coverage yet) — handled by
  treating visible coverage as "shown" so the UI doesn't look perpetually
  loading; polling still runs bounded and harmlessly.
- **Hedging → many price lines** on one symbol could clutter — acceptable for a
  single-user tool; each line is labelled; revisit only if it proves noisy.
- **End-to-end live overlay** needs `journal live` + a live bridge (M9 smoke test
  still pending a human run per `docs/HANDOFF.md`). Phase B is fully developable
  against `adapter/fake.py`; the overlay's happy path is unit-tested via
  `liveLines` / `isNowVisible`, with manual verification deferred to a live run.
