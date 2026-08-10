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
