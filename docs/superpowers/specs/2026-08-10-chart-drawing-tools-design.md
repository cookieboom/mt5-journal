# Chart Drawing Tools — Design

**Date:** 2026-08-10
**Status:** approved (brainstorming), ready for planning
**Scope:** frontend drawing tools on `/chart` (normal + live + replay), rendered
read-only on `/trades/:id/view` and `/live`.

## Problem

`/chart` and its replay mode have no way to mark up the chart. A trader marks
support/resistance, trendlines, and supply/demand zones while reading price, and
writes short notes on what they expect. Today the only markup gestures are the
double-click-hold measure tool and SL/TP line dragging — both transient.

The journal is descriptive (CLAUDE.md rule 9). Drawings are *human* annotations,
not generated signals: nothing in this feature predicts, recommends, or feeds an
automated step. It stays outside `lab/`.

## Tool set

Four tools, chosen by the user; Fibonacci retracement explicitly excluded.

| Tool | Anchors | Notes |
|---|---|---|
| `trend` | 2 × (time, price) | Line segment. No infinite ray extension (YAGNI). |
| `hline` | price only | Spans the full pane width; has no time by construction. |
| `rect` | 2 × (time, price) | Opposite corners. Supply/demand zone, consolidation box. |
| `text`  | 1 × (time, price) + string | Free label anchored to a chart coordinate. |

Text entry: clicking with the `text` tool active places the anchor and opens a
small inline `<input>` at that pixel. `Enter` commits, `Escape` cancels, and an
empty or whitespace-only string discards the object instead of storing a blank
label. Re-editing an existing label is a double-click on it.

Palette contents, top to bottom: cursor (default), trend, hline, rect, text,
and "clear all" — which is destructive and therefore goes through a confirm
step before it wipes the current key's drawings.

## Decisions taken

1. **Rendering: SVG overlay** (not a lightweight-charts `ISeriesPrimitive`, not a
   raw `<canvas>`). The file already runs two SVG overlays — `MeasureOverlay` and
   `CoverageShadeOverlay` — over the same projection machinery (`project()`,
   `bumpProjection()` on visible-range change and `ResizeObserver`). SVG is
   testable under vitest + jsdom; a canvas primitive is not. Hand-drawn objects
   never reach the count where SVG performance matters.
2. **Storage: JSON blob in `app_prefs`** (not a new table, not localStorage).
   Reuses `prefs_store` and the existing `useChartPrefs` write-through pattern:
   zero migration, two endpoints. The upgrade path — a real `drawings` table —
   is marked with a `ponytail:` comment and taken only when drawings need to be
   *queried* (e.g. "did price respect the level I marked?"), which is not a
   requirement today.
3. **Keyed per `symbol_base`, across all timeframes.** A level drawn on M15 is
   the same level on H1; anchors are (time, price), not bar indices. Follows
   CLAUDE.md rule 11 — group by `symbol_base`, never by the raw `symbol`.
4. **Replay drawings are a separate store, scoped by `session_id`.** Live
   drawings are made with knowledge of what happened next; showing them during a
   replay leaks the answer and makes the training dishonest. Replay sees only
   its own session's drawings, and vice versa.
5. **Vertical icon palette on the left edge of the chart pane** (TradingView
   layout), not a dropdown in the crowded top toolbar and not keyboard-only.
6. **Editing: select, drag handles, drag body, delete.** No per-object property
   panel (colour/width/style) — defaults per kind are enough.

## Data model

Stored as one JSON blob per key. `app_prefs` keys:

- `drawings:<symbol_base>` — the normal/live chart, e.g. `drawings:XAUUSD`
- `drawings:replay:<session_id>` — one key per replay session

Blob shape:

```ts
type Anchor = { timeMs: number; price: number };   // epoch ms, integer, UTC (rule 3)

type Drawing =
  | { id: string; kind: "trend"; a: Anchor; b: Anchor; color?: string }
  | { id: string; kind: "hline"; price: number; color?: string }
  | { id: string; kind: "rect";  a: Anchor; b: Anchor; color?: string }
  | { id: string; kind: "text";  a: Anchor; text: string; color?: string };

type DrawingBlob = { v: 1; items: Drawing[] };
```

