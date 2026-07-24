import { describe, it, expect } from "vitest";
import { money, rmult, pct, wib, isGated, price, dur } from "./format";

describe("format", () => {
  it("money carries currency, never a bare dollar, null is n/a not 0", () => {
    expect(money(1250, "USC")).toBe("1,250.00 USC");
    expect(money(-3.75, "USC", { sign: true })).toBe("-3.75 USC");
    expect(money(9.92, "USC")).not.toContain("$");
    expect(money(null, "USC")).toBe("n/a");
    expect(money(0, "USC")).toBe("0.00 USC");
  });

  it("rmult and pct", () => {
    expect(rmult(1.35)).toBe("1.35R");
    expect(rmult(null)).toBe("n/a");
    expect(pct(0.347)).toBe("34.7%");
    expect(pct(null)).toBe("n/a");
  });

  it("wib converts server ms to UTC+7 and shows — for null", () => {
    // 2026-01-15 03:00 UTC (offset 0) → 10:00 WIB
    const ms = Date.UTC(2026, 0, 15, 3, 0) ;
    expect(wib(ms, 0)).toBe("2026-01-15 10:00 WIB");
    expect(wib(null)).toBe("—");
  });

  it("isGated matches the §9 rule", () => {
    expect(isGated(5, null)).toBe(true);
    expect(isGated(25, null)).toBe(false);
    expect(isGated(5, 1.2)).toBe(false);
  });

  it("price: null is unknown (never 0), real numbers show compactly", () => {
    expect(price(null)).toBe("unknown");
    expect(price(4010)).toBe("4010");
    expect(price(4010.5)).toBe("4010.5");
    expect(price(0)).toBe("0");
    expect(price(4010.123)).toBe("4010.123");   // full precision, not %g's 4010.12
    expect(price(100000.5)).toBe("100000.5");   // not %g's 100000
  });

  it("dur: mirrors web/format.py (null=—, s/m/h ladder with zero-pad)", () => {
    expect(dur(null)).toBe("—");
    expect(dur(45)).toBe("45s");
    expect(dur(720)).toBe("12m");        // 12m exactly, seconds 0 → no s
    expect(dur(185)).toBe("3m05s");      // seconds zero-padded
    expect(dur(7620)).toBe("2h07m");     // minutes zero-padded
  });
});
