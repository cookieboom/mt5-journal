import { describe, it, expect } from "vitest";
import { optNum } from "./parse";

describe("optNum", () => {
  it("blank/whitespace-only means leave unchanged (null)", () => {
    expect(optNum("")).toBe(null);
    expect(optNum("   ")).toBe(null);
  });

  it("a finite number (including 0) parses to itself", () => {
    expect(optNum("0")).toBe(0);
    expect(optNum("1.5")).toBe(1.5);
    expect(optNum("-2000.5")).toBe(-2000.5);
  });

  it("invalid or non-finite entries are the NaN sentinel that blocks submit", () => {
    expect(Number.isNaN(optNum("abc"))).toBe(true);
    expect(Number.isNaN(optNum("40o0"))).toBe(true);
    expect(Number.isFinite(optNum("1e999"))).toBe(false);
  });
});
