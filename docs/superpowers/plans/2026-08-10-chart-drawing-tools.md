# Chart Drawing Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add TradingView-style drawing tools (trendline, horizontal line, rectangle zone, text note) to `/chart` in both normal and replay mode, rendered read-only on `/trades/:id/view`.

**Architecture:** Pure geometry, parsing and gesture state live in `frontend/src/lib/drawings.ts` with no React and no chart-API access. A `useDrawingGesture` hook translates DOM pointer events into that pure state and is the only place that touches listeners. `DrawingOverlay` is an absolutely-positioned SVG sibling of the chart pane — the same pattern `MeasureOverlay` and `CoverageShadeOverlay` already use — and is a pure function of already-projected pixels. Persistence is a JSON blob per key in the existing `app_prefs` table via two new endpoints; no migration, no new table.

**Tech Stack:** React 18 + TypeScript, lightweight-charts 5.2.0, vitest + @testing-library/react + jsdom (frontend); FastAPI + sqlite3 stdlib, pytest (backend). No new dependencies (CLAUDE.md rule 8).

## Global Constraints

- **No new dependencies.** Current stack only (CLAUDE.md rule 8).
- **All timestamps are epoch milliseconds, integer, UTC** (rule 3). Anchors store `timeMs`, never a bar index and never a local-time string.
- **Money/price is `REAL`; compare with tolerance** `Math.abs(a - b) < 1e-9`, never `==` (rule 5).
- **`NULL`/absent means unknown, `0` means "none set"** (rule 4). An absent `color` means "use the per-kind default", not "black".
- **Symbols are stored twice** (rule 11): query with `symbol` (`XAUUSDc`), key/group by `symbol_base` (`XAUUSD`). Normalisation happens only through `journal.domain.symbols.to_base`.
- **Descriptive only** (rule 9): drawings are human annotations. Nothing here predicts, recommends, or feeds another automated step.
- **`app_prefs` is not derived from raw** — `journal rebuild` must still succeed and must not touch drawings.
- **`CandleChart.tsx` is already 726 lines.** This feature may add at most ~30 lines to it; all new state belongs in hooks.
- Frontend tests run from `frontend/`: `npx vitest run <path>`. Backend: `uv run pytest <path> -q` from the repo root.
- Existing constant reuse: `HIT_THRESHOLD_PX` (= 8) from `frontend/src/lib/sltpDrag.ts`; `isDoubleClickHold` from `frontend/src/lib/measure.ts`.

---

### Task 1: Drawing types, blob parsing, kind defaults

**Files:**
- Create: `frontend/src/lib/drawings.ts`
- Test: `frontend/src/lib/drawings.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `Anchor`, `DrawingKind`, `Drawing`, `DrawingBlob`, `BLOB_VERSION`, `MAX_TEXT_LEN`, `KIND_COLORS`, `parseDrawings(raw: unknown): Drawing[]`, `colorOf(d: Drawing): string`.

- [x] **Step 1: Write the failing test**

Create `frontend/src/lib/drawings.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { parseDrawings, colorOf, KIND_COLORS, type Drawing } from "./drawings";

const trend: Drawing = {
  id: "d1", kind: "trend",
  a: { timeMs: 1_000_000, price: 100 },
  b: { timeMs: 1_600_000, price: 110 },
};
const hline: Drawing = { id: "d2", kind: "hline", price: 105 };
const rect: Drawing = {
  id: "d3", kind: "rect",
  a: { timeMs: 1_000_000, price: 100 },
  b: { timeMs: 1_600_000, price: 110 },
};
const text: Drawing = { id: "d4", kind: "text", a: { timeMs: 1_000_000, price: 100 }, text: "supply" };

describe("parseDrawings", () => {
  it("returns the four valid kinds unchanged", () => {
    const items = [trend, hline, rect, text];
    expect(parseDrawings({ v: 1, items })).toEqual(items);
  });

  it("returns empty for null, non-object, and a missing items array", () => {
    expect(parseDrawings(null)).toEqual([]);
    expect(parseDrawings("nope")).toEqual([]);
    expect(parseDrawings({ v: 1 })).toEqual([]);
  });

  it("drops the whole blob when the version is unknown", () => {
    expect(parseDrawings({ v: 2, items: [hline] })).toEqual([]);
  });

  it("drops individual malformed items but keeps the good ones", () => {
    const raw = {
      v: 1,
      items: [
        hline,
        { id: "x", kind: "trend", a: { timeMs: 1, price: 2 } },        // no b
        { id: "x", kind: "hline", price: Number.NaN },                  // non-finite
        { id: "x", kind: "wormhole", price: 1 },                        // unknown kind
        { id: "x", kind: "text", a: { timeMs: 1, price: 2 } },          // no text
        { kind: "hline", price: 1 },                                    // no id
        trend,
      ],
    };
    expect(parseDrawings(raw)).toEqual([hline, trend]);
  });

  it("drops a text item whose label is over the length cap", () => {
    const long = { id: "x", kind: "text", a: { timeMs: 1, price: 2 }, text: "a".repeat(281) };
    expect(parseDrawings({ v: 1, items: [long] })).toEqual([]);
  });

  it("keeps an explicit colour and rejects a non-string one", () => {
    const coloured = { ...hline, color: "#ff0000" };
    expect(parseDrawings({ v: 1, items: [coloured] })).toEqual([coloured]);
    expect(parseDrawings({ v: 1, items: [{ ...hline, color: 7 }] })).toEqual([]);
  });
});

