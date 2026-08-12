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
  // The minimum a live chart always knows. The feed-agreement fields below are
  // optional on purpose: before the first poll answers there is nothing to
  // compare, and the heartbeat branch has already blocked by then.
  const base = { entryBarMs: 1_000_000, intervalMs: TF, nowMs: 1_030_000 };
  it("passes a live feed whose forming bar is current", () => {
    expect(staleEntryReason({ ...base, feedLive: true })).toBeNull();
  });
  it("blocks when the journal live heartbeat is cold", () => {
    expect(staleEntryReason({ ...base, feedLive: false })).toMatch(/journal live/);
  });
  // null = the chart is not polling (replay/config drawer open, or the first
  // poll has not answered yet). It still blocks, but it must NOT claim the
  // daemon is dead — that is the bug the browser pass caught, with the badge
  // reading `live · 1s` next to it.
  it("blocks without accusing journal live when liveness is unknown", () => {
    const msg = staleEntryReason({ ...base, feedLive: null });
    expect(msg).not.toBeNull();
    expect(msg).not.toMatch(/journal live/);
  });
  it("blocks when there is no bar to read a price off at all", () => {
    expect(staleEntryReason({ ...base, feedLive: true, entryBarMs: null })).not.toBeNull();
  });
  // The daemon can beat while the bar on screen stops advancing: a lapsed
  // watch, a closed market, or a candle fetch that stalled while the poll kept
  // running. The heartbeat alone would not catch any of them.
  it("blocks when the shown bar has not advanced for two intervals", () => {
    expect(staleEntryReason({ ...base, feedLive: true, nowMs: 1_000_000 + 2 * TF + 1 }))
      .toMatch(/basi/i);
  });
  it("allows exactly two intervals — only older than that is stale", () => {
    expect(staleEntryReason({ ...base, feedLive: true, nowMs: 1_000_000 + 2 * TF }))
      .toBeNull();
  });

  // --- parity with the server guard (`execute._check_feed_fresh`) ------------
  // Everything above is what the browser could check on its own. These two are
  // the checks the server ALSO makes, and used to make alone: the button armed,
  // the human clicked, and the open came back 400. Same windows, same numbers.

  // Server check 2: an actively watched forming bar that has not been
  // refreshed inside FEED_STALE_MS. The bar's own `time_msc` cannot show this
  // — a frozen feed keeps reporting the current bucket, unchanged, forever.
  it("blocks when the forming row has not been refreshed inside the server window", () => {
    const msg = staleEntryReason({
      ...base, feedLive: true, formingUpdatedMs: 1_030_000 - 15_000,
    });
    expect(msg).toMatch(/beku/i);
  });
  it("allows a forming row refreshed just inside the window", () => {
    expect(staleEntryReason({
      ...base, feedLive: true, formingUpdatedMs: 1_030_000 - 14_999,
    })).toBeNull();
  });
  // A quiet bucket is restamped by `touch_forming` without any price moving,
  // so freshness must be read off the stamp only — never off the prices.
  it("does not treat a quiet but restamped feed as frozen", () => {
    expect(staleEntryReason({
      ...base, feedLive: true, formingUpdatedMs: 1_030_000,
      formingClose: 4035, priceRef: 4035, sl: 4030,
    })).toBeNull();
  });

  // Server check 3: the poll is fresh, but the price the lot is SIZED from is
  // not the one the server sees. This is the wedged-`/api/candles` case —
  // `mergeForming` refuses to append a forming bar more than one interval
  // ahead, so the panel keeps sizing off the last bar it managed to fetch
  // while the poll happily reports a current one.
  it("blocks when the sized price has drifted past a quarter of the stop distance", () => {
    // stop distance 5.0 -> tolerance 1.25; the shown bar is 2.0 behind.
    const msg = staleEntryReason({
      ...base, feedLive: true, formingUpdatedMs: 1_030_000,
      formingClose: 4037, priceRef: 4035, sl: 4030,
    });
    expect(msg).toMatch(/muat ulang/i);
  });
  it("allows drift inside the tolerance", () => {
    expect(staleEntryReason({
      ...base, feedLive: true, formingUpdatedMs: 1_030_000,
      formingClose: 4036, priceRef: 4035, sl: 4030,
    })).toBeNull();
  });
  // The same absolute drift is harmless against a wide stop and total against a
  // tight one, which is why the tolerance is a fraction of the distance and not
  // a fixed number of price units.
  it("scales the tolerance with the stop distance, not with the price", () => {
    const drift = { formingClose: 4035.6, priceRef: 4035, formingUpdatedMs: 1_030_000 };
    expect(staleEntryReason({ ...base, feedLive: true, ...drift, sl: 4030 })).toBeNull();
    expect(staleEntryReason({ ...base, feedLive: true, ...drift, sl: 4034 }))
      .toMatch(/muat ulang/i);
  });
  // No stop, no lot: `useRiskSizing` returns nothing and the button is already
  // dead, so there is no tolerance to compute and nothing to refuse.
  it("skips the price comparison when there is no stop yet", () => {
    expect(staleEntryReason({
      ...base, feedLive: true, formingUpdatedMs: 1_030_000,
      formingClose: 9999, priceRef: 4035, sl: null,
    })).toBeNull();
  });
});
