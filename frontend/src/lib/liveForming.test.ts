import { describe, it, expect } from "vitest";
import { mergeForming, staleEntryReason } from "./candles";
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

// The gate behind the open button. Sizing reads the last shown bar's close, and
// the volume is frozen at enqueue — so an old reference price ships a lot the
// market has already invalidated. See docs/HANDOFF.md, OPEN QUESTION.
describe("staleEntryReason", () => {
  const TF = 60_000;   // M1
  it("passes a live feed whose forming bar is current", () => {
    expect(staleEntryReason(true, 1_000_000, TF, 1_030_000)).toBeNull();
  });
  it("blocks when the journal live heartbeat is cold", () => {
    expect(staleEntryReason(false, 1_000_000, TF, 1_030_000)).toMatch(/journal live/);
  });
  // null = the chart is not polling (replay/config drawer open, or the first
  // poll has not answered yet). It still blocks, but it must NOT claim the
  // daemon is dead — that is the bug the browser pass caught, with the badge
  // reading `live · 1s` next to it.
  it("blocks without accusing journal live when liveness is unknown", () => {
    const msg = staleEntryReason(null, 1_000_000, TF, 1_030_000);
    expect(msg).not.toBeNull();
    expect(msg).not.toMatch(/journal live/);
  });
  it("blocks when there is no bar to read a price off at all", () => {
    expect(staleEntryReason(true, null, TF, 1_030_000)).not.toBeNull();
  });
  // The daemon can beat while the bar on screen stops advancing: a lapsed
  // watch, a closed market, or a candle fetch that stalled while the poll kept
  // running. The heartbeat alone would not catch any of them.
  it("blocks when the shown bar has not advanced for two intervals", () => {
    expect(staleEntryReason(true, 1_000_000, TF, 1_000_000 + 2 * TF + 1))
      .toMatch(/basi/i);
  });
  it("allows exactly two intervals — only older than that is stale", () => {
    expect(staleEntryReason(true, 1_000_000, TF, 1_000_000 + 2 * TF)).toBeNull();
  });
});
