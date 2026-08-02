import { describe, it, expect } from "vitest";
import { mergeForming } from "./candles";
import type { Candle } from "./types";

const bar = (t: number, c: number): Candle =>
  ({ time_msc: t, o: 1, h: 2, l: 0.5, c, v: 1 } as unknown as Candle);

describe("mergeForming", () => {
  it("returns candles unchanged when forming is null", () => {
    const cs = [bar(100, 1)];
    expect(mergeForming(cs, null, 100)).toEqual(cs);
  });
  it("replaces the last bar when time_msc matches", () => {
    const out = mergeForming([bar(100, 1), bar(200, 2)], bar(200, 9), 100);
    expect(out).toHaveLength(2);
    expect(out[1].c).toBe(9);
  });
  it("appends when forming is exactly one interval ahead", () => {
    const out = mergeForming([bar(100, 1)], bar(200, 2), 100);
    expect(out).toHaveLength(2);
    expect(out[1].time_msc).toBe(200);
  });
  it("ignores a forming bar older than the last", () => {
    const out = mergeForming([bar(200, 2)], bar(100, 1), 100);
    expect(out).toEqual([bar(200, 2)]);
  });
  // Reproduces the reported bug: the instant a bar closes, `forming` (from
  // the 5s live poll) advances to the NEXT bucket immediately, but
  // data.candles only catches up once loadUpTo's async fetch resolves.
  // During that gap, forming.time_msc sits MORE than one interval ahead of
  // the last historical bar. Appending it anyway would put the brand-new,
  // barely-started next bar's tiny OHLC directly after the OLD last bar —
  // visually indistinguishable from "the just-closed bar's shape changing
  // the instant it becomes historical," since what's actually shown is the
  // WRONG bar in that slot until loadUpTo resolves and this runs again.
  it("does not append a forming bar more than one interval ahead of the last bar", () => {
    const out = mergeForming([bar(100, 1)], bar(300, 9), 100);
    expect(out).toEqual([bar(100, 1)]);
  });
});
