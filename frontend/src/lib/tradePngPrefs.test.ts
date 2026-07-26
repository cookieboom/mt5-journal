import { describe, it, expect } from "vitest";
import { normalizeTradePng, DEFAULT_TRADE_PNG, toApi, fromApi } from "./tradePngPrefs";

describe("tradePngPrefs", () => {
  it("defaults on null/garbage", () => {
    expect(normalizeTradePng(null)).toEqual(DEFAULT_TRADE_PNG);
    expect(normalizeTradePng({ theme: "bogus" }).theme).toBe("charles");
  });
  it("clamps padBars and validates tf", () => {
    expect(normalizeTradePng({ padBars: 999 }).padBars).toBe(120);
    expect(normalizeTradePng({ padBars: 1 }).padBars).toBe(5);
    expect(normalizeTradePng({ tfOverride: "ZZ" }).tfOverride).toBeNull();
    expect(normalizeTradePng({ tfOverride: "M5" }).tfOverride).toBe("M5");
  });
  it("round-trips through the snake_case API shape", () => {
    const s = { ...DEFAULT_TRADE_PNG, theme: "nightclouds" as const, padBars: 40 };
    expect(fromApi(toApi(s))).toEqual(s);
    expect(toApi(s)).toMatchObject({ theme: "nightclouds", pad_bars: 40 });
  });
});
