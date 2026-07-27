import { describe, it, expect } from "vitest";
import { classifyGaps, type Segment } from "./coverage";
import type { Candle } from "./types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 1 } as unknown as Candle);
const kinds = (s: Segment[]) => s.map((x) => x.kind);

describe("classifyGaps", () => {
  it("all covered when bars are contiguous and nothing missing", () => {
    const bars = [bar(0), bar(300_000), bar(600_000)];
    const segs = classifyGaps(bars, [], [0, 600_000], "M5");
    expect(kinds(segs)).toEqual(["covered"]);
  });
  it("marks an uncovered range as unfetched", () => {
    const bars = [bar(0)];
    const segs = classifyGaps(bars, [[300_000, 600_000]], [0, 600_000], "M5");
    expect(segs.some((s) => s.kind === "unfetched")).toBe(true);
  });
  it("marks a covered-but-empty gap as closed (market shut)", () => {
    // bars at 0 and 600_000, gap at 300_000 is inside coverage (not in missing)
    const bars = [bar(0), bar(600_000)];
    const segs = classifyGaps(bars, [], [0, 600_000], "M5");
    expect(segs.some((s) => s.kind === "closed")).toBe(true);
  });
});
