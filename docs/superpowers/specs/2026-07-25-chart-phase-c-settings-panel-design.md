# Chart Phase C — Full Settings Panel + Preference Persistence

**Date:** 2026-07-25
**Branch:** `chart-phase-c-settings-panel` (cut from Phase B tip `6ad3d79`)
**Depends on:** Phase B (PR #8) — **must merge before Phase C merges.** Phase A
(PR #7) already on `main`.
**Segment:** "chart", phase 3 of 4 (A data ✓ · B interactive page ✓ · **C settings** ·
D training/replay).

## 1. Goal

Phase B shipped an interactive TradingView-style chart at `/chart` with a
*minimal* settings popover (theme + grid, persisted to `localStorage`). Phase C
replaces that with a **full settings drawer** and moves preference persistence to
a **DB-backed, cross-browser** store (localStorage retained as an instant-render
mirror). It also folds in the memory-cap item deferred from Phase B and a few
small correctness fixes in the area we are touching.

Out of scope: training/replay (Phase D). Chart settings stay global and fully
separate from any future training config — they must not leak into Phase D.

## 2. Constraints (from CLAUDE.md — non-negotiable)

- **Rule 1 / M9 boundary:** `web/` never touches the MT5 bridge. Prefs are pure
  DB; no bridge involvement.
- **Rule 3:** timestamps are epoch **milliseconds**, integer, broker-server UTC
  (offset 0). lightweight-charts wants UNIX **seconds** — divide by 1000 only at
  the chart boundary. WIB (UTC+7) is display-only.
- **Rule 4:** `NULL` = unknown, `0` = none set. Respected by `liveLines` and by
  the info-panel volume fix below.
- **Rule 6:** charts are cache. The prefs table is **application config**, not a
  chart cache and not derived from raw — it is legitimately durable state, and
  `journal rebuild` (which only drops + rebuilds `trades` from raw) must not
  touch it.
- **Schema:** changes go through a **migration file**, never an in-place edit of
  `schema.sql` after data exists. `db.py` applies `schema.sql` wholesale to a
  fresh DB and the `migrations/NNN_*.sql` files to an existing one;
  `tests/test_migrations.py` asserts fresh == migrated, so the new table goes in
  **both** places and `SCHEMA_VERSION` bumps.
- **No new dependencies**, backend or frontend. Color pickers use native
  `<input type="color">`; everything else is already available.

## 3. Settings schema (`ChartSettings` v1)

Extends Phase B's `{ theme, grid }`. Lives in `frontend/src/lib/chartPrefs.ts`.

```ts
interface ChartSettings {
  version: 1;
  // Appearance (theme drives CHROME only: bg / text / grid / border)
  theme: "dark" | "light";
  grid: boolean;
  colors: { up: string; down: string; wick: string };   // GLOBAL hex; wick applies to both up+down wicks; same across themes
  chartType: "candle" | "bar" | "line" | "area";
  crosshair: "normal" | "magnet" | "hidden";
  // Scale
  priceScale: "linear" | "log";
  autoScale: boolean;
  lastPriceLine: boolean;
  // Behaviour
  liveOverlay: boolean;                 // master on/off for SL/TP/entry overlay
  defaultSymbol: Sym;                   // used when URL has no ?symbol=
  defaultTimeframe: Timeframe;          // used when URL has no ?tf=
  // Data
  initialBars: number;                  // default 300; clamp [100, 1000]
  maxBars: number;                      // default 3000; clamp [max(500, initialBars), 10000]
}
```

**Defaults** (`DEFAULT_SETTINGS`), seeded from Phase B's DARK palette. Bodies keep
their Phase B up/down colors exactly. Wicks differ deliberately: Phase B tinted
wicks two-tone (green up / red down), whereas Phase C exposes **one** global
`colors.wick` applied to both — the default is a neutral (`#9a97c4`), a small,
resettable change from Phase B's two-tone wicks:

```ts
{
  version: 1,
  theme: "dark", grid: true,
  colors: { up: "#34d399", down: "#fb7185", wick: "#9a97c4" },
  chartType: "candle", crosshair: "normal",
  priceScale: "linear", autoScale: true, lastPriceLine: true,
  liveOverlay: true, defaultSymbol: "XAUUSDc", defaultTimeframe: "M5",
  initialBars: 300, maxBars: 3000,
}
```

**Migration (localStorage):** the Phase B object `{ theme, grid }` has no
`version`. `loadChartSettings` treats any object lacking `version` as legacy:
keep its `theme`/`grid`, fill every new field from `DEFAULT_SETTINGS`, stamp
`version: 1`. The localStorage key stays `mt5j.chart.settings` (never broken).
Numeric fields are clamped to their bounds on load (defends against hand-edited
or corrupt values); `maxBars` is additionally raised to `initialBars` if smaller.

## 4. Persistence — two layers, DB authoritative

### 4a. Backend

- **Migration** `store/migrations/005_app_prefs.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS app_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,          -- JSON blob
    updated_ms INTEGER NOT NULL
  );
  ```
  The same `CREATE TABLE` is added to `schema.sql` (fresh path), and
  `SCHEMA_VERSION` in `db.py` bumps `4 → 5`. `test_migrations.py` then passes.
- **Store module** `store/prefs_store.py` — pure DB, no bridge, mirrors the
  `candle_queue.py` shape (functions take `conn`):
  - `get_pref(conn, key) -> str | None` — raw JSON text or `None`.
  - `set_pref(conn, key, value, updated_ms) -> None` — `INSERT ... ON CONFLICT
    (key) DO UPDATE` upsert, commits.
  Storage is opaque JSON text; the store does not parse or validate the blob
  (the frontend owns the schema/migration). This keeps the table reusable for
  any future single-value pref without a schema change.
- **API** (two routes in the `app.py` factory, same style as `/api/candles`):
  - `GET /api/chart/prefs` → `{ "prefs": <parsed JSON> | null }` (null when no
    row yet — the client then falls back to its own defaults / localStorage).
  - `PUT /api/chart/prefs` → body is the settings JSON; server stamps
    `updated_ms = now_ms()` and upserts under key `"chart"`. Returns
    `{ "ok": true, "updated_ms": <n> }`. The route stores the body verbatim as
    text; it does not need to understand the shape.

### 4b. Frontend — `useChartPrefs` hook

Isolates all persistence so `Chart.tsx` just consumes `{ settings, update, reset }`.

1. **Instant load:** initialise state from `loadChartSettings()` (localStorage) —
   no round-trip, no flash of defaults.
2. **DB reconcile (on mount):** `GET /api/chart/prefs`.
   - Row present → **DB wins**: overwrite state + localStorage with the parsed,
     migrated, clamped DB value.
   - Row absent **and** localStorage had a value → **import**: `PUT` the current
     settings so this browser seeds the DB.
   - Both absent → defaults (already in state); no PUT until first change.
3. **Write-through on change:** `update(next)` sets state, writes localStorage
   immediately, and `PUT`s to the DB **debounced ~400 ms** (color-picker drags
   fire rapidly; debounce the network write, not the local/visual update).
4. **Reconcile is a pure function** `reconcilePrefs(local, dbParsed)` →
   `{ settings, shouldImport }`, unit-tested without the network.

Last-write-wins across browsers via `updated_ms`; sufficient for single-user.
No optimistic-concurrency handling (YAGNI).

## 5. UX — right drawer (`ChartSettingsDrawer.tsx`, replaces the popover)

- Slides in from the right edge; the chart stays visible so every non-structural
  change **previews live** (all settings except `chartType` apply via
  `applyOptions`). Gear button in `ChartToolbar` toggles it; click-outside / Esc
  closes.
- Four grouped sections: **Tampilan** (theme, grid, up/down/wick color pickers,
  chart type, crosshair), **Skala** (linear/log, auto-scale, last-price line),
  **Data** (initial bars, max bars — number inputs with the clamp bounds shown),
  **Perilaku** (live overlay master toggle, default symbol, default timeframe).
- Sticky footer: **"Reset ke default"** → sets `DEFAULT_SETTINGS`, write-through
  (localStorage + PUT). Reset is all-or-nothing (no per-section reset — YAGNI).
- `ChartSettingsPopover.tsx` is deleted; the drawer is its replacement.

## 6. Applying settings to the chart (`CandleChart.tsx`)

- **Extend the existing `applyOptions` effect** (still no full re-create):
  - `colors` → `series.applyOptions({ upColor, downColor, wickUpColor,
    wickDownColor })` (wick from `colors.wick`).
  - `crosshair` → `crosshair.mode` (`Normal` / `Magnet` / `Hidden`).
  - `priceScale` + `autoScale` → `rightPriceScale.{ mode, autoScale }`
    (`mode`: `0` normal / `1` logarithmic — mapped in `CandleChart`, not leaked
    to `domain/`; this is view code so lightweight-charts enums are fine here).
  - `lastPriceLine` → `series.applyOptions({ priceLineVisible })`.
- **`chartType`:** an effect keyed on `chartType` that **recreates the series
  only** (remove + add the new series type, re-`setData`, re-draw price lines) —
  not the whole chart, so pan/zoom and theme survive. `candle`/`bar` map OHLC;
  `line`/`area` map `close`. On hover, line/area series expose only `close`, so
  the info panel shows a single **Harga** row and hides O/H/L for those types.
- **`liveOverlay`:** an added guard in the overlay effect — master `false` draws
  no lines regardless of `nowVisible`/positions (existing gating still applies
  when `true`).

## 7. Memory cap — fold-in of the Phase B defer (`useChartData.ts` + `candles.ts`)

Clean, live-correct model:

- `maxBars` bounds the in-memory array. Eviction **always drops the oldest
  bars**; the newest (now) side is always retained, so `isNowVisible`, the live
  overlay gating, and `jumpToNow` stay correct.
- `loadOlder` **stops** once `candles.length >= maxBars` (becomes a no-op) rather
  than fighting eviction by re-dropping the bars a pan just loaded. Product
  meaning: from now you can pan back up to `maxBars` bars; to go further, raise
  `maxBars` in settings. A subtle non-blocking hint may indicate the history
  bound was reached (optional, low priority).
- `initialBars` replaces the hardcoded `300` in `initialWindow` (and the pan
  `olderWindow` batch size stays independent, still 300).
- Pure, unit-testable pieces: a `capCandles(candles, maxBars)` helper in
  `candles.ts` (drops oldest beyond `maxBars`, keeps sort), and the
  `loadOlder`-gating predicate. `useChartData` takes `initialBars`/`maxBars`
  from settings.

## 8. Minor fold-ins (we are already in this code)

- **`ChartInfoPanel` volume row:** `bar.v ?` (truthy) → `bar.v != null` so a
  genuine `0` volume renders (Rule 4). *(For line/area chart types this row is
  part of the hidden O/H/L group anyway.)*
- **`fetchCandles`:** check `r.ok` **before** `r.json()`, so a non-JSON error
  page surfaces `HTTP {status}` instead of a parse error.
- **Visual verification pass** (explicit plan task): in the browser, both
  light + dark — drawer open/close, live preview of each control, color pickers,
  chart-type switch, pan/zoom/crosshair, price-scale log/linear, and the live
  SL/TP/entry overlay actually drawing (uses the already-proven `/api/live`
  path — this is a frontend RENDER check, not a bridge check).

## 9. Testing (Definition of Done)

- **Backend `uv run pytest`** (green, output pasted):
  - `prefs_store`: `set_pref` then `get_pref` round-trips; upsert overwrites the
    same key and bumps `updated_ms`; unknown key → `None`.
  - API: `GET /api/chart/prefs` on empty DB → `prefs: null`; `PUT` then `GET`
    returns the stored blob; `updated_ms` present.
  - `test_migrations.py` still passes (fresh == migrated after `005` +
    `SCHEMA_VERSION = 5`).
- **Frontend `vitest`:**
  - `chartPrefs`: legacy `{theme,grid}` (no version) migrates to a full v1 object
    with defaults; clamping of `initialBars`/`maxBars`; `maxBars` raised to
    `initialBars`; `parseSelection(params, defaults)` — URL wins, else saved
    default, else hard default.
  - `reconcilePrefs(local, dbParsed)`: DB present → DB wins; DB absent + local
    present → `shouldImport`; both absent → defaults.
  - `capCandles` drops oldest beyond `maxBars` and preserves order; `loadOlder`
    gating predicate stops at `maxBars`.
- **`npm --prefix frontend run build`** → 0 errors.
- **`uv run journal rebuild`** still succeeds (proves prefs table is untouched
  by rebuild).
- `graphify update .` after code changes.

## 10. File-change summary

**Backend**
- `store/schema.sql` — add `app_prefs` table (fresh path).
- `store/migrations/005_app_prefs.sql` — new migration.
- `store/db.py` — `SCHEMA_VERSION = 5`.
- `store/prefs_store.py` — new: `get_pref` / `set_pref`.
- `web/app.py` — `GET` + `PUT /api/chart/prefs`.
- `tests/` — `test_prefs_store.py`, prefs API cases in the web test module,
  `test_migrations.py` unchanged but must still pass.

**Frontend**
- `lib/chartPrefs.ts` — extend `ChartSettings`, defaults, legacy migration,
  clamping, `reconcilePrefs`, `parseSelection(params, defaults)`.
- `hooks/useChartPrefs.ts` — new: instant-load + DB reconcile + debounced
  write-through.
- `lib/candles.ts` — `capCandles`, `initialBars` param on `initialWindow`.
- `hooks/useChartData.ts` — consume `initialBars`/`maxBars`, cap + `loadOlder`
  gate.
- `components/ChartSettingsDrawer.tsx` — new (replaces
  `ChartSettingsPopover.tsx`, deleted).
- `components/ChartToolbar.tsx` — gear toggles the drawer.
- `components/CandleChart.tsx` — extended `applyOptions`, `chartType` series
  recreate, `liveOverlay` guard.
- `components/ChartInfoPanel.tsx` — volume `!= null` fix; line/area single-price
  hover.
- `lib/candles.ts` `fetchCandles` — `r.ok` before `r.json()`.
- `pages/Chart.tsx` — use `useChartPrefs`; pass settings through.
- `*.test.ts` — the vitest cases above.

## 11. Phase D boundary

Chart settings are global and stored under key `"chart"`. Phase D (training /
replay) gets its own config surface and must not read or write these. Because all
four "debatable" items were pulled into C (chart type, default symbol/tf, memory
cap, crosshair), Phase D is left clean for replay/training only.
