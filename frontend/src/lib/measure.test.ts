import { describe, it, expect } from "vitest";
import { computeMetrics, fmtSpan, type Point } from "./measure";

const P = (price: number, barTimeMs: number, logical: number): Point => ({ price, barTimeMs, logical });

describe("computeMetrics", () => {
  it("positive move: dPrice, pct, bars, dTimeMs, up=true", () => {
    const anchor = P(2000, 1_000_000, 10);
    const cursor = P(2012.34, 1_000_000 + 8100_000, 19); // +8100s = 2h15m, 9 bars
    const m = computeMetrics(anchor, cursor);
    expect(m.dPrice).toBeCloseTo(12.34, 9);
    expect(m.pct).toBeCloseTo(0.617, 3); // 12.34/2000*100
    expect(m.bars).toBe(9);
    expect(m.dTimeMs).toBe(8100_000);
    expect(m.up).toBe(true);
  });

  it("negative move (cursor below/before anchor): dPrice<0, up=false, bars & dTimeMs are absolute", () => {
    const anchor = P(2000, 2_000_000, 20);
    const cursor = P(1990, 1_000_000, 12);
    const m = computeMetrics(anchor, cursor);
    expect(m.dPrice).toBeCloseTo(-10, 9);
    expect(m.up).toBe(false);
    expect(m.bars).toBe(8);
    expect(m.dTimeMs).toBe(1_000_000);
  });

  it("zero anchor price → pct is null (no divide-by-zero)", () => {
    const m = computeMetrics(P(0, 0, 0), P(5, 100, 1));
    expect(m.pct).toBeNull();
    expect(m.dPrice).toBeCloseTo(5, 9);
  });

  it("bars rounds a fractional logical difference", () => {
    const m = computeMetrics(P(1, 0, 10.2), P(1, 0, 13.9));
    expect(m.bars).toBe(4); // |13.9-10.2| = 3.7 → 4
  });
});

describe("fmtSpan", () => {
  it("minutes only", () => { expect(fmtSpan(45 * 60_000)).toBe("45m"); });
  it("hours + minutes", () => { expect(fmtSpan((2 * 3600 + 15 * 60) * 1000)).toBe("2h 15m"); });
  it("days + hours", () => { expect(fmtSpan((3 * 86400 + 4 * 3600) * 1000)).toBe("3d 4h"); });
  it("under a minute → 0m", () => { expect(fmtSpan(5000)).toBe("0m"); });
});