describe("colorOf", () => {
  it("prefers the explicit colour and falls back to the kind default", () => {
    expect(colorOf({ ...hline, color: "#ff0000" })).toBe("#ff0000");
    expect(colorOf(trend)).toBe(KIND_COLORS.trend);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/drawings.test.ts`
Expected: FAIL — `Failed to resolve import "./drawings"`.

- [x] **Step 3: Write minimal implementation**

Create `frontend/src/lib/drawings.ts`:

```ts
// Pure model for hand-drawn chart annotations. No React, no chart API here —
// the same discipline as measure.ts. Time is epoch ms (broker server = UTC,
// CLAUDE.md rule 3); prices are REAL and only ever compared with tolerance.

export interface Anchor {
  timeMs: number;   // epoch ms, integer, UTC
  price: number;
}

export type DrawingKind = "trend" | "hline" | "rect" | "text";

export type Drawing =
  | { id: string; kind: "trend"; a: Anchor; b: Anchor; color?: string }
  | { id: string; kind: "hline"; price: number; color?: string }
  | { id: string; kind: "rect"; a: Anchor; b: Anchor; color?: string }
  | { id: string; kind: "text"; a: Anchor; text: string; color?: string };

export interface DrawingBlob { v: 1; items: Drawing[] }

export const BLOB_VERSION = 1;
export const MAX_TEXT_LEN = 280;

// Per-kind defaults. An absent `color` on an item means "use this" — absent is
// unknown, not black (rule 4 in spirit).
export const KIND_COLORS: Record<DrawingKind, string> = {
  trend: "#7dd3fc",
  hline: "#a78bfa",
  rect: "#fbbf24",
  text: "#e6e6f0",
};

export function colorOf(d: Drawing): string {
  return d.color ?? KIND_COLORS[d.kind];
}

function isAnchor(v: unknown): v is Anchor {
  if (typeof v !== "object" || v === null) return false;
  const a = v as Record<string, unknown>;
  return Number.isFinite(a.timeMs) && Number.isFinite(a.price);
}

function isDrawing(v: unknown): v is Drawing {
  if (typeof v !== "object" || v === null) return false;
  const d = v as Record<string, unknown>;
  if (typeof d.id !== "string" || d.id === "") return false;
  if (d.color !== undefined && typeof d.color !== "string") return false;
  switch (d.kind) {
    case "trend":
    case "rect":
      return isAnchor(d.a) && isAnchor(d.b);
    case "hline":
      return Number.isFinite(d.price);
    case "text":
      return isAnchor(d.a) && typeof d.text === "string"
        && d.text.length > 0 && d.text.length <= MAX_TEXT_LEN;
    default:
      return false;
  }
}

// The single trust boundary for anything coming back from the API or from an
// older client. A corrupt ITEM is dropped; a blob with an unknown version is
// dropped whole rather than guessed at. Never throws: one bad entry must not
// blank the chart.
export function parseDrawings(raw: unknown): Drawing[] {
  if (typeof raw !== "object" || raw === null) return [];
  const blob = raw as Record<string, unknown>;
  if (blob.v !== BLOB_VERSION) return [];
  if (!Array.isArray(blob.items)) return [];
  return blob.items.filter(isDrawing);
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/drawings.test.ts`
Expected: PASS (7 tests).

- [x] **Step 5: Commit**

```bash
git add frontend/src/lib/drawings.ts frontend/src/lib/drawings.test.ts
git commit -m "feat(drawings): drawing model, blob parsing, kind defaults"
```

---

### Task 2: Projection and hit-testing geometry

The cross-timeframe trap lives here: `timeScale().timeToCoordinate()` returns `null` for any timestamp that is not exactly a bar time on the current series, so an M15 anchor viewed on H1 would vanish. `anchorToX` snaps to the containing bar instead.

**Files:**
- Modify: `frontend/src/lib/drawings.ts` (append)
- Test: `frontend/src/lib/drawings.test.ts` (append)

**Interfaces:**
- Consumes: `Anchor`, `Drawing` from Task 1.
- Produces: `PixelPoint`, `Handle` (`"a" | "b" | "body"`), `Hit`, `Projected`, `barIndexAt(candles, timeMs): number`, `anchorToX(timeMs, candles, logicalToX): number | null`, `projectDrawing(d, ctx): Projected`, `distToSegment(p, a, b): number`, `hitTest(projected, p, threshold?): Hit | null`, `ProjectCtx`.

- [x] **Step 1: Write the failing test**

Append to `frontend/src/lib/drawings.test.ts`:

```ts
import {
  barIndexAt, anchorToX, distToSegment, hitTest, projectDrawing,
  type ProjectCtx,
} from "./drawings";

// 10 bars, 60s apart, starting at 1_000_000.
const candles = Array.from({ length: 10 }, (_, i) => ({ time_msc: 1_000_000 + i * 60_000 }));

// identity logical→x; price→y as y = 200 - (price - 100) * 10 (same convention
// as the existing CandleChart test harness). Pane is 500px wide.
const ctx: ProjectCtx = {
  width: 500,
  candles,
  logicalToX: (i: number) => i,
  priceToY: (p: number) => 200 - (p - 100) * 10,
};

describe("barIndexAt", () => {
  it("returns the exact index on a bar boundary", () => {
    expect(barIndexAt(candles, 1_000_000)).toBe(0);
    expect(barIndexAt(candles, 1_180_000)).toBe(3);
  });

  it("snaps a between-bars time down to the containing bar", () => {
    expect(barIndexAt(candles, 1_000_001)).toBe(0);
    expect(barIndexAt(candles, 1_179_999)).toBe(2);
  });

  it("returns -1 before the first bar and the last index after the last bar", () => {
    expect(barIndexAt(candles, 999_999)).toBe(-1);
    expect(barIndexAt(candles, 9_000_000)).toBe(9);
  });

  it("returns -1 for an empty candle array", () => {
    expect(barIndexAt([], 1_000_000)).toBe(-1);
  });
});

describe("anchorToX", () => {
  it("projects through the containing bar index", () => {
    expect(anchorToX(1_180_000, candles, (i) => i)).toBe(3);
    expect(anchorToX(1_179_999, candles, (i) => i)).toBe(2);
  });

  it("is null before the first loaded bar", () => {
    expect(anchorToX(999_999, candles, (i) => i)).toBeNull();
  });

  it("is null when the time scale itself cannot resolve the index", () => {
    expect(anchorToX(1_180_000, candles, () => null)).toBeNull();
  });
});

describe("projectDrawing", () => {
  it("spans an hline across the full pane width", () => {
    const p = projectDrawing({ id: "h", kind: "hline", price: 105 }, ctx);
    expect(p.a).toEqual({ x: 0, y: 150 });
    expect(p.b).toEqual({ x: 500, y: 150 });
  });

  it("projects both trend anchors", () => {
    const p = projectDrawing(
      { id: "t", kind: "trend", a: { timeMs: 1_000_000, price: 100 }, b: { timeMs: 1_240_000, price: 110 } },
      ctx,
    );
    expect(p.a).toEqual({ x: 0, y: 200 });
    expect(p.b).toEqual({ x: 4, y: 100 });
  });

  it("yields a null endpoint when the anchor falls outside the loaded window", () => {
    const p = projectDrawing(
      { id: "t", kind: "trend", a: { timeMs: 1, price: 100 }, b: { timeMs: 1_240_000, price: 110 } },
      ctx,
    );
    expect(p.a).toBeNull();
  });
});

describe("distToSegment", () => {
  it("is the perpendicular distance inside the segment", () => {
    expect(distToSegment({ x: 5, y: 10 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(10);
  });

  it("clamps to the nearest endpoint outside the segment", () => {
    expect(distToSegment({ x: 20, y: 0 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(10);
  });

  it("is zero for a degenerate segment at the point", () => {
    expect(distToSegment({ x: 3, y: 4 }, { x: 3, y: 4 }, { x: 3, y: 4 })).toBe(0);
  });
});

describe("hitTest", () => {
  const trendItem = {
    id: "t", kind: "trend" as const,
    a: { timeMs: 1_000_000, price: 100 }, b: { timeMs: 1_540_000, price: 100 },
  };
  const projected = [projectDrawing(trendItem, ctx)];

  it("returns the endpoint handle when the pointer is on an endpoint", () => {
    expect(hitTest(projected, { x: 0, y: 200 })).toEqual({ id: "t", handle: "a" });
    expect(hitTest(projected, { x: 9, y: 200 })).toEqual({ id: "t", handle: "b" });
  });

  it("returns the body handle when the pointer is on the line between endpoints", () => {
    expect(hitTest(projected, { x: 5, y: 202 })).toEqual({ id: "t", handle: "body" });
  });

  it("returns null when the pointer is beyond the threshold", () => {
    expect(hitTest(projected, { x: 5, y: 240 })).toBeNull();
  });

  it("hits a rect on its edge but not through its middle", () => {
    const r = projectDrawing(
      { id: "r", kind: "rect", a: { timeMs: 1_000_000, price: 110 }, b: { timeMs: 1_540_000, price: 100 } },
      ctx,
    );
    expect(hitTest([r], { x: 5, y: 100 })).toEqual({ id: "r", handle: "body" });
    expect(hitTest([r], { x: 5, y: 150 })).toBeNull();
  });

  it("skips an item with an unprojectable endpoint", () => {
    const off = projectDrawing(
      { id: "off", kind: "trend", a: { timeMs: 1, price: 100 }, b: { timeMs: 1_540_000, price: 100 } },
      ctx,
    );
    expect(hitTest([off], { x: 9, y: 200 })).toBeNull();
  });

  it("prefers the topmost (last) item when two overlap", () => {
    const under = projectDrawing({ id: "under", kind: "hline", price: 105 }, ctx);
    const over = projectDrawing({ id: "over", kind: "hline", price: 105 }, ctx);
    expect(hitTest([under, over], { x: 200, y: 150 })?.id).toBe("over");
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/drawings.test.ts`
Expected: FAIL — `barIndexAt is not a function` (or an import resolution error for the new names).

- [x] **Step 3: Write minimal implementation**

Append to `frontend/src/lib/drawings.ts`:

```ts
import { HIT_THRESHOLD_PX } from "./sltpDrag";

export interface PixelPoint { x: number; y: number }

export type Handle = "a" | "b" | "body";
export interface Hit { id: string; handle: Handle }

// A drawing resolved to pane pixels. `a`/`b` are null when the anchor falls
// outside the loaded window (or the price scale cannot represent it) — such an
// item is skipped for this frame and NEVER deleted.
export interface Projected {
  d: Drawing;
  a: PixelPoint | null;
  b: PixelPoint | null;
}

export interface ProjectCtx {
  width: number;                                   // pane width in px (hline span)
  candles: { time_msc: number }[];
  logicalToX: (index: number) => number | null;    // timeScale().logicalToCoordinate
  priceToY: (price: number) => number | null;      // series.priceToCoordinate
}

// Index of the last bar at or before `timeMs`; -1 when the time precedes the
// first loaded bar or there are no bars. Binary search — the candle array is
// sorted by construction and can hold thousands of bars.
export function barIndexAt(candles: { time_msc: number }[], timeMs: number): number {
  let lo = 0, hi = candles.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (candles[mid].time_msc <= timeMs) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return best;
}

// Anchor time → pixel x. Deliberately NOT timeToCoordinate(): that returns null
// for any timestamp which is not exactly a bar time on the CURRENT series, so a
// drawing made on M15 would silently vanish when the same chart is viewed on
// H1. Snapping to the containing bar is the correct reading of "this level, at
// this moment", and is what keeps drawings shared across timeframes.
export function anchorToX(
  timeMs: number,
  candles: { time_msc: number }[],
  logicalToX: (index: number) => number | null,
): number | null {
  const i = barIndexAt(candles, timeMs);
  if (i < 0) return null;
  return logicalToX(i);
}

export function projectDrawing(d: Drawing, ctx: ProjectCtx): Projected {
  const pt = (anchor: Anchor): PixelPoint | null => {
    const x = anchorToX(anchor.timeMs, ctx.candles, ctx.logicalToX);
    const y = ctx.priceToY(anchor.price);
    return x === null || y === null ? null : { x, y };
  };
  if (d.kind === "hline") {
    const y = ctx.priceToY(d.price);
    return y === null
      ? { d, a: null, b: null }
      : { d, a: { x: 0, y }, b: { x: ctx.width, y } };
  }
  if (d.kind === "text") return { d, a: pt(d.a), b: null };
  return { d, a: pt(d.a), b: pt(d.b) };
}

export function distToSegment(p: PixelPoint, a: PixelPoint, b: PixelPoint): number {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 < 1e-9) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

// Rough text label box, matching DrawingOverlay's 11px monospace label.
function textBox(p: PixelPoint, text: string): { x0: number; y0: number; x1: number; y1: number } {
  return { x0: p.x, y0: p.y - 14, x1: p.x + text.length * 6.5 + 8, y1: p.y + 2 };
}

// Topmost-first: later items are drawn on top, so they must win the hit.
// Endpoint handles beat the body so a drag on an endpoint moves that endpoint.
// Rect hits on its EDGES only — a filled zone would otherwise swallow every
// click inside it, including one meant for the chart underneath.
export function hitTest(
  projected: Projected[],
  p: PixelPoint,
  threshold: number = HIT_THRESHOLD_PX,
): Hit | null {
  for (let i = projected.length - 1; i >= 0; i--) {
    const { d, a, b } = projected[i];
    if (a === null) continue;

    if (d.kind === "text") {
      const box = textBox(a, d.text);
      if (p.x >= box.x0 - threshold && p.x <= box.x1 + threshold
          && p.y >= box.y0 - threshold && p.y <= box.y1 + threshold) {
        return { id: d.id, handle: "body" };
      }
      continue;
    }
    if (b === null) continue;

    // hline has no endpoints to grab: its whole span is body.
    if (d.kind !== "hline") {
      if (Math.hypot(p.x - a.x, p.y - a.y) <= threshold) return { id: d.id, handle: "a" };
      if (Math.hypot(p.x - b.x, p.y - b.y) <= threshold) return { id: d.id, handle: "b" };
    }

    if (d.kind === "rect") {
      const c1 = { x: a.x, y: b.y }, c2 = { x: b.x, y: a.y };
      const edges: [PixelPoint, PixelPoint][] = [[a, c1], [c1, b], [b, c2], [c2, a]];
      if (edges.some(([e0, e1]) => distToSegment(p, e0, e1) <= threshold)) {
        return { id: d.id, handle: "body" };
      }
      continue;
    }

    if (distToSegment(p, a, b) <= threshold) return { id: d.id, handle: "body" };
  }
  return null;
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/drawings.test.ts`
Expected: PASS (all Task 1 + Task 2 tests).

- [x] **Step 5: Commit**

```bash
git add frontend/src/lib/drawings.ts frontend/src/lib/drawings.test.ts
git commit -m "feat(drawings): bar-snapped projection and hit-test geometry"
```

---

### Task 3: Gesture reducer and drawing mutation

**Files:**
- Modify: `frontend/src/lib/drawings.ts` (append)
- Test: `frontend/src/lib/drawings.test.ts` (append)

**Interfaces:**
- Consumes: `Anchor`, `Drawing`, `Handle` from Tasks 1–2.
- Produces: `Tool` (`"cursor" | "trend" | "hline" | "rect" | "text"`), `DrawState`, `DrawEvent`, `DRAW_IDLE`, `drawReducer(s, e): DrawState`, `newDrawing(kind, id, at): Drawing`, `moveDrawing(d, handle, from, to): Drawing`.

- [x] **Step 1: Write the failing test**

Append to `frontend/src/lib/drawings.test.ts`:

```ts
import { drawReducer, newDrawing, moveDrawing, DRAW_IDLE, type DrawState } from "./drawings";

const A = { timeMs: 1_000_000, price: 100 };
const B = { timeMs: 1_600_000, price: 110 };

describe("newDrawing", () => {
  it("starts a trend with both anchors at the press point", () => {
    expect(newDrawing("trend", "id1", A)).toEqual({ id: "id1", kind: "trend", a: A, b: A });
  });

  it("starts an hline from the press price only", () => {
    expect(newDrawing("hline", "id2", A)).toEqual({ id: "id2", kind: "hline", price: 100 });
  });

  it("starts a text with an empty label", () => {
    expect(newDrawing("text", "id3", A)).toEqual({ id: "id3", kind: "text", a: A, text: "" });
  });
});

describe("drawReducer", () => {
  it("begin puts a draft in the drawing phase", () => {
    const s = drawReducer(DRAW_IDLE, { t: "begin", draft: newDrawing("trend", "id1", A) });
    expect(s.phase).toBe("drawing");
  });

  it("move extends the draft's second anchor", () => {
    let s: DrawState = drawReducer(DRAW_IDLE, { t: "begin", draft: newDrawing("trend", "id1", A) });
    s = drawReducer(s, { t: "move", at: B });
    expect(s).toEqual({ phase: "drawing", draft: { id: "id1", kind: "trend", a: A, b: B } });
  });

  it("move on an hline draft tracks the price, not a second anchor", () => {
    let s: DrawState = drawReducer(DRAW_IDLE, { t: "begin", draft: newDrawing("hline", "id2", A) });
    s = drawReducer(s, { t: "move", at: B });
    expect(s).toEqual({ phase: "drawing", draft: { id: "id2", kind: "hline", price: 110 } });
  });

  it("commit leaves the finished object selected", () => {
    let s: DrawState = drawReducer(DRAW_IDLE, { t: "begin", draft: newDrawing("trend", "id1", A) });
    s = drawReducer(s, { t: "commit" });
    expect(s).toEqual({ phase: "selected", id: "id1" });
  });

  it("cancel always returns to idle", () => {
    const drawing = drawReducer(DRAW_IDLE, { t: "begin", draft: newDrawing("rect", "id4", A) });
    expect(drawReducer(drawing, { t: "cancel" })).toEqual(DRAW_IDLE);
    expect(drawReducer({ phase: "selected", id: "z" }, { t: "cancel" })).toEqual(DRAW_IDLE);
  });

  it("grab enters dragging and remembers where the pointer grabbed", () => {
    const s = drawReducer({ phase: "selected", id: "t" }, { t: "grab", id: "t", handle: "body", at: A });
    expect(s).toEqual({ phase: "dragging", id: "t", handle: "body", from: A });
  });

  it("move while dragging keeps the original grab point", () => {
    let s: DrawState = drawReducer(DRAW_IDLE, { t: "grab", id: "t", handle: "b", at: A });
    s = drawReducer(s, { t: "move", at: B });
    expect(s).toEqual({ phase: "dragging", id: "t", handle: "b", from: A, at: B });
  });

  it("commit after a drag leaves the object selected", () => {
    let s: DrawState = drawReducer(DRAW_IDLE, { t: "grab", id: "t", handle: "a", at: A });
    s = drawReducer(s, { t: "commit" });
    expect(s).toEqual({ phase: "selected", id: "t" });
  });

  it("move and commit outside a gesture are no-ops", () => {
    expect(drawReducer(DRAW_IDLE, { t: "move", at: B })).toEqual(DRAW_IDLE);
    expect(drawReducer(DRAW_IDLE, { t: "commit" })).toEqual(DRAW_IDLE);
  });

  it("select replaces any selection", () => {
    expect(drawReducer({ phase: "selected", id: "a" }, { t: "select", id: "b" }))
      .toEqual({ phase: "selected", id: "b" });
  });
});

describe("moveDrawing", () => {
  const trend = { id: "t", kind: "trend" as const, a: A, b: B };

  it("moves only the grabbed endpoint", () => {
    const moved = moveDrawing(trend, "b", A, { timeMs: 2_000_000, price: 120 });
    expect(moved).toEqual({ id: "t", kind: "trend", a: A, b: { timeMs: 2_000_000, price: 120 } });
  });

  it("shifts both anchors by the pointer delta on a body drag", () => {
    const moved = moveDrawing(trend, "body", A, { timeMs: 1_060_000, price: 101 });
    expect(moved).toEqual({
      id: "t", kind: "trend",
      a: { timeMs: 1_060_000, price: 101 },
      b: { timeMs: 1_660_000, price: 111 },
    });
  });

  it("moves an hline to the pointer price and ignores time", () => {
    const moved = moveDrawing({ id: "h", kind: "hline", price: 105 }, "body", A, { timeMs: 9, price: 107 });
    expect(moved).toEqual({ id: "h", kind: "hline", price: 107 });
  });

  it("moves a text anchor on a body drag and leaves its label alone", () => {
    const t = { id: "x", kind: "text" as const, a: A, text: "supply" };
    expect(moveDrawing(t, "body", A, B)).toEqual({ id: "x", kind: "text", a: B, text: "supply" });
  });

  it("preserves an explicit colour through a move", () => {
    const coloured = { ...trend, color: "#ff0000" };
    expect(moveDrawing(coloured, "b", A, B).color).toBe("#ff0000");
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/drawings.test.ts`
Expected: FAIL — `drawReducer is not a function`.

- [x] **Step 3: Write minimal implementation**

Append to `frontend/src/lib/drawings.ts`:

```ts
export type Tool = "cursor" | DrawingKind;

export type DrawState =
  | { phase: "idle" }
  | { phase: "drawing"; draft: Drawing }
  | { phase: "selected"; id: string }
  | { phase: "dragging"; id: string; handle: Handle; from: Anchor; at?: Anchor };

export type DrawEvent =
  | { t: "begin"; draft: Drawing }
  | { t: "move"; at: Anchor }
  | { t: "commit" }
  | { t: "select"; id: string }
  | { t: "grab"; id: string; handle: Handle; at: Anchor }
  | { t: "cancel" };

export const DRAW_IDLE: DrawState = { phase: "idle" };

// A fresh object at the press point: both anchors coincide until the pointer
// moves, so a click without a drag produces a degenerate object the gesture
// hook discards rather than a half-built one.
export function newDrawing(kind: DrawingKind, id: string, at: Anchor): Drawing {
  switch (kind) {
    case "hline": return { id, kind, price: at.price };
    case "text": return { id, kind, a: at, text: "" };
    default: return { id, kind, a: at, b: at };
  }
}

// Pure transition. `commit` from either gesture phase leaves the object
// selected — that is what makes "draw it, then nudge its endpoint" one
// continuous flow without a second click.
export function drawReducer(s: DrawState, e: DrawEvent): DrawState {
  switch (e.t) {
    case "begin":
      return { phase: "drawing", draft: e.draft };
    case "move":
      if (s.phase === "drawing") {
        const d = s.draft;
        const draft: Drawing = d.kind === "hline"
          ? { ...d, price: e.at.price }
          : d.kind === "text" ? d : { ...d, b: e.at };
        return { phase: "drawing", draft };
      }
      if (s.phase === "dragging") return { ...s, at: e.at };
      return s;
    case "commit":
      if (s.phase === "drawing") return { phase: "selected", id: s.draft.id };
      if (s.phase === "dragging") return { phase: "selected", id: s.id };
      return s;
    case "select":
      return { phase: "selected", id: e.id };
    case "grab":
      return { phase: "dragging", id: e.id, handle: e.handle, from: e.at };
    case "cancel":
      return DRAW_IDLE;
  }
}

function shift(a: Anchor, from: Anchor, to: Anchor): Anchor {
  return { timeMs: a.timeMs + (to.timeMs - from.timeMs), price: a.price + (to.price - from.price) };
}

// Applies a drag to an object. `from` is where the pointer grabbed, `to` where
// it is now — a body drag shifts by the delta so the grab point stays under the
// cursor, while an endpoint drag snaps that endpoint to the pointer.
export function moveDrawing(d: Drawing, handle: Handle, from: Anchor, to: Anchor): Drawing {
  if (d.kind === "hline") return { ...d, price: to.price };
  if (d.kind === "text") return { ...d, a: handle === "body" ? shift(d.a, from, to) : to };
  if (handle === "a") return { ...d, a: to };
  if (handle === "b") return { ...d, b: to };
  return { ...d, a: shift(d.a, from, to), b: shift(d.b, from, to) };
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/drawings.test.ts`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add frontend/src/lib/drawings.ts frontend/src/lib/drawings.test.ts
git commit -m "feat(drawings): draw/select/drag reducer and pure mutation"
```

---

### Task 4: `prefs_store` drawings keys

**Files:**
- Modify: `src/journal/store/prefs_store.py`
- Test: `tests/test_prefs_store.py` (append)

**Interfaces:**
- Consumes: `journal.domain.symbols.to_base`.
- Produces: `prefs_store.drawings_key(symbol: str, session_id: int | None) -> str`, `prefs_store.get_drawings(conn, symbol, session_id) -> Any | None`, `prefs_store.set_drawings(conn, symbol, session_id, blob) -> int`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_prefs_store.py`:

```python
def test_drawings_key_uses_symbol_base_not_the_raw_symbol(conn):
    # Rule 11: the broker suffix never reaches a storage key.
    assert ps.drawings_key("XAUUSDc", None) == "drawings:XAUUSD"
    assert ps.drawings_key("XAUUSD", None) == "drawings:XAUUSD"


def test_drawings_key_for_a_replay_session_is_separate_from_live():
    # A replay session is symbol-bound by construction, so its key carries the
    # session id instead of the symbol. Live drawings must never leak in.
    assert ps.drawings_key("XAUUSDc", 42) == "drawings:replay:42"
    assert ps.drawings_key("XAUUSDc", 42) != ps.drawings_key("XAUUSDc", None)


def test_drawings_roundtrip_parses_json(conn):
    assert ps.get_drawings(conn, "XAUUSDc", None) is None
    blob = {"v": 1, "items": [{"id": "d1", "kind": "hline", "price": 2415.5}]}
    ts = ps.set_drawings(conn, "XAUUSDc", None, blob)
    assert isinstance(ts, int) and ts > 0
    assert ps.get_drawings(conn, "XAUUSDc", None) == blob


def test_drawings_are_isolated_per_symbol_and_per_session(conn):
    gold = {"v": 1, "items": [{"id": "g", "kind": "hline", "price": 2415.5}]}
    btc = {"v": 1, "items": [{"id": "b", "kind": "hline", "price": 61000.0}]}
    replay = {"v": 1, "items": [{"id": "r", "kind": "hline", "price": 2400.0}]}
    ps.set_drawings(conn, "XAUUSDc", None, gold)
    ps.set_drawings(conn, "BTCUSDc", None, btc)
    ps.set_drawings(conn, "XAUUSDc", 7, replay)

    assert ps.get_drawings(conn, "XAUUSDc", None) == gold
    assert ps.get_drawings(conn, "BTCUSDc", None) == btc
    assert ps.get_drawings(conn, "XAUUSDc", 7) == replay
    assert ps.get_drawings(conn, "XAUUSDc", 8) is None


def test_set_drawings_upserts_one_row_per_key(conn):
    ps.set_drawings(conn, "XAUUSDc", None, {"v": 1, "items": []})
    ps.set_drawings(conn, "XAUUSDc", None, {"v": 1, "items": [{"id": "x", "kind": "hline", "price": 1.0}]})
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM app_prefs WHERE key = 'drawings:XAUUSD'"
    ).fetchone()
    assert row["n"] == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prefs_store.py -q`
Expected: FAIL with `AttributeError: module 'journal.store.prefs_store' has no attribute 'drawings_key'`.

- [x] **Step 3: Write minimal implementation**

In `src/journal/store/prefs_store.py`, add the import below the existing ones:

```python
from ..domain.symbols import to_base
```

and append at the end of the file:

```python
DRAWINGS_PREFIX = "drawings"


def drawings_key(symbol: str, session_id: int | None) -> str:
    """Storage key for a chart's hand-drawn annotations.

    Live/normal chart: `drawings:<symbol_base>` — grouped by base, never by the
    raw broker symbol (rule 11), so XAUUSDc and a future XAUUSD share a level.

    Replay: `drawings:replay:<session_id>`. A replay session is symbol-bound by
    construction, so the id alone identifies it — and keeping it off the live
    key is the point: live drawings were made knowing what happened next, and
    showing them during training would leak the answer.
    """
    if session_id is not None:
        return f"{DRAWINGS_PREFIX}:replay:{int(session_id)}"
    return f"{DRAWINGS_PREFIX}:{to_base(symbol)}"


def get_drawings(conn: sqlite3.Connection, symbol: str,
                 session_id: int | None = None) -> Any | None:
    """Parsed drawings blob for this symbol/session, or None if never saved."""
    raw = get_pref(conn, drawings_key(symbol, session_id))
    return json.loads(raw) if raw is not None else None


def set_drawings(conn: sqlite3.Connection, symbol: str,
                 session_id: int | None, blob: Any) -> int:
    """Persist the drawings blob verbatim (the client owns its schema, exactly
    like the other prefs wrappers). Returns the updated_ms stamp."""
    return set_pref(conn, drawings_key(symbol, session_id), json.dumps(blob), now_ms())
```

Also extend the module docstring's first line so it no longer claims the table holds only single-value preferences:

```python
"""app_prefs — single-value application preferences and small per-key JSON blobs
(chart settings, replay config, risk sizing, chart drawings), pure DB. The web
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prefs_store.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/journal/store/prefs_store.py tests/test_prefs_store.py
git commit -m "feat(drawings): app_prefs keys for chart drawings, per symbol and replay session"
```

---

### Task 5: `/api/drawings` endpoints

**Files:**
- Modify: `src/journal/web/app.py` (insert after `api_put_replay_prefs`, around line 272)
- Test: `tests/test_web.py` (append)

**Interfaces:**
- Consumes: `prefs_store.get_drawings` / `set_drawings` from Task 4.
- Produces: routes named `api_get_drawings` (`GET /api/drawings`) and `api_put_drawings` (`PUT /api/drawings`). GET returns `{"drawings": <blob|null>}`; PUT returns `{"ok": True, "updated_ms": int}` or a 400 `{"error": ...}`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_api_drawings_get_null_then_put_then_get(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_drawings")
    put_fn = _endpoint(app, "api_put_drawings")

    resp = get_fn(symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"drawings": None}

    blob = {"v": 1, "items": [
        {"id": "d1", "kind": "hline", "price": 2415.5},
        {"id": "d2", "kind": "trend",
         "a": {"timeMs": 1_700_000_000_000, "price": 2400.0},
         "b": {"timeMs": 1_700_003_600_000, "price": 2420.0}},
    ]}
    put = put_fn(body=blob, symbol="XAUUSDc", session_id=None, conn=conn)
    put_body = json.loads(put.body)
    assert put_body["ok"] is True and isinstance(put_body["updated_ms"], int)

    resp2 = get_fn(symbol="XAUUSDc", session_id=None, conn=conn)
    assert json.loads(resp2.body) == {"drawings": blob}


def test_api_drawings_normalises_the_broker_suffix(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_drawings")
    put_fn = _endpoint(app, "api_put_drawings")
    blob = {"v": 1, "items": [{"id": "d1", "kind": "hline", "price": 2415.5}]}

    put_fn(body=blob, symbol="XAUUSDc", session_id=None, conn=conn)
    # Same base symbol without the suffix reads the same drawings (rule 11).
    resp = get_fn(symbol="XAUUSD", session_id=None, conn=conn)
    assert json.loads(resp.body) == {"drawings": blob}


def test_api_drawings_replay_session_does_not_see_live_drawings(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_drawings")
    put_fn = _endpoint(app, "api_put_drawings")

    live = {"v": 1, "items": [{"id": "live", "kind": "hline", "price": 2415.5}]}
    put_fn(body=live, symbol="XAUUSDc", session_id=None, conn=conn)

    resp = get_fn(symbol="XAUUSDc", session_id=3, conn=conn)
    assert json.loads(resp.body) == {"drawings": None}


def test_api_drawings_put_rejects_a_non_object_body(conn):
    app = create_app(":memory:")
    put_fn = _endpoint(app, "api_put_drawings")
    resp = put_fn(body=[1, 2, 3], symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 400
    assert "error" in json.loads(resp.body)


def test_api_drawings_put_rejects_an_oversized_blob(conn):
    app = create_app(":memory:")
    put_fn = _endpoint(app, "api_put_drawings")
    huge = {"v": 1, "items": [{"id": "x" * 300_000, "kind": "hline", "price": 1.0}]}
    resp = put_fn(body=huge, symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 400
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web.py -q -k drawings`
Expected: FAIL with `AssertionError: no route named 'api_get_drawings'`.

- [x] **Step 3: Write minimal implementation**

In `src/journal/web/app.py`, insert directly after the `api_put_replay_prefs` function:

```python
    # --- chart drawings (hand annotations). Pure DB, like the prefs endpoints
    # above: the blob is stored verbatim and the client owns its schema. The
    # server only enforces that it is a JSON object of sane size — a broken
    # client must not be able to write junk unbounded — and normalises the
    # symbol to its base (rule 11) so the key never carries a broker suffix.
    MAX_DRAWINGS_BYTES = 256 * 1024

    @app.get("/api/drawings")
    def api_get_drawings(
        symbol: str,
        session_id: int | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Drawings blob for a symbol, or for one replay session when
        `session_id` is given. `drawings` is null until the first save."""
        return JSONResponse({"drawings": prefs_store.get_drawings(conn, symbol, session_id)})

    @app.put("/api/drawings")
    def api_put_drawings(
        symbol: str,
        body=Body(...),
        session_id: int | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the drawings blob. The server stamps updated_ms."""
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        if len(json.dumps(body)) > MAX_DRAWINGS_BYTES:
            return JSONResponse(
                {"error": f"drawings blob exceeds {MAX_DRAWINGS_BYTES} bytes"}, status_code=400,
            )
        ts = prefs_store.set_drawings(conn, symbol, session_id, body)
        return JSONResponse({"ok": True, "updated_ms": ts})
```

If `json` is not already imported at the top of `src/journal/web/app.py`, add `import json` there (check first — do not add a duplicate import).

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_web.py -q -k drawings`
Expected: PASS (5 tests).

- [x] **Step 5: Commit**

```bash
git add src/journal/web/app.py tests/test_web.py
git commit -m "feat(drawings): GET/PUT /api/drawings with base-symbol keys and body limits"
```

---

### Task 6: `useDrawings` hook

**Files:**
- Create: `frontend/src/hooks/useDrawings.ts`
- Test: `frontend/src/hooks/useDrawings.test.ts`

**Interfaces:**
- Consumes: `parseDrawings`, `BLOB_VERSION`, `Drawing` from Task 1; the endpoints from Task 5.
- Produces: `useDrawings(symbol: string, sessionId: number | null, enabled: boolean): { items: Drawing[]; add(d: Drawing): void; update(d: Drawing): void; remove(id: string): void; clear(): void }`.

- [x] **Step 1: Write the failing test**

Create `frontend/src/hooks/useDrawings.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useDrawings } from "./useDrawings";
import type { Drawing } from "../lib/drawings";

const hline: Drawing = { id: "d1", kind: "hline", price: 105 };

function mockFetch(getBody: unknown) {
  return vi.fn((url: string, init?: RequestInit) => {
    if (!init || init.method !== "PUT") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(getBody) } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) } as Response);
  });
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("useDrawings", () => {
  it("loads and parses the stored blob on mount", async () => {
    const f = mockFetch({ drawings: { v: 1, items: [hline] } });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([hline]));
    expect(f.mock.calls[0][0]).toBe("/api/drawings?symbol=XAUUSDc");
  });

  it("passes session_id in the query when replaying", async () => {
    const f = mockFetch({ drawings: null });
    vi.stubGlobal("fetch", f);
    renderHook(() => useDrawings("XAUUSDc", 42, true));
    await waitFor(() => expect(f.mock.calls[0][0]).toBe("/api/drawings?symbol=XAUUSDc&session_id=42"));
  });

  it("does not fetch at all when disabled", () => {
    const f = mockFetch({ drawings: null });
    vi.stubGlobal("fetch", f);
    renderHook(() => useDrawings("XAUUSDc", null, false));
    expect(f).not.toHaveBeenCalled();
  });

  it("drops a corrupt blob instead of throwing", async () => {
    const f = mockFetch({ drawings: { v: 9, items: [hline] } });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([]));
  });

  it("add/update/remove/clear mutate items and schedule one debounced PUT", async () => {
    const f = mockFetch({ drawings: { v: 1, items: [] } });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([]));

    act(() => { result.current.add(hline); });
    expect(result.current.items).toEqual([hline]);

    act(() => { result.current.update({ ...hline, price: 111 }); });
    expect(result.current.items).toEqual([{ ...hline, price: 111 }]);

    // Both mutations coalesce into ONE PUT after the debounce window.
    const putsBefore = f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT").length;
    expect(putsBefore).toBe(0);
    await act(async () => { vi.advanceTimersByTime(500); });
    const puts = f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(JSON.parse(puts[0][1]!.body as string)).toEqual({ v: 1, items: [{ ...hline, price: 111 }] });

    act(() => { result.current.remove("d1"); });
    expect(result.current.items).toEqual([]);
    act(() => { result.current.add(hline); result.current.clear(); });
    expect(result.current.items).toEqual([]);
  });

  it("keeps working when the GET fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([]));
    act(() => { result.current.add(hline); });
    expect(result.current.items).toEqual([hline]);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useDrawings.test.ts`
Expected: FAIL — `Failed to resolve import "./useDrawings"`.

- [x] **Step 3: Write minimal implementation**

Create `frontend/src/hooks/useDrawings.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { BLOB_VERSION, parseDrawings, type Drawing } from "../lib/drawings";

const DEBOUNCE_MS = 400;

function url(symbol: string, sessionId: number | null): string {
  const q = new URLSearchParams({ symbol });
  if (sessionId !== null) q.set("session_id", String(sessionId));
  return `/api/drawings?${q.toString()}`;
}

// Drawings live only in the DB — unlike chart prefs there is no localStorage
// mirror, because a drawing belongs to the symbol (and to a replay session),
// not to the browser that happened to make it.
export function useDrawings(symbol: string, sessionId: number | null, enabled: boolean): {
  items: Drawing[];
  add: (d: Drawing) => void;
  update: (d: Drawing) => void;
  remove: (id: string) => void;
  clear: () => void;
} {
  const [items, setItems] = useState<Drawing[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const target = useRef(url(symbol, sessionId));
  target.current = url(symbol, sessionId);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setItems([]);                       // never show one symbol's drawings on another
    fetch(url(symbol, sessionId))
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { drawings: unknown } | null) => {
        if (!alive || !body) return;
        setItems(parseDrawings(body.drawings));
      })
      .catch(() => { /* offline — start empty, mutations still work locally */ });
    return () => { alive = false; };
  }, [symbol, sessionId, enabled]);

  // One debounced PUT per burst of edits. A dropped PUT loses at most the last
  // edit; in-memory state stays correct until reload.
  const schedule = useCallback((next: Drawing[]) => {
    if (timer.current) clearTimeout(timer.current);
    const to = target.current;
    timer.current = setTimeout(() => {
      void fetch(to, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ v: BLOB_VERSION, items: next }),
      }).catch(() => { /* offline — annotations only */ });
    }, DEBOUNCE_MS);
  }, []);

  const apply = useCallback((fn: (prev: Drawing[]) => Drawing[]) => {
    setItems((prev) => {
      const next = fn(prev);
      schedule(next);
      return next;
    });
  }, [schedule]);

  const add = useCallback((d: Drawing) => apply((prev) => [...prev, d]), [apply]);
  const update = useCallback(
    (d: Drawing) => apply((prev) => prev.map((x) => (x.id === d.id ? d : x))), [apply],
  );
  const remove = useCallback(
    (id: string) => apply((prev) => prev.filter((x) => x.id !== id)), [apply],
  );
  const clear = useCallback(() => apply(() => []), [apply]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { items, add, update, remove, clear };
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/hooks/useDrawings.test.ts`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git add frontend/src/hooks/useDrawings.ts frontend/src/hooks/useDrawings.test.ts
git commit -m "feat(drawings): useDrawings hook with debounced write-through"
```

---

### Task 7: `DrawingOverlay` SVG

**Files:**
- Create: `frontend/src/components/DrawingOverlay.tsx`
- Test: `frontend/src/components/DrawingOverlay.test.tsx`

**Interfaces:**
- Consumes: `Projected`, `colorOf` from Tasks 1–2.
- Produces: default-exported `DrawingOverlay({ projected, selectedId }: { projected: Projected[]; selectedId: string | null })`. Each rendered object carries `data-testid={"drawing-" + id}`; each selection handle carries `data-testid={"handle-" + id + "-" + which}`.

- [x] **Step 1: Write the failing test**

Create `frontend/src/components/DrawingOverlay.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import DrawingOverlay from "./DrawingOverlay";
import { projectDrawing, type ProjectCtx } from "../lib/drawings";

const candles = Array.from({ length: 10 }, (_, i) => ({ time_msc: 1_000_000 + i * 60_000 }));
const ctx: ProjectCtx = {
  width: 500,
  candles,
  logicalToX: (i) => i * 10,
  priceToY: (p) => 200 - (p - 100) * 10,
};

const trend = projectDrawing(
  { id: "t", kind: "trend", a: { timeMs: 1_000_000, price: 100 }, b: { timeMs: 1_240_000, price: 110 } },
  ctx,
);
const hline = projectDrawing({ id: "h", kind: "hline", price: 105 }, ctx);
const rect = projectDrawing(
  { id: "r", kind: "rect", a: { timeMs: 1_000_000, price: 110 }, b: { timeMs: 1_240_000, price: 100 } },
  ctx,
);
const text = projectDrawing(
  { id: "x", kind: "text", a: { timeMs: 1_060_000, price: 105 }, text: "supply" },
  ctx,
);

describe("DrawingOverlay", () => {
  it("renders one element per kind", () => {
    render(<DrawingOverlay projected={[trend, hline, rect, text]} selectedId={null} />);
    expect(screen.getByTestId("drawing-t").tagName.toLowerCase()).toBe("line");
    expect(screen.getByTestId("drawing-h").tagName.toLowerCase()).toBe("line");
    expect(screen.getByTestId("drawing-r").tagName.toLowerCase()).toBe("rect");
    expect(screen.getByTestId("drawing-x").textContent).toBe("supply");
  });

  it("places the trend line on its projected endpoints", () => {
    render(<DrawingOverlay projected={[trend]} selectedId={null} />);
    const line = screen.getByTestId("drawing-t");
    expect(line.getAttribute("x1")).toBe("0");
    expect(line.getAttribute("y1")).toBe("200");
    expect(line.getAttribute("x2")).toBe("40");
    expect(line.getAttribute("y2")).toBe("100");
  });

  it("normalises a rect drawn from any corner", () => {
    render(<DrawingOverlay projected={[rect]} selectedId={null} />);
    const r = screen.getByTestId("drawing-r");
    expect(r.getAttribute("x")).toBe("0");
    expect(r.getAttribute("y")).toBe("100");
    expect(r.getAttribute("width")).toBe("40");
    expect(r.getAttribute("height")).toBe("100");
  });

  it("shows handles only for the selected object", () => {
    const { rerender } = render(<DrawingOverlay projected={[trend]} selectedId={null} />);
    expect(screen.queryByTestId("handle-t-a")).toBeNull();
    rerender(<DrawingOverlay projected={[trend]} selectedId="t" />);
    expect(screen.getByTestId("handle-t-a")).toBeTruthy();
    expect(screen.getByTestId("handle-t-b")).toBeTruthy();
  });

  it("omits an object whose projection is null", () => {
    const off = projectDrawing(
      { id: "off", kind: "trend", a: { timeMs: 1, price: 100 }, b: { timeMs: 1_240_000, price: 110 } },
      ctx,
    );
    render(<DrawingOverlay projected={[off]} selectedId={null} />);
    expect(screen.queryByTestId("drawing-off")).toBeNull();
  });

  it("does not intercept pointer events", () => {
    const { container } = render(<DrawingOverlay projected={[trend]} selectedId={null} />);
    const svg = container.querySelector("svg")!;
    expect(svg.style.pointerEvents).toBe("none");
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/DrawingOverlay.test.tsx`
Expected: FAIL — `Failed to resolve import "./DrawingOverlay"`.

- [x] **Step 3: Write minimal implementation**

Create `frontend/src/components/DrawingOverlay.tsx`:

```tsx
import { colorOf, type Projected } from "../lib/drawings";

// Absolute SVG over the chart pane, pointer-events:none so the chart stays
// interactive — the same arrangement MeasureOverlay uses. Everything here is a
// pure function of already-projected pixels; no chart API, no state.
export default function DrawingOverlay({
  projected, selectedId,
}: {
  projected: Projected[];
  selectedId: string | null;
}) {
  return (
    <svg
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: "none" }}
      data-testid="drawing-overlay"
    >
      {projected.map(({ d, a, b }) => {
        if (a === null) return null;
        const color = colorOf(d);
        const selected = d.id === selectedId;
        const handles = selected && b !== null && d.kind !== "hline" ? (
          <>
            <circle data-testid={`handle-${d.id}-a`} cx={a.x} cy={a.y} r={4} fill={color} />
            <circle data-testid={`handle-${d.id}-b`} cx={b.x} cy={b.y} r={4} fill={color} />
          </>
        ) : null;

        if (d.kind === "text") {
          return (
            <g key={d.id}>
              <text
                data-testid={`drawing-${d.id}`}
                x={a.x + 4} y={a.y}
                fill={color}
                style={{ font: "11px/1.35 ui-monospace, monospace" }}
              >
                {d.text}
              </text>
              {selected && (
                <circle data-testid={`handle-${d.id}-a`} cx={a.x} cy={a.y} r={4} fill={color} />
              )}
            </g>
          );
        }

        if (b === null) return null;

        if (d.kind === "rect") {
          return (
            <g key={d.id}>
              <rect
                data-testid={`drawing-${d.id}`}
                x={Math.min(a.x, b.x)} y={Math.min(a.y, b.y)}
                width={Math.abs(b.x - a.x)} height={Math.abs(b.y - a.y)}
                fill={color} fillOpacity={0.10}
                stroke={color} strokeWidth={selected ? 2 : 1}
              />
              {handles}
            </g>
          );
        }

        return (
          <g key={d.id}>
            <line
              data-testid={`drawing-${d.id}`}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={color} strokeWidth={selected ? 2 : 1.5}
              strokeDasharray={d.kind === "hline" ? "4 3" : undefined}
            />
            {handles}
          </g>
        );
      })}
    </svg>
  );
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/DrawingOverlay.test.tsx`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git add frontend/src/components/DrawingOverlay.tsx frontend/src/components/DrawingOverlay.test.tsx
git commit -m "feat(drawings): SVG overlay for trend, hline, rect and text"
```

---

### Task 8: `DrawingPalette`

**Files:**
- Create: `frontend/src/components/DrawingPalette.tsx`
- Test: `frontend/src/components/DrawingPalette.test.tsx`

**Interfaces:**
- Consumes: `Tool` from Task 3.
- Produces: default-exported `DrawingPalette({ tool, onTool, onClearAll, count }: { tool: Tool; onTool: (t: Tool) => void; onClearAll: () => void; count: number })`.

- [x] **Step 1: Write the failing test**

Create `frontend/src/components/DrawingPalette.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DrawingPalette from "./DrawingPalette";

describe("DrawingPalette", () => {
  it("reports the tool that was clicked", () => {
    const onTool = vi.fn();
    render(<DrawingPalette tool="cursor" onTool={onTool} onClearAll={() => {}} count={0} />);
    fireEvent.click(screen.getByRole("button", { name: "trendline" }));
    expect(onTool).toHaveBeenCalledWith("trend");
  });

  it("marks the active tool as pressed", () => {
    render(<DrawingPalette tool="rect" onTool={() => {}} onClearAll={() => {}} count={0} />);
    expect(screen.getByRole("button", { name: "kotak" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "kursor" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("hides clear-all when there is nothing to clear", () => {
    render(<DrawingPalette tool="cursor" onTool={() => {}} onClearAll={() => {}} count={0} />);
    expect(screen.queryByRole("button", { name: /hapus semua/i })).toBeNull();
  });

  it("requires a second click to clear all", () => {
    const onClearAll = vi.fn();
    render(<DrawingPalette tool="cursor" onTool={() => {}} onClearAll={onClearAll} count={3} />);
    const btn = screen.getByRole("button", { name: /hapus semua/i });
    fireEvent.click(btn);
    expect(onClearAll).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /yakin/i }));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/DrawingPalette.test.tsx`
Expected: FAIL — `Failed to resolve import "./DrawingPalette"`.

- [x] **Step 3: Write minimal implementation**

Create `frontend/src/components/DrawingPalette.tsx`:

```tsx
import { useState } from "react";
import type { Tool } from "../lib/drawings";

const TOOLS: { tool: Tool; icon: string; label: string }[] = [
  { tool: "cursor", icon: "⌖", label: "kursor" },
  { tool: "trend", icon: "╱", label: "trendline" },
  { tool: "hline", icon: "─", label: "garis horizontal" },
  { tool: "rect", icon: "▭", label: "kotak" },
  { tool: "text", icon: "T", label: "teks" },
];

// Vertical icon column on the pane's left edge (TradingView layout). Clearing
// every drawing is destructive and unrecoverable, so it takes two clicks — a
// modal would be heavier than the action deserves, but one click is too few.
export default function DrawingPalette({
  tool, onTool, onClearAll, count,
}: {
  tool: Tool;
  onTool: (t: Tool) => void;
  onClearAll: () => void;
  count: number;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="glass absolute left-2 top-2 z-20 flex flex-col p-1 gap-1 text-[13px]">
      {TOOLS.map(({ tool: t, icon, label }) => (
        <button
          key={t}
          aria-label={label}
          aria-pressed={tool === t}
          title={label}
          onClick={() => onTool(t)}
          className={
            "w-7 h-7 leading-none " +
            (tool === t ? "bg-violet/25 text-ink" : "text-muted hover:text-ink")
          }
        >
          {icon}
        </button>
      ))}
      {count > 0 && (
        <button
          aria-label={confirming ? "yakin hapus semua" : "hapus semua"}
          title={confirming ? "Klik lagi untuk menghapus" : "Hapus semua gambar"}
          onClick={() => {
            if (confirming) { onClearAll(); setConfirming(false); } else { setConfirming(true); }
          }}
          onBlur={() => setConfirming(false)}
          className={"w-7 h-7 leading-none " + (confirming ? "text-neg" : "text-muted hover:text-neg")}
        >
          {confirming ? "!" : "🗑"}
        </button>
      )}
    </div>
  );
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/DrawingPalette.test.tsx`
Expected: PASS (4 tests).

- [x] **Step 5: Commit**

```bash
git add frontend/src/components/DrawingPalette.tsx frontend/src/components/DrawingPalette.test.tsx
git commit -m "feat(drawings): vertical tool palette with two-click clear-all"
```

---

### Task 9: `useDrawingGesture` + `CandleChart` wiring (trend / hline / rect)

The text tool is deliberately deferred to Task 10 — this task delivers the three drag-drawn kinds end to end.

**Files:**
- Create: `frontend/src/hooks/useDrawingGesture.ts`
- Modify: `frontend/src/components/CandleChart.tsx`
- Test: `frontend/src/components/CandleChart.test.tsx` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–3, `DrawingOverlay` (Task 7), `DrawingPalette` (Task 8).
- Produces:
  - `useDrawingGesture(opts: DrawingGestureOpts): { state: DrawState; draft: Drawing | null; selectedId: string | null }` where

    ```ts
    export interface DrawingGestureOpts {
      node: HTMLElement | null;
      enabled: boolean;
      tool: Tool;
      items: Drawing[];
      projected: Projected[];
      toAnchor: (x: number, y: number) => Anchor | null;
      reserved: (x: number, y: number, e: PointerEvent) => boolean;
      onAdd: (d: Drawing) => void;
      onUpdate: (d: Drawing) => void;
      onDelete: (id: string) => void;
      onToolDone: () => void;
      suppressPan: (off: boolean) => void;
    }
    ```
  - A new optional `CandleChart` prop:

    ```ts
    drawings?: {
      items: Drawing[];
      editable: boolean;
      onAdd: (d: Drawing) => void;
      onUpdate: (d: Drawing) => void;
      onDelete: (id: string) => void;
      onClearAll: () => void;
    };
    ```

    Absent → no palette, no overlay, no listeners. `Lab.tsx` passes nothing and is unaffected.

- [x] **Step 1: Write the failing test**

Append to `frontend/src/components/CandleChart.test.tsx`. Note the harness already stubs `priceToCoordinate`/`coordinateToPrice` (y=200↔price 100, 1px = 0.1 price) and `coordinateToLogical = (x) => x`; add `logicalToCoordinate` as the identity in the same `createChart` mock, immediately after the `ts.coordinateToLogical` line:

```ts
      ts.logicalToCoordinate = (i: number) => i;
```

Then append the tests:

```tsx
describe("drawing gesture", () => {
  const drawingProps = (over: Partial<NonNullable<ComponentProps<typeof CandleChart>["drawings"]>> = {}) => ({
    items: [],
    editable: true,
    onAdd: vi.fn(),
    onUpdate: vi.fn(),
    onDelete: vi.fn(),
    onClearAll: vi.fn(),
    ...over,
  });

  const base = {
    symbol: "XAUUSDc" as const,
    tf: "M1" as const,
    settings: DEFAULT_SETTINGS,
    candles: mockCandles,
    onHover: () => {},
    onNowVisibleChange: () => {},
    onRequestOlder: () => {},
    lastBarMs: null,
    live: null,
    nowVisible: false,
  };

  it("renders neither palette nor overlay when the drawings prop is absent", () => {
    const { container, queryByTestId } = render(<CandleChart {...base} />);
    expect(queryByTestId("drawing-overlay")).toBeNull();
    expect(container.querySelector('[aria-label="trendline"]')).toBeNull();
  });

  it("renders the palette when editable and hides it when read-only", () => {
    const { container, rerender } = render(<CandleChart {...base} drawings={drawingProps()} />);
    expect(container.querySelector('[aria-label="trendline"]')).toBeTruthy();
    rerender(<CandleChart {...base} drawings={drawingProps({ editable: false })} />);
    expect(container.querySelector('[aria-label="trendline"]')).toBeNull();
  });

  it("draws a trendline from press to release and returns the tool to cursor", () => {
    const props = drawingProps();
    const { container } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="trendline"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;

    fireEvent.pointerDown(pane, { clientX: 10, clientY: 200 });
    fireEvent.pointerMove(window, { clientX: 60, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 60, clientY: 100 });

    expect(props.onAdd).toHaveBeenCalledTimes(1);
    const added = props.onAdd.mock.calls[0][0];
    expect(added.kind).toBe("trend");
    expect(added.a.price).toBeCloseTo(100, 6);
    expect(added.b.price).toBeCloseTo(110, 6);
    // one object per tool click: the palette falls back to cursor afterwards
    expect(container.querySelector('[aria-label="kursor"]')!.getAttribute("aria-pressed")).toBe("true");
  });

  it("discards a degenerate object drawn with no movement", () => {
    const props = drawingProps();
    const { container } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="kotak"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 10, clientY: 200 });
    fireEvent.pointerUp(window, { clientX: 10, clientY: 200 });
    expect(props.onAdd).not.toHaveBeenCalled();
  });

  it("escape cancels an in-progress draw", () => {
    const props = drawingProps();
    const { container } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="trendline"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 10, clientY: 200 });
    fireEvent.pointerMove(window, { clientX: 60, clientY: 100 });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.pointerUp(window, { clientX: 60, clientY: 100 });
    expect(props.onAdd).not.toHaveBeenCalled();
  });

  it("selects an existing drawing and deletes it with the Delete key", () => {
    const hline = { id: "h1", kind: "hline" as const, price: 105 };
    const props = drawingProps({ items: [hline] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    // price 105 sits at y=150 under the harness mapping
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 150 });
    fireEvent.keyDown(window, { key: "Delete" });
    expect(props.onDelete).toHaveBeenCalledWith("h1");
  });

  it("drags a selected hline to a new price", () => {
    const hline = { id: "h1", kind: "hline" as const, price: 105 };
    const props = drawingProps({ items: [hline] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 100 });
    expect(props.onUpdate).toHaveBeenCalledTimes(1);
    expect(props.onUpdate.mock.calls[0][0].price).toBeCloseTo(110, 6);
  });

  it("lets an SL/TP line win over a drawing at the same pixel", () => {
    const onSlTpChange = vi.fn();
    const positions: DraggablePosition[] = [
      { id: 5, direction: "buy", entry_price: 105, sl: 100, tp: 110 },
    ];
    // A drawing sits exactly on the SL line (price 100 → y=200).
    const props = drawingProps({ items: [{ id: "h1", kind: "hline", price: 100 }] });
    const { container } = render(
      <CandleChart {...base} drawings={props} draggablePositions={positions} onSlTpChange={onSlTpChange} />,
    );
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 200 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 190 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 190 });
    expect(onSlTpChange).toHaveBeenCalledTimes(1);
    expect(props.onUpdate).not.toHaveBeenCalled();
  });

  it("attaches no drawing listeners when read-only", () => {
    const props = drawingProps({ editable: false, items: [{ id: "h1", kind: "hline", price: 105 }] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 100 });
    expect(props.onUpdate).not.toHaveBeenCalled();
    // …but the object is still drawn
    expect(container.querySelector('[data-testid="drawing-h1"]')).toBeTruthy();
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/CandleChart.test.tsx`
Expected: FAIL — the `drawings` prop does not exist; no palette or overlay renders.

- [x] **Step 3: Write minimal implementation**

Create `frontend/src/hooks/useDrawingGesture.ts`:

```ts
import { useEffect, useRef, useState } from "react";
import {
  DRAW_IDLE, drawReducer, hitTest, moveDrawing, newDrawing,
  type Anchor, type DrawState, type Drawing, type Projected, type Tool,
} from "../lib/drawings";

export interface DrawingGestureOpts {
  node: HTMLElement | null;
  enabled: boolean;
  tool: Tool;
  items: Drawing[];
  projected: Projected[];
  toAnchor: (x: number, y: number) => Anchor | null;
  // True when something with a stronger claim owns this press — an SL/TP price
  // line, or the second down of a measure double-click-hold. The gesture then
  // stands aside instead of consuming the event.
  reserved: (x: number, y: number, e: PointerEvent) => boolean;
  onAdd: (d: Drawing) => void;
  onUpdate: (d: Drawing) => void;
  onDelete: (id: string) => void;
  onToolDone: () => void;
  suppressPan: (off: boolean) => void;
}

// Owns every pointer/key listener for drawing. Listens in the CAPTURE phase and
// stops propagation only for presses it actually consumes, so CandleChart's own
// measure and SL/TP handlers keep working untouched for everything else.
export function useDrawingGesture(o: DrawingGestureOpts): {
  state: DrawState;
  draft: Drawing | null;
  selectedId: string | null;
} {
  const [state, setState] = useState<DrawState>(DRAW_IDLE);
  const opts = useRef(o);
  opts.current = o;

  useEffect(() => {
    const node = o.node;
    if (!node || !o.enabled) return;

    const rel = (e: PointerEvent) => {
      const r = node.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      const { tool, projected, toAnchor, reserved, suppressPan } = opts.current;
      const { x, y } = rel(e);

      if (tool !== "cursor") {
        const at = toAnchor(x, y);
        if (!at) return;
        suppressPan(true);
        setState(drawReducer(DRAW_IDLE, {
          t: "begin", draft: newDrawing(tool, crypto.randomUUID(), at),
        }));
        e.preventDefault();
        e.stopPropagation();
        return;
      }

      if (reserved(x, y, e)) return;         // SL/TP or measure owns this press

      const hit = hitTest(projected, { x, y });
      if (!hit) { setState(DRAW_IDLE); return; }
      const at = toAnchor(x, y);
      if (!at) return;
      suppressPan(true);
      setState(drawReducer(DRAW_IDLE, { t: "grab", id: hit.id, handle: hit.handle, at }));
      e.preventDefault();
      e.stopPropagation();
    };

    const onMove = (e: PointerEvent) => {
      setState((s) => {
        if (s.phase !== "drawing" && s.phase !== "dragging") return s;
        const at = opts.current.toAnchor(rel(e).x, rel(e).y);
        return at ? drawReducer(s, { t: "move", at }) : s;
      });
    };

    const onUp = () => {
      setState((s) => {
        const { items, onAdd, onUpdate, onToolDone, suppressPan } = opts.current;
        if (s.phase === "drawing") {
          suppressPan(false);
          onToolDone();
          // A press with no drag leaves both anchors coincident — that is a
          // stray click, not an object.
          if (isDegenerate(s.draft)) return DRAW_IDLE;
          onAdd(s.draft);
          return drawReducer(s, { t: "commit" });
        }
        if (s.phase === "dragging") {
          suppressPan(false);
          const target = items.find((d) => d.id === s.id);
          if (target && s.at) onUpdate(moveDrawing(target, s.handle, s.from, s.at));
          return drawReducer(s, { t: "commit" });
        }
        return s;
      });
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        opts.current.suppressPan(false);
        setState(DRAW_IDLE);
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        setState((s) => {
          if (s.phase !== "selected") return s;
          opts.current.onDelete(s.id);
          return DRAW_IDLE;
        });
      }
    };

    const onCancel = () => { opts.current.suppressPan(false); setState(DRAW_IDLE); };

    node.addEventListener("pointerdown", onDown, true);   // capture
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointercancel", onCancel);
    return () => {
      node.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointercancel", onCancel);
    };
  }, [o.node, o.enabled]);

  const draft = state.phase === "drawing" ? state.draft : null;
  const selectedId = state.phase === "selected" ? state.id
    : state.phase === "dragging" ? state.id : null;
  return { state, draft, selectedId };
}

function isDegenerate(d: Drawing): boolean {
  if (d.kind === "hline") return false;
  if (d.kind === "text") return d.text.length === 0;
  return Math.abs(d.a.timeMs - d.b.timeMs) < 1
    && Math.abs(d.a.price - d.b.price) < 1e-9;
}
```

In `frontend/src/components/CandleChart.tsx`:

1. Add the imports:

```ts
import DrawingOverlay from "./DrawingOverlay";
import DrawingPalette from "./DrawingPalette";
import { useDrawingGesture } from "../hooks/useDrawingGesture";
import {
  moveDrawing, projectDrawing, type Anchor, type Drawing, type Projected, type Tool,
} from "../lib/drawings";
```

(`moveDrawing` is used by the drag preview below; keep the import list to what is actually referenced or `tsc` will fail on `noUnusedLocals`.)

2. Add the prop to the `forwardRef` prop type, after `hideDate?: boolean;`:

```ts
  drawings?: {
    items: Drawing[];
    editable: boolean;
    onAdd: (d: Drawing) => void;
    onUpdate: (d: Drawing) => void;
    onDelete: (id: string) => void;
    onClearAll: () => void;
  };
```

3. Add the tool state next to the other `useState` calls (after the `sltpGhost` state):

```ts
  const [tool, setTool] = useState<Tool>("cursor");
```

4. Above the `return`, next to the existing `project()` helper, add the drawing projection, the gesture wiring, and the overlay element:

```ts
  // Drawing projection. anchorToX (inside projectDrawing) snaps a time to the
  // containing bar rather than using timeToCoordinate, which returns null for
  // any timestamp that is not exactly a bar time — that is what lets a level
  // drawn on M15 still render on H1.
  const drawings = props.drawings;
  const paneWidth = el.current?.clientWidth ?? 0;
  const projectedDrawings: Projected[] = drawings
    ? drawings.items.map((d) => projectDrawing(d, {
        width: paneWidth,
        candles: props.candles,
        logicalToX: (i) => {
          const x = chart.current?.timeScale().logicalToCoordinate(i as never);
          return x === null || x === undefined ? null : (x as number);
        },
        priceToY: (p) => {
          const y = series.current?.priceToCoordinate(p);
          return y === null || y === undefined ? null : (y as number);
        },
      }))
    : [];

  const toAnchor = useCallback((x: number, y: number): Anchor | null => {
    const p = toPoint(x, y);
    return p === null ? null : { timeMs: p.barTimeMs, price: p.price };
  }, [toPoint]);

  const gesture = useDrawingGesture({
    node: el.current,
    enabled: !!drawings?.editable,
    tool,
    items: drawings?.items ?? [],
    projected: projectedDrawings,
    toAnchor,
    // An SL/TP line and the measure double-click-hold both outrank a drawing.
    reserved: (_x, y, e) =>
      hitTestLine(y) !== null
      || (lastUp.current !== null
          && isDoubleClickHold(lastUp.current.ms, lastUp.current.x, lastUp.current.y,
                               e.timeStamp, _x, y)),
    onAdd: (d) => drawings?.onAdd(d),
    onUpdate: (d) => drawings?.onUpdate(d),
    onDelete: (id) => drawings?.onDelete(id),
    onToolDone: () => setTool("cursor"),
    suppressPan: (off) => chart.current?.applyOptions({ handleScroll: !off, handleScale: !off }),
  });

  // Live preview: the in-progress draft, or the object under an active drag
  // shown at its would-be position. Neither is committed until pointerup.
  let previewProjected = projectedDrawings;
  if (gesture.draft) {
    previewProjected = [...projectedDrawings, projectDrawing(gesture.draft, {
      width: paneWidth,
      candles: props.candles,
      logicalToX: (i) => {
        const x = chart.current?.timeScale().logicalToCoordinate(i as never);
        return x === null || x === undefined ? null : (x as number);
      },
      priceToY: (p) => {
        const y = series.current?.priceToCoordinate(p);
        return y === null || y === undefined ? null : (y as number);
      },
    })];
  } else if (gesture.state.phase === "dragging" && gesture.state.at && drawings) {
    const s = gesture.state;
    const target = drawings.items.find((d) => d.id === s.id);
    if (target) {
      const moved = moveDrawing(target, s.handle, s.from, s.at);
      previewProjected = projectedDrawings.map((p) =>
        p.d.id === s.id
          ? projectDrawing(moved, {
              width: paneWidth,
              candles: props.candles,
              logicalToX: (i) => {
                const x = chart.current?.timeScale().logicalToCoordinate(i as never);
                return x === null || x === undefined ? null : (x as number);
              },
              priceToY: (pr) => {
                const y = series.current?.priceToCoordinate(pr);
                return y === null || y === undefined ? null : (y as number);
              },
            })
          : p);
    }
  }
```

To keep that readable, factor the repeated context into one local before the three uses and pass it:

```ts
  const drawCtx = {
    width: paneWidth,
    candles: props.candles,
    logicalToX: (i: number) => {
      const x = chart.current?.timeScale().logicalToCoordinate(i as never);
      return x === null || x === undefined ? null : (x as number);
    },
    priceToY: (p: number) => {
      const y = series.current?.priceToCoordinate(p);
      return y === null || y === undefined ? null : (y as number);
    },
  };
```

and use `projectDrawing(d, drawCtx)` in all three places.

5. Extend the returned JSX:

```tsx
  return (
    <div className="relative w-full h-full">
      <div ref={el} className="w-full h-full" />
      {overlay}
      {shadeOverlay}
      {drawings && (
        <DrawingOverlay projected={previewProjected} selectedId={gesture.selectedId} />
      )}
      {drawings?.editable && (
        <DrawingPalette
          tool={tool}
          onTool={setTool}
          onClearAll={drawings.onClearAll}
          count={drawings.items.length}
        />
      )}
    </div>
  );
```

6. The gesture hook's `node` argument is `el.current`, which is `null` on the first render. The existing `bumpProjection` re-render on `ResizeObserver` already fires after mount, but to be certain the listeners attach, add one line to the existing mount effect (the `ResizeObserver` one) right after `ro.observe(node)`:

```ts
    bumpProjection();   // el.current is now set: re-render so gesture listeners attach
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/CandleChart.test.tsx`
Expected: PASS — the 9 new drawing tests plus every pre-existing CandleChart test.

Then confirm nothing else regressed:

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit`
Expected: all suites PASS, no type errors.

- [x] **Step 5: Commit**

```bash
git add frontend/src/hooks/useDrawingGesture.ts frontend/src/components/CandleChart.tsx frontend/src/components/CandleChart.test.tsx
git commit -m "feat(drawings): pointer gesture and CandleChart wiring for trend/hline/rect"
```

---

### Task 10: Text tool inline editor

**Files:**
- Create: `frontend/src/components/TextDrawingInput.tsx`
- Modify: `frontend/src/components/CandleChart.tsx`
- Test: `frontend/src/components/CandleChart.test.tsx` (append)

**Interfaces:**
- Consumes: Task 9's gesture wiring, `MAX_TEXT_LEN` from Task 1.
- Produces: default-exported `TextDrawingInput({ x, y, initial, onCommit, onCancel }: { x: number; y: number; initial: string; onCommit: (text: string) => void; onCancel: () => void })`, carrying `data-testid="text-drawing-input"`.

- [x] **Step 1: Write the failing test**

Append to `frontend/src/components/CandleChart.test.tsx`, inside the existing `describe("drawing gesture", ...)` block:

```tsx
  it("opens an input for the text tool and commits the label on Enter", () => {
    const props = drawingProps();
    const { container, getByTestId } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="teks"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 150 });

    const input = getByTestId("text-drawing-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "supply zone" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(props.onAdd).toHaveBeenCalledTimes(1);
    const added = props.onAdd.mock.calls[0][0];
    expect(added.kind).toBe("text");
    expect(added.text).toBe("supply zone");
    expect(added.a.price).toBeCloseTo(105, 6);
  });

  it("discards an empty label instead of storing a blank note", () => {
    const props = drawingProps();
    const { container, getByTestId, queryByTestId } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="teks"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 150 });

    const input = getByTestId("text-drawing-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(props.onAdd).not.toHaveBeenCalled();
    expect(queryByTestId("text-drawing-input")).toBeNull();
  });

  it("escape closes the text input without adding anything", () => {
    const props = drawingProps();
    const { container, getByTestId, queryByTestId } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="teks"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 150 });
    fireEvent.keyDown(getByTestId("text-drawing-input"), { key: "Escape" });
    expect(props.onAdd).not.toHaveBeenCalled();
    expect(queryByTestId("text-drawing-input")).toBeNull();
  });

  it("double-click on an existing label reopens it for editing", () => {
    const label = { id: "x1", kind: "text" as const, a: { timeMs: mockCandles[2].time_msc, price: 105 }, text: "old" };
    const props = drawingProps({ items: [label] });
    const { container, getByTestId } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    // the label is projected at logical index 2 → x=2 under the identity stub
    fireEvent.doubleClick(pane, { clientX: 6, clientY: 150 });
    const input = getByTestId("text-drawing-input") as HTMLInputElement;
    expect(input.value).toBe("old");
    fireEvent.change(input, { target: { value: "new" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onUpdate).toHaveBeenCalledWith({ ...label, text: "new" });
  });
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/CandleChart.test.tsx -t "text"`
Expected: FAIL — `Unable to find an element by: [data-testid="text-drawing-input"]`.

- [x] **Step 3: Write minimal implementation**

Create `frontend/src/components/TextDrawingInput.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { MAX_TEXT_LEN } from "../lib/drawings";

// Inline label editor, positioned at the anchor's pixel. Enter commits, Escape
// cancels, and a blank label is discarded rather than stored — an empty note is
// an accident, not an annotation.
export default function TextDrawingInput({
  x, y, initial, onCommit, onCancel,
}: {
  x: number;
  y: number;
  initial: string;
  onCommit: (text: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);

  return (
    <input
      ref={ref}
      data-testid="text-drawing-input"
      className="glass absolute z-30 px-1 py-0.5 text-[11px] bg-bg text-ink"
      style={{ left: x, top: y - 18, width: 160 }}
      value={value}
      maxLength={MAX_TEXT_LEN}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); onCommit(value.trim()); }
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      }}
      onBlur={() => onCommit(value.trim())}
    />
  );
}
```

In `frontend/src/components/CandleChart.tsx`:

1. Import it and add the editor state next to `tool`:

```ts
import TextDrawingInput from "./TextDrawingInput";
```

```ts
  // Open text editor: either a fresh label being placed, or an existing one
  // being re-edited (`id` set).
  const [textEdit, setTextEdit] = useState<
    { id: string | null; anchor: Anchor; px: { x: number; y: number }; initial: string } | null
  >(null);
```

2. The `text` tool must not go through the drag path. Pass a `tool` to the gesture hook that hides it, and open the editor from the pane's own click instead — add to the `useDrawingGesture` call:

```ts
    tool: tool === "text" ? "cursor" : tool,
```

3. Add an effect that opens the editor for a `text`-tool press and for a double-click on an existing label. Place it after the existing gesture effects:

```ts
  // Text tool: a single press places the anchor and opens the inline editor —
  // there is nothing to drag, so this never enters the draw reducer. A
  // double-click on an existing label reopens it. Both paths are capture-phase
  // so they land before the measure gesture, which needs a HOLD and therefore
  // never competes for a plain click.
  useEffect(() => {
    const node = el.current;
    if (!node || !props.drawings?.editable) return;

    const rel = (e: MouseEvent) => {
      const r = node.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      if (toolRef.current !== "text") return;
      const { x, y } = rel(e);
      const at = toAnchor(x, y);
      if (!at) return;
      setTextEdit({ id: null, anchor: at, px: { x, y }, initial: "" });
      setTool("cursor");
      e.preventDefault();
      e.stopPropagation();
    };

    const onDouble = (e: MouseEvent) => {
      const { x, y } = rel(e);
      const hit = hitTest(projectedRef.current, { x, y });
      if (!hit) return;
      const target = cbs.current.drawings?.items.find((d) => d.id === hit.id);
      if (!target || target.kind !== "text") return;
      setTextEdit({ id: target.id, anchor: target.a, px: { x, y }, initial: target.text });
      e.preventDefault();
      e.stopPropagation();
    };

    node.addEventListener("pointerdown", onDown, true);
    node.addEventListener("dblclick", onDouble, true);
    return () => {
      node.removeEventListener("pointerdown", onDown, true);
      node.removeEventListener("dblclick", onDouble, true);
    };
  }, [props.drawings?.editable, toAnchor]);
```

This needs two refs so the effect does not re-subscribe on every render — add them next to the other refs:

```ts
  const toolRef = useRef<Tool>("cursor");
  toolRef.current = tool;
  const projectedRef = useRef<Projected[]>([]);
```

and set `projectedRef.current = projectedDrawings;` immediately after `projectedDrawings` is computed. Import `hitTest` alongside the other `drawings` imports.

3. Render the editor in the JSX, after the palette:

```tsx
      {drawings?.editable && textEdit && (
        <TextDrawingInput
          x={textEdit.px.x}
          y={textEdit.px.y}
          initial={textEdit.initial}
          onCommit={(text) => {
            const edit = textEdit;
            setTextEdit(null);
            if (text.length === 0) return;      // blank label = discarded
            if (edit.id === null) {
              drawings.onAdd({ id: crypto.randomUUID(), kind: "text", a: edit.anchor, text });
            } else {
              const target = drawings.items.find((d) => d.id === edit.id);
              if (target && target.kind === "text") drawings.onUpdate({ ...target, text });
            }
          }}
          onCancel={() => setTextEdit(null)}
        />
      )}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/CandleChart.test.tsx`
Expected: PASS (all drawing-gesture tests including the 4 new text ones).

- [x] **Step 5: Commit**

```bash
git add frontend/src/components/TextDrawingInput.tsx frontend/src/components/CandleChart.tsx frontend/src/components/CandleChart.test.tsx
git commit -m "feat(drawings): inline text-label editor with blank-discard"
```

---

### Task 11: Page wiring — `Chart.tsx` editable, `TradeView.tsx` read-only

**Files:**
- Modify: `frontend/src/pages/Chart.tsx`
- Modify: `frontend/src/pages/TradeView.tsx`
- Test: `frontend/src/pages/Chart.test.tsx` (append), `frontend/src/pages/TradeView.test.tsx` (append)

**Interfaces:**
- Consumes: `useDrawings` (Task 6), the `drawings` prop (Task 9).
- Produces: no new exports. `Lab.tsx` is deliberately left untouched.

- [x] **Step 1: Write the failing test**

Both page tests already mock `CandleChart` out and capture its props, so these
assert on the captured props and on the request URL — not on rendered pixels.

Append to `frontend/src/pages/Chart.test.tsx`, **inside the existing `describe`
block that defines `renderChartPage`** (the one holding `FAKE_SESSION`, whose
`id` is `1`). `waitFor`, `screen` and `mockCandleChart` are already in scope
there.

```tsx
  it("hands the chart editable drawings keyed to the live symbol outside replay", async () => {
    const { fetchMock } = renderChartPage({ replayOpen: false });
    await screen.findByTestId("candle-chart");

    const calls = mockCandleChart.mock.calls;
    expect(calls[calls.length - 1][0].drawings.editable).toBe(true);

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]: [string]) => u);
      expect(urls).toContain("/api/drawings?symbol=XAUUSDc");
    });
    const urls = fetchMock.mock.calls.map(([u]: [string]) => u);
    expect(urls.some((u) => u.startsWith("/api/drawings?") && u.includes("session_id="))).toBe(false);
  });

  it("scopes drawings to the replay session while replaying", async () => {
    // Live annotations were made knowing what happened next; training must not
    // see them, and the session-scoped key is what enforces that.
    const { fetchMock } = renderChartPage({ replayOpen: true });
    await screen.findByTestId("candle-chart");
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]: [string]) => u);
      expect(urls.some((u) => u.startsWith("/api/drawings?") && u.includes("session_id=1"))).toBe(true);
    });
  });
```

Append to `frontend/src/pages/TradeView.test.tsx` (that file already exports
`candleChartMock` and renders through `MemoryRouter` in exactly this shape):

```tsx
it("renders drawings read-only on the trade viewer", async () => {
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes><Route path="/trades/:id/view" element={<TradeView />} /></Routes>
  </MemoryRouter>);
  await screen.findByTestId("candle-chart");
  const calls = candleChartMock.mock.calls;
  const props = calls[calls.length - 1][0];
  expect(props.drawings.editable).toBe(false);
  expect(Array.isArray(props.drawings.items)).toBe(true);
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Chart.test.tsx src/pages/TradeView.test.tsx`
Expected: FAIL — no `/api/drawings` request is ever made.

- [x] **Step 3: Write minimal implementation**

In `frontend/src/pages/Chart.tsx`:

1. Import the hook:

```ts
import { useDrawings } from "../hooks/useDrawings";
```

2. Add the hook call after the `replay` / `replayPrefs` declarations (it needs `replay.session`):

```ts
  // Replay drawings live under their own session key: a live annotation was
  // made knowing what happened next, so showing it during training would leak
  // the answer. Passing null outside replay selects the per-symbol live key.
  const drawingSession = replayOpen ? replay.session?.id ?? null : null;
  const drawings = useDrawings(symbol, drawingSession, true);
```

> **SUPERSEDED (fix wave, 2026-08-10 — IMPORTANT 1):** the `useDrawings(symbol,
> drawingSession, true)` line above is a plan defect, not an implementation
> slip — it was built exactly as prescribed. `replay.start(cfg)` is an async
> POST; `setReplayOpen(true)` lands synchronously well before the response
> assigns `replay.session`. For that whole window `replayOpen` is `true` but
> `replay.session` is still `null`, so `replay.session?.id ?? null` falls back
> to the LIVE per-symbol key — rendering it editable on the replay chart, with
> an edit in that window persisting to the live key. That is exactly the leak
> the comment above says this key split exists to prevent. The shipped fix
> gates the hook itself on a `drawingsReady = !replayOpen || replay.session !=
> null` flag (see `frontend/src/pages/Chart.tsx`), used both as the third
> argument to `useDrawings` and as `drawingsProp.editable`, so the palette
> cannot even render during that window. See PENDING HUMAN item 6 below —
> it must be re-run against this fix.

3. Pass it to `CandleChart`, after the `hideDate` prop:

```tsx
              drawings={{
                items: drawings.items,
                editable: true,
                onAdd: drawings.add,
                onUpdate: drawings.update,
                onDelete: drawings.remove,
                onClearAll: drawings.clear,
              }}
```

Wrap that object in `useMemo` keyed on `[drawings.items, drawings.add, drawings.update, drawings.remove, drawings.clear]` for the same reason `plannedOrder` and `draggableReplay` are memoised above — a fresh object every render would churn the chart's effects on every hover:

```ts
  const drawingsProp = useMemo(() => ({
    items: drawings.items,
    editable: true,
    onAdd: drawings.add,
    onUpdate: drawings.update,
    onDelete: drawings.remove,
    onClearAll: drawings.clear,
  }), [drawings.items, drawings.add, drawings.update, drawings.remove, drawings.clear]);
```

and pass `drawings={drawingsProp}`.

In `frontend/src/pages/TradeView.tsx`:

1. Import the hook:

```ts
import { useDrawings } from "../hooks/useDrawings";
```

2. Call it with the trade's symbol (the component already has `t.symbol`), and memoise the prop:

```ts
  const drawings = useDrawings(t?.symbol ?? "", null, !!t);
  const drawingsProp = useMemo(() => ({
    items: drawings.items,
    editable: false,                 // the viewer inspects; it does not annotate
    onAdd: () => {}, onUpdate: () => {}, onDelete: () => {}, onClearAll: () => {},
  }), [drawings.items]);
```

Place this next to the component's other hooks — before any early `return`, so hook order stays stable across renders.

3. Add `drawings={drawingsProp}` to the `<CandleChart ... />` call.

- [x] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Chart.test.tsx src/pages/TradeView.test.tsx`
Expected: PASS.

- [x] **Step 5: Full verification**

Run every gate and paste the real output (CLAUDE.md "Definition of done"):

```bash
cd frontend && npx vitest run && npx tsc -b --noEmit && npm run build
cd .. && uv run pytest -q && uv run journal rebuild
```

Expected: all vitest suites pass, 0 type errors, build succeeds, all pytest pass, `journal rebuild` succeeds. `rebuild` is the check that drawings in `app_prefs` survive a full trades rebuild untouched.

- [x] **Step 6: Commit**

```bash
git add frontend/src/pages/Chart.tsx frontend/src/pages/Chart.test.tsx frontend/src/pages/TradeView.tsx frontend/src/pages/TradeView.test.tsx
git commit -m "feat(drawings): wire editable drawings into /chart and read-only into the trade viewer"
```

---

## PENDING HUMAN

**Status 2026-08-12: 7 of 8 run in a real browser and PASSED; item 4 is the
only one left.** The pass was driven through Chrome against the running
`journal serve` + `journal live` on `main` (`3afbb03`), on `XAUUSDc`, and every
result was cross-checked against the persisted blob (`GET /api/drawings`,
`app_prefs`) rather than eyeballed. Test drawings were cleared afterwards via
the palette's own clear button; no order was placed at any point.

1. **PASS** — trendline, hline, rectangle and a text note drawn on `/chart`; all
   four returned in the same places after a full page reload.
2. **PASS** — M15 → H1 → M15: nothing disappeared, each object landed on the bar
   containing its anchor (compressed on H1, identical pixels back on M15).
3. **PASS** — endpoint drag (trend `b` 4395 → 4403.32), body drag (rect both
   prices +13.95, times unchanged), `Delete` removed the selected rect (blob
   4 → 3 items). Note for whoever runs this next: a rect is **hollow**, so
   "drag the body" means grabbing an *edge* away from the corners — a press
   inside the fill pans the chart, which is correct but reads as a dead drag.
4. **NOT RUN — needs a real open position.** There was none open, and opening
   one is a live trade, so it was left alone. What *was* verified is the part
   the item is actually protecting: with a **planned** SL typed to exactly the
   hline's price (4404.95), dragging that line moved SL to 4411.89 and left the
   drawing untouched and unselected — the hit-test precedence holds. The
   untested remainder is the live modify/commit path only.
5. **PASS** — measure gesture still works over an area with drawings on it
   (Δ +0.37%, 20 bars, frozen until Esc). Driven by dispatching the real
   pointer sequence in-page: two `pointerdown`s must land inside
   `DBLCLICK_MS` (350 ms), which no remote-automation round trip can hit.
6. **PASS, including the IMPORTANT 1 case.** Replay opened clean (no live
   drawings), a trendline drawn there went to `drawings:replay:108` with
   `drawings:XAUUSD` untouched, and exiting brought the live chart back with
   exactly its own 3 objects. For the pending window: `window.fetch` was
   patched to delay `/api/replay/start` by 6 s (a real round trip, just slow),
   and a trendline drawn *inside* that window was discarded entirely — no
   `drawings:replay:109` key, live key still 3 items.
7. **PASS** — `/trades/:id/view` renders drawings (an hline placed in the
   trade's own price range showed up) with no palette. Watch the id: the route
   takes a **`position_id`**, not `trades.id`; a wrong id leaves the page
   sitting on "Memuat…" forever instead of erroring.
8. **PASS** — `/lab` shows no palette and renders no drawings, including an
   hline whose price was inside the lab chart's visible range.