- `id` is a client-generated `crypto.randomUUID()`. Single user, no collision
  concern, no server-side id allocation.
- `v` is a format version so a future shape change can be migrated in the loader
  instead of silently corrupting old drawings. The loader hard-drops a blob with
  an unknown `v` rather than guessing (drawings are annotations; losing them is
  recoverable, misreading them is not).
- `color` absent means "use the per-kind default"; `NULL`/absent is unknown, not
  a colour (rule 4 in spirit).
- Prices are `REAL`; every comparison uses `Math.abs(a - b) < 1e-9` (rule 5).

### Blob validation

`parseDrawings(unknown): Drawing[]` in `lib/drawings.ts` is the single trust
boundary. It drops any item that fails a shape check (missing anchor, non-finite
number, unknown `kind`, `text` over 280 chars) and returns the survivors, rather
than throwing — a corrupt entry must never blank the whole chart. Invalid input
from the API is data, not a crash.

## Cross-timeframe anchoring (the subtle part)

`CandleChart.project()` uses `timeScale().timeToCoordinate()`, which returns
`null` for any timestamp that is not exactly a bar time on the current series. A
trendline anchored at 10:15 on M15, viewed on H1, has no 10:15 bar — the
coordinate resolves to `null` and **the whole drawing silently disappears**.

Fix: `lib/drawings.ts` exports `anchorToX(timeMs, candles, timeScale)`:

1. Binary-search `candles` for the last bar with `time_msc <= timeMs`.
2. Convert that array index to a pixel via `timeScale().logicalToCoordinate(i)`.

Consequences, deliberate and documented:

- An M15 anchor at 10:15 renders on the H1 bar that opened at 10:00. Snapping to
  the containing bar is the correct reading of "this level, at this moment".
- An anchor older than the first loaded bar, or newer than the last, yields
  `null` and that object is skipped for this frame. It reappears when the window
  covers it again — nothing is deleted.
- `hline` has no time and is therefore immune; it always spans the full pane.

## Pointer priority

`CandleChart`'s `onDown` already multiplexes two gestures. The drawing gesture
inserts into a strict order:

1. **Active tool ≠ cursor** → begin drawing; suppress pan/zoom for the drag.
2. **double-click-hold** → measure gesture (existing, unchanged).
3. **hit-test SL/TP price line** → SL/TP drag (existing). This *wins* over a
   drawing hit: it is an order-affecting control and must never be shadowed by
   a decorative line drawn near it.
4. **hit-test drawing** → select it; drag a handle to move an endpoint, drag the
   body to move the whole object.
5. **otherwise** → clear selection, clear any frozen measurement (existing).

Double-click on a `text` label opens its inline editor. This does not collide
with the measure gesture, which requires double-click **then hold** — a release
without a hold never enters measure — but the text-label case must be checked
first so a hold that begins on a label still edits rather than measures.

Keys: `Escape` cancels an in-progress draw and clears selection.
`Delete`/`Backspace` removes the selected object. Both are added to the existing
`onKey` handler, not a second listener.

Hit-testing lives in `lib/drawings.ts` as pure functions over *projected pixel*
coordinates: point-to-segment distance for `trend`, |Δy| for `hline`, edge
distance (not fill) for `rect`, label bounding box for `text`. Threshold reuses
the existing `HIT_THRESHOLD_PX` constant from `lib/sltpDrag.ts`.

## Components and boundaries

`CandleChart.tsx` is already 726 lines. The drawing feature must not grow it by
more than ~25 lines; all new state lives behind a hook.

```
frontend/src/
  lib/drawings.ts               pure: types, parseDrawings, anchorToX, hitTest,
                                distance math, draw/drag reducer, kind defaults
  lib/drawings.test.ts
  hooks/useDrawingGesture.ts    DOM events ↔ chart coordinates; owns draw/select/
                                drag state; returns the objects to render
  hooks/useDrawings.ts          GET on mount + debounced PUT write-through,
                                mirroring useChartPrefs (localStorage-free: the
                                DB is the only source, drawings are not prefs)
  hooks/useDrawings.test.ts
  components/DrawingOverlay.tsx SVG: segments, rects, text labels, selection handles
  components/DrawingOverlay.test.tsx
  components/DrawingPalette.tsx vertical icon column, absolutely positioned in
                                the chart wrapper's left edge

src/journal/
  web/app.py                    GET/PUT /api/drawings
  store/prefs_store.py          get_drawings / set_drawings
```

