import { describe, it, expect } from "vitest";
import { clipToCursor, outcomeCounts, replayLines, summarize, unrealizedR, msPerStep, type TrainingPosition } from "./replay";
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

describe("outcomeCounts", () => {
  it("counts closed positions by exit reason, folding eod into manual", () => {
    const c = outcomeCounts([
      pos({ status: "closed", exit_reason: "tp" }),
      pos({ status: "closed", exit_reason: "sl" }),
      pos({ status: "closed", exit_reason: "manual" }),
      pos({ status: "closed", exit_reason: "eod" }),
      pos({ status: "open" }),
      pos({ status: "pending" }),
    ]);
    expect(c).toEqual({ closed: 4, sl: 1, tp: 1, manual: 2 });
  });
  it("is all-zero with no positions", () => {
    expect(outcomeCounts([])).toEqual({ closed: 0, sl: 0, tp: 0, manual: 0 });
  });
});

describe("summarize", () => {
  it("aggregates closed+resolved positions across scenarios, ignoring open ones", () => {
    const s = summarize([
      pos({ status: "closed", net_profit: 100, r_multiple: 2, mae_r: 0.5, mfe_r: 2.5 }),
      pos({ status: "closed", net_profit: -50, r_multiple: -1, mae_r: 1.5, mfe_r: 0.5 }),
      pos({ status: "closed", net_profit: null }),   // unresolved — no input
      pos({ status: "open", net_profit: 999, r_multiple: 9 }),
    ]);
    expect(s.n).toBe(2);
    expect(s.win_rate).toBeCloseTo(0.5);
    expect(s.total_r).toBeCloseTo(1);
    expect(s.avg_r).toBeCloseTo(0.5);
    expect(s.avg_mae_r).toBeCloseTo(1);
    expect(s.avg_mfe_r).toBeCloseTo(1.5);
  });
  it("greys metrics with no input (rule 4) instead of reporting 0", () => {
    const s = summarize([pos({ status: "closed", net_profit: 10, r_multiple: null })]);
    expect(s.n).toBe(1);
    expect(s.avg_r).toBeNull();
    expect(s.avg_mae_r).toBeNull();
    expect(summarize([]).win_rate).toBeNull();
  });
});

describe("msPerStep", () => {
  it("maps speed to a delay, faster = smaller", () => {
    expect(msPerStep(1)).toBeGreaterThan(msPerStep(10));
  });
});
