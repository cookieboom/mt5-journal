import { describe, it, expect } from "vitest";
import { palette, shadow, tint, white, chartDark } from "./theme";

describe("theme", () => {
  it("tints a token without shifting its channels", () => {
    expect(tint(palette.neg, 0.14)).toBe("rgba(251,113,133,0.14)");
    expect(tint("#000000", 1)).toBe("rgba(0,0,0,1)");
    expect(white(0.06)).toBe("rgba(255,255,255,0.06)");
  });
  it("keeps the glow on the cyan token", () => {
    expect(shadow.glow).toContain(palette.cyan);
  });
  it("derives the dark chart theme from the palette", () => {
    expect(chartDark.up).toBe(palette.pos);
    expect(chartDark.down).toBe(palette.neg);
  });
});
