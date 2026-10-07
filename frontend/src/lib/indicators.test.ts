import { describe, expect, it } from "vitest";
import {
  mergeTail, normalizeLayout, paneAssignments, signalMarkers, toLineData, valueAt,
  plotColor, type IndicatorResult,
} from "./indicators";
import { palette } from "./theme";

function result(times: number[], values: (number | null)[], over: Partial<IndicatorResult> = {}): IndicatorResult {
  return {
    times,
    plots: [{ title: "P", color: "cyan", pane: "price", style: "line", width: 2, values }],
    hlines: [],
    signals: [],
    lookback: 3,
    warm_from_msc: times[0] ?? null,
    forming_msc: null,
    pending: false,
    ...over,
  };
}

describe("normalizeLayout", () => {
  it("defaults garbage to an empty v1 layout", () => {
    expect(normalizeLayout(null)).toEqual({ version: 1, items: [] });
    expect(normalizeLayout({ items: "x" })).toEqual({ version: 1, items: [] });
  });
  it("keeps valid items and drops broken ones and bad inputs", () => {
    const l = normalizeLayout({ version: 1, items: [
      { id: "a", script: "lib:ema", inputs: { length: 20, on: true, bad: "x" }, visible: false },
      { id: 3, script: "lib:rsi" },
      { id: "b", script: "lib:rsi" },
    ] });
    expect(l.items).toEqual([
      { id: "a", script: "lib:ema", inputs: { length: 20, on: true }, visible: false },
      { id: "b", script: "lib:rsi", inputs: {}, visible: true },
    ]);
  });
});

describe("mergeTail", () => {
  it("replaces from the tail's first time and appends", () => {
    const prev = result([1, 2, 3], [10, 20, 30], { signals: [{ time_msc: 1, side: "long" }, { time_msc: 3, side: "short" }] });
    const tail = result([3, 4], [31, 40], { signals: [{ time_msc: 4, side: "long" }], forming_msc: 4 });
    const m = mergeTail(prev, tail);
    expect(m.times).toEqual([1, 2, 3, 4]);
    expect(m.plots[0].values).toEqual([10, 20, 31, 40]);
    expect(m.signals).toEqual([{ time_msc: 1, side: "long" }, { time_msc: 4, side: "long" }]);
    expect(m.forming_msc).toBe(4);
    expect(m.warm_from_msc).toBe(1);
  });
  it("an empty tail leaves the previous result", () => {
    const prev = result([1, 2], [1, 2]);
    expect(mergeTail(prev, result([], []))).toBe(prev);
  });
  it("a tail whose plots changed shape is taken whole", () => {
    const prev = result([1, 2], [1, 2]);
    const tail = { ...result([2], [5]), plots: [] };
    expect(mergeTail(prev, tail)).toBe(tail);
  });
});

describe("valueAt", () => {
  it("finds the value at an exact bar time, null otherwise", () => {
    const r = result([100, 200, 300], [1, null, 3]);
    expect(valueAt(r, 300)).toEqual([3]);
    expect(valueAt(r, 200)).toEqual([null]);
    expect(valueAt(r, 250)).toEqual([null]);
  });
});

describe("paneAssignments", () => {
  it("price stays on pane 0; named panes get indices in layout order per instance", () => {
    const panes = paneAssignments([
      { id: "a", plots: ["price", "rsi"] },
      { id: "b", plots: ["macd", "macd"] },
      { id: "c", plots: ["rsi"] },
    ]);
    expect(panes).toEqual({ "a:price": 0, "a:rsi": 1, "b:macd": 2, "c:rsi": 3 });
  });
});

describe("toLineData", () => {
  it("emits whitespace for unknown values (a gap, never a zero)", () => {
    expect(toLineData([1000, 2000], [5, null])).toEqual([{ time: 1, value: 5 }, { time: 2 }]);
  });
});

describe("signalMarkers", () => {
  it("merges every instance's signals in time order", () => {
    const m = signalMarkers([
      [{ time_msc: 3000, side: "short" }],
      [{ time_msc: 1000, side: "long" }],
    ]);
    expect(m.map((x) => x.time)).toEqual([1, 3]);
    expect(m[0].position).toBe("belowBar");
    expect(m[1].position).toBe("aboveBar");
  });
});

describe("plotColor", () => {
  it("maps a theme token, falling back to muted", () => {
    expect(plotColor("cyan")).toBe(palette.cyan);
    expect(plotColor("#ff0000")).toBe(palette.muted);
  });
});
