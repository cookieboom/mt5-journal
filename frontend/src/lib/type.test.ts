/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";
import { typeScale, font } from "./type";

// Vite's own source loader rather than node:fs — this tsconfig has no node
// types, and a scan of the tree is not worth a dependency.
const SOURCES = import.meta.glob("../**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("type scale", () => {
  it("keeps 10px as the floor", () => {
    const px = Object.values(typeScale).map(([size]) => parseFloat(size));
    expect(Math.min(...px)).toBe(10);
  });

  it("spells a role as a font shorthand for SVG and inline styles", () => {
    expect(font("meta")).toBe("11px/1.4 ui-monospace, monospace");
    expect(font("label", "Inter, sans-serif")).toBe("10px/1.2 Inter, sans-serif");
  });

  // The whole point of replacing Tailwind's fontSize theme rather than
  // extending it: a size that is not a role should be unspellable. This test is
  // what makes that true in review as well as at build time — `text-[13px]`
  // still *compiles*, it just quietly leaves the system, which is exactly how
  // 18 sizes accumulated for 6 roles the first time.
  it("has no component spelling a size outside the roles", () => {
    const offenders: string[] = [];
    for (const [file, text] of Object.entries(SOURCES)) {
      for (const m of text.matchAll(
        /\btext-(?:\[[\d.]+px\]|xs|sm|base|lg|xl|2xl|3xl)\b/g,
      )) {
        offenders.push(`${file}: ${m[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("scanned the tree it claims to scan", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(40);
  });
});
