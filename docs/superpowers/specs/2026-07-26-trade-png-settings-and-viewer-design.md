# Spec A — Trade PNG Render Settings + Interactive Trade Viewer

**Date:** 2026-07-26
**Status:** Approved (design), pending implementation plan
**Scope:** Two related, PNG-vs-interactive trade features on the Trades surface.
This is the first of three specs decomposed from a larger request (build order:
**Spec A → Spec B (chart measure gesture) → Spec C (realtime symbol monitor)**).

Deliberately NOT in this spec: realtime monitoring (Spec C), the double-click
measure gesture (Spec B). They are separate subsystems with their own specs.

---

## Context (current state, measured)

- `TradeDetail.tsx` shows a **static PNG** at `/trades/{position_id}/chart.png`,
  rendered by `src/journal/render/chart.py` (mplfinance). Knobs today are
  hard-coded constants: `PAD_BARS = 15`, `style="charles"`, TF chosen by
  `choose_timeframe(duration_s)` (overridable via `tf` arg), fixed marker/hline
  colors, SL/TP hlines always drawn.
- The **interactive** chart engine (lightweight-charts) lives in `Chart.tsx`,
  fed by `useChartData` + `candles.ts`, with on-demand fill mediated by the
  `candle_requests` queue (**web never touches the bridge**; `journal live`
  fulfils). Replay/training infra (cursor + evaluator) is in `useReplaySession`
  + `web/training.py` (Phase D).
- DB-backed preferences use `app_prefs` single-value keys (Phase C
  `useChartPrefs`, plus `replayPrefs`). Migration 005 created `app_prefs`.
- Trades list ordering: `views.py` → `ORDER BY open_time_msc DESC`, filterable by
  `symbol`, `status`, `source`. Trade fields available: `symbol`/`symbol_base`,
  `duration_s`, `open_price`/`close_price`, `sl_initial`/`tp_initial`,
  `net_profit`, `r_multiple`, `mae_r`/`mfe_r`, `session`, tags, annotation.
- Manual tags + annotations are user-editable via the existing `annotate` API;
  auto-tags are derived and must stay rebuildable (hard rule 2).

Account-specific rules that bind this spec: money is **USC** (never bare `$`);
prefer **R-multiple** (unit-free) in stats; `NULL` SL/TP = unknown → render `—`,
never `0` (hard rule 4); charts are cache, reproducible from DB (hard rule 6).

---

## Part 1 — Trade PNG Render Settings (#2)

### Goal
Make the mplfinance trade PNG customizable through a **global** setting, edited
from a compact panel on the trade page. One setting set applies to all trade
images.

### Data model
- New `app_prefs` key **`trade_png`**, JSON value, normalized/clamped in one
  place (mirror the `replayPrefs`/`chartPrefs` lib pattern).
