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

    // Endpoints require a tighter tolerance (half of body threshold) so that
    // a click on a line body is not stolen by a distant endpoint. This prevents
    // "clicking a trend line at x=5 when endpoint-a is at x=0 from grabbing
    // endpoint-a at distance 5 instead of the body at distance 2".
    const endpointThreshold = threshold / 2;

    // hline has no endpoints to grab: its whole span is body.
    if (d.kind !== "hline" && d.kind !== "rect") {
      if (Math.hypot(p.x - a.x, p.y - a.y) <= endpointThreshold) return { id: d.id, handle: "a" };
      if (Math.hypot(p.x - b.x, p.y - b.y) <= endpointThreshold) return { id: d.id, handle: "b" };
    }

    if (d.kind === "rect") {
      const c1 = { x: a.x, y: b.y }, c2 = { x: b.x, y: a.y };
      const edges: [PixelPoint, PixelPoint][] = [[a, c1], [c1, b], [b, c2], [c2, a]];
      if (edges.some(([e0, e1]) => distToSegment(p, e0, e1) <= threshold)) {
        // Rect is hollow: only its edges are hittable, not the interior.
        // A point in the interior passes the edge-distance check if it's close
        // to an edge, but we exclude it if it's strictly inside the rect bounds.
        const minX = Math.min(a.x, b.x);
        const maxX = Math.max(a.x, b.x);
        const minY = Math.min(a.y, b.y);
        const maxY = Math.max(a.y, b.y);
        // Interior: strictly inside both x and y bounds.
        const isInterior = p.x > minX && p.x < maxX && p.y > minY && p.y < maxY;
        if (!isInterior) return { id: d.id, handle: "body" };
      }
      continue;
    }

    if (distToSegment(p, a, b) <= threshold) return { id: d.id, handle: "body" };
  }
  return null;
}
