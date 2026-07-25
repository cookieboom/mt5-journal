import { describe, it, expect } from "vitest";
import { clipToCursor, replayLines, unrealizedR, msPerStep, type TrainingPosition } from "./replay";
import type { Candle } from "./types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 2, l: 0.5, c: 1.5, v: 1 });

function pos(over: Partial<TrainingPosition> = {}): TrainingPosition {
  return {
    id: 1, session_id: 1, direction: "buy", volume: 0.1, decision_msc: 1000,
    entry_msc: 2000, entry_price: 4000, sl: 3998, tp: 4004, close_requested_msc: null,
    exit_msc: null, exit_price: null, exit_reason: null, status: "open",
    net_profit: null, r_multiple: null, mae: null, mfe: null, mae_r: null, mfe_r: null,
    created_at_msc: 0, ...over,
  };
}

describe("clipToCursor", () => {
  it("keeps only bars at or before the cursor", () => {
    const bars = [bar(1000), bar(2000), bar(3000)];
    expect(clipToCursor(bars, 2000).map((b) => b.time_msc)).toEqual([1000, 2000]);
  });
});

describe("replayLines", () => {
  it("draws entry/sl/tp for open positions, skipping 0 (none set)", () => {
    const lines = replayLines([pos({ sl: 0 })]);
    const titles = lines.map((l) => l.title);
    expect(titles.some((t) => t.startsWith("entry"))).toBe(true);
    expect(titles.some((t) => t.startsWith("TP"))).toBe(true);
    expect(titles.some((t) => t.startsWith("SL"))).toBe(false); // sl=0 skipped
  });
  it("ignores closed positions", () => {
    expect(replayLines([pos({ status: "closed" })])).toEqual([]);
  });
});

describe("unrealizedR", () => {
  it("is null without an SL", () => {
    expect(unrealizedR(pos({ sl: 0 }), 4002)).toBeNull();
  });
  it("computes (price-entry)/risk for a long", () => {
    expect(unrealizedR(pos({ entry_price: 4000, sl: 3998 }), 4002)).toBeCloseTo(1.0);
  });
});

describe("msPerStep", () => {
  it("maps speed to a delay, faster = smaller", () => {
    expect(msPerStep(1)).toBeGreaterThan(msPerStep(10));
  });
});