Each unit answers the three questions cleanly: `drawings.ts` is pure geometry
and parsing with no React and no chart API; `useDrawingGesture` is the only
place that knows about pointer events; `DrawingOverlay` is a pure function of
projected pixels; `useDrawings` is the only place that talks to the API.

## API

```
GET  /api/drawings?symbol=XAUUSDc[&session_id=<int>]  → { drawings: <blob|null> }
PUT  /api/drawings?symbol=XAUUSDc[&session_id=<int>]  → { saved_ms: <int> }
```

- The server normalises `symbol` → `symbol_base` via `domain/symbols.py` (rule
  11); the key is never built from the raw symbol string.
- `session_id` absent → the normal/live chart store.
- The blob is stored verbatim as JSON, like the other prefs endpoints. The
  server does not interpret drawing geometry; the frontend's `parseDrawings` is
  the validator. The server *does* reject a body that is not a JSON object and
  caps the payload size, so a broken client cannot write junk unbounded.
- `null` on GET means "no drawings yet", the same convention the existing prefs
  endpoints use.

Write-through: debounced ~400 ms PUT after any mutation, same as
`useChartPrefs`. A dropped PUT loses at most the last edit; the in-memory state
stays correct until reload.

## Page scope

| Page | Palette | Drawings rendered |
|---|---|---|
| `/chart` (normal, live) | yes | yes, editable |
| `/chart` replay mode | yes | yes, editable, session-scoped |
| `/trades/:id/view` | no | yes, read-only |
| `/live` | no | yes, read-only |

Read-only is a single `editable={false}` prop: the palette is not rendered and
`useDrawingGesture` does not attach its listeners.

## Replay interaction

Replay passes its `session_id`; the normal chart passes none. Two consequences,
both free:

- Live and replay drawings can never bleed into each other — different keys.
- An anchor in the future relative to the replay cursor projects to `null`,
  because `clipToCursor` has already removed those candles. Future drawings are
  invisible with no extra logic, and reappear as the cursor advances.

## Error handling

- API unreachable on mount → empty drawing set, palette still works, PUTs retry
  on the next mutation. Never blocks the chart.
- Corrupt/unknown-version blob → dropped by `parseDrawings`, chart renders
  clean. Logged to console once.
- Anchor outside the loaded window → object skipped this frame (not deleted).
- A drawing dragged onto a price the price scale cannot represent (log scale,
  price ≤ 0) → `priceToCoordinate` returns `null`, object skipped; the stored
  value is left untouched so nothing is lost.

## Testing

Frontend (vitest):

- `drawings.test.ts` — `parseDrawings` accepts valid, drops each invalid shape;
  `anchorToX` snapping including the exact-bar, between-bars, before-first, and
  after-last cases; point-to-segment distance; hit-test priority; reducer
  transitions (idle → drawing → committed, escape → idle, select → drag → move).
- `DrawingOverlay.test.tsx` — renders one element per kind; handles appear only
  for the selected object; an object whose projection is `null` is omitted.
- `useDrawings.test.ts` — GET populates, mutation schedules a debounced PUT,
  unmount cancels a pending PUT.

Backend (pytest):

- `prefs_store` round-trip for both key shapes; `symbol` → `symbol_base`
  normalisation on the endpoint; non-object body rejected.

Gates before merge: `uv run pytest`, `npx vitest run`, `tsc --noEmit`,
`npm run build`, and `uv run journal rebuild` still succeeds — drawings live in
`app_prefs` and are untouched by rebuild, which is exactly what the rebuild run
verifies.

## Explicitly out of scope

- Fibonacci retracement, channels, pitchforks, freehand brush.
- Per-object styling panel (colour, width, line style).
- Infinite-extension rays.
- Any analytic that reads drawings (e.g. "price respected this level N times").
  That is the trigger for promoting the blob to a real table, not this spec.
- Snapping drawings to OHLC values (magnet mode).