- Fields and defaults (defaults reproduce today's behavior exactly):
  - `theme`: enum of mplfinance styles — at minimum `charles` (light, default),
    `nightclouds` (dark), `yahoo`. Maps to `mpf` style + marketcolors.
  - `pad_bars`: int, default `15`, clamped **[5, 120]**.
  - `tf_override`: one of `TIMEFRAMES` or `null` (= auto ladder via
    `choose_timeframe`). Validated against `TIMEFRAMES`.
  - `show_sltp`: bool, default `true` (SL/TP hlines).
  - `show_markers`: bool, default `true` (entry/exit markers).
  - `show_volume`: bool, default `false` (matches current — no volume panel).
  - `show_grid`: bool, default per current style.

### Backend
- Introduce a `RenderOpts` dataclass in `render/chart.py`. `render_trade()` takes
  `RenderOpts` instead of reading module constants; `PAD_BARS`/`style` become
  defaults on `RenderOpts`, not behavior baked into the body. `window_for()`
  takes `pad_bars` as a parameter.
- The `/trades/{position_id}/chart.png` endpoint reads `trade_png` prefs from the
  DB and passes the resulting `RenderOpts` into `render_trade()`.
- **Cache invalidation (hard rule 6).** The cache PNG filename gains a short
  **signature suffix** derived from `RenderOpts` (stable hash of the normalized
  opts). Changing a setting yields a new cache key → the PNG is re-rendered; the
  old file is a harmless orphan (cache is disposable). The DB never depends on a
  rendered file. The endpoint accepts the signature as a cache-busting query
  param so the browser refetches.

### Frontend
- Collapsible **"Render settings"** panel above the `<img>` in `TradeDetail.tsx`:
  theme dropdown, pad-bars stepper (clamped), TF selector (Auto + TIMEFRAMES),
  overlay toggle chips (SL/TP, markers, volume, grid).
- On save: `PUT` `trade_png` prefs, then refetch `<img>` with the new signature
  query. Copy makes the global scope explicit ("applies to all trades").
- Reuse existing prefs hook conventions (localStorage seed + DB reconcile if we
  follow `useChartPrefs`; otherwise a straight DB read/write — match whichever
  pattern Phase C settled on for a single global key).

### Errors / edges
- `pad_bars` clamped before use; invalid `tf_override` rejected → fall back to
  auto. Empty window keeps the existing "run `journal candles`" message.

### Tests
- Pytest (TDD, hard rule 7 — render is behavior): `render_trade` honors each knob
  (theme → style, `pad_bars` → window width, `tf_override` → chosen TF, each
  toggle → presence/absence of the overlay); cache key **changes** when opts
  change and is **stable** when they don't; endpoint reads prefs and threads them
  through.
- Vitest: panel renders, clamps input, PUTs on save, busts the image cache.

---

## Part 2 — Interactive Trade Viewer (#3)

### Goal
From the trade detail page, a **"Lihat di chart"** button opens an interactive
viewer with the trade centered, a stats + tag-edit panel on the right, and
prev/next buttons (bottom-center) to move between trades without returning to the
list.

### Route & navigation
- New route **`/trades/:id/view`**, own component `TradeView.tsx`, reusing the
  lightweight-charts engine + candle loader + `candle_requests` fill queue from
  `Chart.tsx` (web never touches the bridge).
- **Prev/next follows the active Trades-list filter subset & order.** Filter
  params (`symbol`, `status`, `source`) travel in the query-string, making the
  view deep-linkable. The viewer calls the same list endpoint (order
  `open_time_msc DESC`) to obtain the ordered id set, then computes the id before
  / after `:id`. Prev/next only swap `:id` while preserving the filter query.
  Buttons disable at the ends of the set. If `:id` is outside the filtered set,
  fall back safely (treat as a singleton; disable both buttons).
- Keyboard `←` / `→` mirror prev/next (small nicety).

### Chart & overlay
- Default: **entire window shown at once**, interactive (pan / zoom / switch TF).
  Initial TF = `choose_timeframe(duration_s)` (same as PNG); window = `window_for`
  → trade centered with PAD context.
- Overlay: entry/exit markers + initial SL/TP lines (SL/TP `NULL` → not drawn).
- **Step = optional playback reveal** (secondary feature): borrows the replay
  **cursor only**, NOT the evaluator. Play/step reveals the real bars
  progressively from a few bars before entry through past exit; the exit marker
  appears when reached. No fill simulation — the trade already happened and its
  outcome is known.

### Right panel — stats + edit
- Read-only stats: **R-multiple** (led, unit-free), `net_profit` (USC via
  `money()`), direction/volume, `mae_r`/`mfe_r`, entry/exit price, initial SL/TP
  (`—` when unknown), duration, open/close time (WIB display), session, symbol,
  auto-tags.
- Editable: add/remove **manual tags** + edit the **annotation note**, via the
  existing `annotate` API (PUT). Auto-tags stay read-only (hard rule 2).

### Errors / edges
- No candles yet → show a "fill queued / run `journal candles`" state (reuse the
  `NoCandlesError` pattern), not an empty chart.
- Open / non-chartable trade → viewer refuses with a message, mirroring the
  current `chartable` guard.

### Tests
- Vitest: prev/next honor the filter subset and disable at ends; keyboard arrows
  navigate; panel renders with null fields (`—`, never `0`); tag/annotation edit
  calls the API; playback reveal advances the cursor.
- Pytest: the list-for-navigation endpoint and `annotate` PUT are already
  covered; extend only if a new query shape is needed.

---

## Reuse map
- Chart engine & candle loader ← `Chart.tsx` / `useChartData` / `candles.ts`
- Playback cursor ← replay infra Phase D (`useReplaySession`) — cursor only
- Window & TF ladder ← `render/chart.py` (`choose_timeframe`, `window_for`)
- Tags / annotations ← `annotate` API
- Prefs pattern ← `app_prefs` + Phase C hooks (`useChartPrefs` / `replayPrefs`)

## Out of scope (later specs)
- Spec B: double-click-and-hold price measure gesture (applies to all charts).
- Spec C: realtime symbol monitor (configurable update interval, forming-bar
  storage strategy).

## Definition of done
Tests pass with pasted pytest + vitest output; `journal rebuild` still succeeds;
`vite build` clean; `graphify update .` run at the end.
