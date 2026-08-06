import { describe, it, expect } from "vitest";
import { toBands } from "./regimeBands";
import type { LabBarScore } from "./types";

const bar = (time_msc: number, regime: string): LabBarScore =>
  ({ time_msc, regime, regime_proba: {}, p_tp_long: 0.5, p_tp_short: 0.5 } as LabBarScore);

describe("toBands", () => {
  it("collapses a run of one regime into a single band", () => {
    const bands = toBands([bar(0, "trend_up"), bar(60, "trend_up"), bar(120, "trend_up")]);
    expect(bands).toEqual([{ from: 0, to: 120, regime: "trend_up" }]);
  });

  it("splits where the regime changes", () => {
    const bands = toBands([bar(0, "range"), bar(60, "trend_up"), bar(120, "trend_up")]);
    expect(bands).toHaveLength(2);
    expect(bands[0]).toEqual({ from: 0, to: 60, regime: "range" });
    expect(bands[1]).toEqual({ from: 60, to: 120, regime: "trend_up" });
  });

  it("returns nothing for no bars", () => {
    expect(toBands([])).toEqual([]);
  });

  it("handles a single bar", () => {
    expect(toBands([bar(0, "range")])).toEqual([{ from: 0, to: 0, regime: "range" }]);
  });

  it("keeps bars in time order even if the input is not", () => {
    const bands = toBands([bar(120, "range"), bar(0, "range"), bar(60, "range")]);
    expect(bands).toEqual([{ from: 0, to: 120, regime: "range" }]);
  });
});
