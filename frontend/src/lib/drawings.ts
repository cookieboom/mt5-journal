// Pure model for hand-drawn chart annotations. No React, no chart API here —
// the same discipline as measure.ts. Time is epoch ms (broker server = UTC,
// CLAUDE.md rule 3); prices are REAL and only ever compared with tolerance.
import { palette } from "./theme";

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
  trend: palette["mark-sky"],
  hline: palette.violet,
  rect: palette["mark-amber"],
  text: palette["mark-chalk"],
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

import { HIT_THRESHOLD_PX } from "./sltpDrag";

export interface PixelPoint { x: number; y: number }

// "c1"/"c2" are the rect's other two corners — (a.x, b.y) and (b.x, a.y). They
// are not stored anchors, so dragging one moves ONE coordinate of each anchor.
export type Handle = "a" | "b" | "c1" | "c2" | "body";
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
  tfMs: number;                                    // bar size, for the empty space right of the last bar
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

// Logical index (fractional, from coordinateToLogical) → the time it stands for.
// Inside the window that is the bar under the pointer. To the RIGHT of the last
// bar there is no bar to name, so the index counts whole timeframes forward
// from the last one — the same step the time axis itself draws with. To the
// LEFT it clamps: earlier bars exist, they are merely not loaded, so inventing
// times there would misplace an anchor the moment history arrives.
export function timeAtLogical(
  candles: { time_msc: number }[],
  logical: number,
  tfMs: number,
): number {
  if (candles.length === 0) return 0;
  const last = candles.length - 1;
  const i = Math.round(logical);
  if (i <= 0) return candles[0].time_msc;
  if (i <= last) return candles[i].time_msc;
  if (tfMs <= 0) return candles[last].time_msc;
  return candles[last].time_msc + (i - last) * tfMs;
}

// Anchor time → pixel x. Deliberately NOT timeToCoordinate(): that returns null
// for any timestamp which is not exactly a bar time on the CURRENT series, so a
// drawing made on M15 would silently vanish when the same chart is viewed on
// H1. Snapping to the containing bar is the correct reading of "this level, at
// this moment", and is what keeps drawings shared across timeframes.
// Past the last bar the same extrapolation as timeAtLogical runs in reverse, so
// an anchor placed in the empty right-hand space stays where it was put.
export function anchorToX(
  timeMs: number,
  candles: { time_msc: number }[],
  logicalToX: (index: number) => number | null,
  tfMs: number,
): number | null {
  const i = barIndexAt(candles, timeMs);
  if (i < 0) return null;
  const last = candles.length - 1;
  if (i === last && tfMs > 0) {
    const ahead = (timeMs - candles[last].time_msc) / tfMs;
    if (ahead > 0) return logicalToX(last + ahead);
  }
  return logicalToX(i);
}

export function projectDrawing(d: Drawing, ctx: ProjectCtx): Projected {
  const pt = (anchor: Anchor): PixelPoint | null => {
    const x = anchorToX(anchor.timeMs, ctx.candles, ctx.logicalToX, ctx.tfMs);
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
// An endpoint handle inside the grab radius wins OUTRIGHT, before any body or
// edge test. It cannot be decided by "closest feature wins": the body line runs
// through its own endpoints and a rect edge through its own corners, so the
// body distance is never larger than the endpoint distance — comparing them
// made every near-corner press a whole-object move and left the handles
// reachable only from beyond the segment.
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

    // hline has no movable endpoints — its a/b are the pane edges, not anchors.
    if (d.kind !== "hline") {
      const distA = Math.hypot(p.x - a.x, p.y - a.y);
      const distB = Math.hypot(p.x - b.x, p.y - b.y);
      if (Math.min(distA, distB) <= threshold) {
        return { id: d.id, handle: distA <= distB ? "a" : "b" };
      }
    }

    if (d.kind === "rect") {
      const c1 = { x: a.x, y: b.y }, c2 = { x: b.x, y: a.y };
      const corners: [Handle, PixelPoint][] = [["c1", c1], ["c2", c2]];
      for (const [handle, c] of corners) {
        if (Math.hypot(p.x - c.x, p.y - c.y) <= threshold) return { id: d.id, handle };
      }
      const edges: [PixelPoint, PixelPoint][] = [[a, c1], [c1, b], [b, c2], [c2, a]];
      const minEdgeDist = Math.min(...edges.map(([e0, e1]) => distToSegment(p, e0, e1)));

      if (minEdgeDist <= threshold) {
        // Edge hit, but verify not in interior (hollow rect).
        const minX = Math.min(a.x, b.x);
        const maxX = Math.max(a.x, b.x);
        const minY = Math.min(a.y, b.y);
        const maxY = Math.max(a.y, b.y);
        const isInterior = p.x > minX && p.x < maxX && p.y > minY && p.y < maxY;
        if (!isInterior) return { id: d.id, handle: "body" };
      }
      continue;
    }

    if (d.kind !== "hline") {
      if (distToSegment(p, a, b) <= threshold) return { id: d.id, handle: "body" };
      continue;
    }

    // hline: body only.
    if (distToSegment(p, a, b) <= threshold) return { id: d.id, handle: "body" };
  }
  return null;
}

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
  // Only a rect has the mixed corners; anything else falls through to the body
  // shift below rather than half-moving an anchor it never showed a handle for.
  if (d.kind === "rect") {
    if (handle === "c1") {
      return { ...d, a: { ...d.a, timeMs: to.timeMs }, b: { ...d.b, price: to.price } };
    }
    if (handle === "c2") {
      return { ...d, a: { ...d.a, price: to.price }, b: { ...d.b, timeMs: to.timeMs } };
    }
  }
  return { ...d, a: shift(d.a, from, to), b: shift(d.b, from, to) };
}
