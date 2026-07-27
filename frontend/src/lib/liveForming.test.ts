import { describe, it, expect } from "vitest";
import { mergeForming } from "./candles";
import type { Candle } from "./types";

const bar = (t: number, c: number): Candle =>
  ({ time_msc: t, o: 1, h: 2, l: 0.5, c, v: 1 } as unknown as Candle);

describe("mergeForming", () => {
  it("returns candles unchanged when forming is null", () => {
    const cs = [bar(100, 1)];
    expect(mergeForming(cs, null)).toEqual(cs);
  });
  it("replaces the last bar when time_msc matches", () => {
    const out = mergeForming([bar(100, 1), bar(200, 2)], bar(200, 9));
    expect(out).toHaveLength(2);
    expect(out[1].c).toBe(9);
  });
  it("appends when forming is newer", () => {
    const out = mergeForming([bar(100, 1)], bar(200, 2));
    expect(out).toHaveLength(2);
    expect(out[1].time_msc).toBe(200);
  });
  it("ignores a forming bar older than the last", () => {
    const out = mergeForming([bar(200, 2)], bar(100, 1));
    expect(out).toEqual([bar(200, 2)]);
  });
});
