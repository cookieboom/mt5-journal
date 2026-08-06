import { describe, it, expect } from "vitest";
import { bestModel, formatAge, regimeColor, DEFAULT_TRAIN_FORM, LAB_FEATURES } from "./lab";
import type { LabModel } from "./types";

const model = (over: Partial<LabModel>): LabModel =>
  ({
    id: 1, created_ms: 0, symbol: "XAUUSDc", timeframe: "H1", stage: "timing",
    regime: null, kind: "lgbm", pooled: false, active: false, n_rows: 100,
    train_from_ms: 0, train_to_ms: 1, config: {}, metrics: { n: 100 },
    ...over,
  } as LabModel);

describe("regimeColor", () => {
  it("gives each regime its own colour and never returns empty", () => {
    const seen = new Set(["trend_up", "trend_down", "range"].map(regimeColor));
    expect(seen.size).toBe(3);
    expect(regimeColor("nonsense")).not.toBe("");
  });
});

describe("formatAge", () => {
  it("reads in days once past a day", () => {
    expect(formatAge(3 * 86_400_000)).toBe("3d ago");
  });
  it("reads in hours under a day", () => {
    expect(formatAge(5 * 3_600_000)).toBe("5h ago");
  });
  it("says just now under an hour", () => {
    expect(formatAge(60_000)).toBe("just now");
  });
  it("handles a missing age", () => {
    expect(formatAge(null)).toBe("never trained");
  });
});

describe("bestModel", () => {
  it("prefers the active model", () => {
    const models = [
      model({ id: 1, created_ms: 100, active: false }),
      model({ id: 2, created_ms: 50, active: true }),
    ];
    expect(bestModel(models, "timing")?.id).toBe(2);
  });
  it("falls back to the newest when none is active", () => {
    const models = [
      model({ id: 1, created_ms: 100 }),
      model({ id: 2, created_ms: 50 }),
    ];
    expect(bestModel(models, "timing")?.id).toBe(1);
  });
  it("ignores other stages", () => {
    expect(bestModel([model({ stage: "regime", active: true })], "timing")).toBeNull();
  });
});

describe("defaults", () => {
  it("starts with every feature on", () => {
    expect(DEFAULT_TRAIN_FORM.features).toEqual([...LAB_FEATURES]);
  });
  it("carries the spec's default label parameters", () => {
    expect(DEFAULT_TRAIN_FORM.n_bars).toBe(24);
    expect(DEFAULT_TRAIN_FORM.k_atr).toBe(1);
    expect(DEFAULT_TRAIN_FORM.rr).toBe(2);
    expect(DEFAULT_TRAIN_FORM.er_threshold).toBe(0.35);
  });
});
