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
