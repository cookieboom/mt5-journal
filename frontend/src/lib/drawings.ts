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
