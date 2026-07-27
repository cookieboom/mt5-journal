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

import {
  measureReducer, isDoubleClickHold, IDLE, DBLCLICK_MS, DBLCLICK_PX,
  type MeasureState,
} from "./measure";

const pt = (price: number): Point => ({ price, barTimeMs: price * 1000, logical: price });

describe("measureReducer", () => {
  it("start from idle → measuring with anchor, cursor seeded to anchor", () => {
    const s = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    expect(s.phase).toBe("measuring");
    if (s.phase !== "idle") { expect(s.anchor).toEqual(pt(1)); expect(s.cursor).toEqual(pt(1)); }
  });

  it("move while measuring updates cursor, keeps anchor", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "move", cursor: pt(2) });
    if (s.phase === "measuring") { expect(s.cursor).toEqual(pt(2)); expect(s.anchor).toEqual(pt(1)); }
    else throw new Error("expected measuring");
  });

  it("release while measuring → frozen, keeping anchor & cursor", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "move", cursor: pt(3) });
    s = measureReducer(s, { t: "release" });
    expect(s.phase).toBe("frozen");
    if (s.phase === "frozen") { expect(s.anchor).toEqual(pt(1)); expect(s.cursor).toEqual(pt(3)); }
  });

  it("start from frozen replaces the old measurement", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "release" });
    s = measureReducer(s, { t: "start", anchor: pt(9) });
    expect(s.phase).toBe("measuring");
    if (s.phase !== "idle") expect(s.anchor).toEqual(pt(9));
  });

  it("clear always returns idle", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    expect(measureReducer(s, { t: "clear" })).toEqual(IDLE);
    s = measureReducer(s, { t: "release" });
    expect(measureReducer(s, { t: "clear" })).toEqual(IDLE);
    expect(measureReducer(IDLE, { t: "clear" })).toEqual(IDLE);
  });

  it("move/release are no-ops in idle", () => {
    expect(measureReducer(IDLE, { t: "move", cursor: pt(2) })).toEqual(IDLE);
    expect(measureReducer(IDLE, { t: "release" })).toEqual(IDLE);
  });

  it("move is a no-op while frozen (only a new start changes it)", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "release" });
    const before = s;
    expect(measureReducer(s, { t: "move", cursor: pt(5) })).toEqual(before);
  });
});

describe("isDoubleClickHold", () => {
  it("true when second press is within time and distance", () => {
    expect(isDoubleClickHold(1000, 100, 100, 1000 + DBLCLICK_MS - 1, 100 + DBLCLICK_PX - 1, 100)).toBe(true);
  });
  it("false when too slow", () => {
    expect(isDoubleClickHold(1000, 100, 100, 1000 + DBLCLICK_MS + 1, 100, 100)).toBe(false);
  });
  it("false when too far", () => {
    expect(isDoubleClickHold(1000, 100, 100, 1010, 100 + DBLCLICK_PX + 1, 100)).toBe(false);
  });
  it("false when there was no previous up (null)", () => {
    expect(isDoubleClickHold(null, 0, 0, 1000, 0, 0)).toBe(false);
  });
});
