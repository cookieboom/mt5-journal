import { describe, it, expect } from "vitest";
import {
  timeframeMs, toSeconds, initialWindow, olderWindow, mergeCandles,
  isNowVisible, liveLines, LINE_COLORS, capCandles, backfillWindow, barCloseCountdown,
  axisTickLabel,
} from "./candles";
import type { Candle } from "./types";
import type { LivePosition } from "./types";

const M1 = 60_000;
const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 2, l: 0.5, c: 1.5, v: 3 });

describe("candles helpers", () => {
  it("timeframeMs maps each frame to ms", () => {
    expect(timeframeMs("M1")).toBe(M1);
    expect(timeframeMs("M5")).toBe(5 * M1);
    expect(timeframeMs("M15")).toBe(15 * M1);
    expect(timeframeMs("H1")).toBe(60 * M1);
    expect(timeframeMs("H4")).toBe(240 * M1);
    expect(timeframeMs("D1")).toBe(1440 * M1);
  });

  it("toSeconds floors ms to unix seconds", () => {
    expect(toSeconds(1_700_000_000_500)).toBe(1_700_000_000);
  });

  it("initialWindow spans `bars` bars ending at now", () => {
    const now = 1_700_000_000_000;
    expect(initialWindow("M5", now, 300)).toEqual([now - 300 * 5 * M1, now]);
  });

  it("olderWindow extends left of the current oldest, non-overlapping", () => {
    const from = 1_700_000_000_000;
    expect(olderWindow(from, "M5", 300)).toEqual([from - 300 * 5 * M1, from - 1]);
  });

  it("mergeCandles dedupes by time, incoming wins, sorted ascending", () => {
    const a = [bar(3000), bar(1000)];
    const b = [bar(2000), { ...bar(1000), c: 9 }];
    const out = mergeCandles(a, b);
    expect(out.map((c) => c.time_msc)).toEqual([1000, 2000, 3000]);
    expect(out[0].c).toBe(9); // incoming overwrote existing at t=1000
  });

  it("isNowVisible true only when the right edge reaches the last bar", () => {
    const last = 1_700_000_000_000;
    expect(isNowVisible(last, last, "M5")).toBe(true);
    expect(isNowVisible(last, last - 5 * M1, "M5")).toBe(true);   // within one bar
    expect(isNowVisible(last, last - 6 * M1, "M5")).toBe(false);  // panned away
    expect(isNowVisible(null, last, "M5")).toBe(false);
    expect(isNowVisible(last, null, "M5")).toBe(false);
  });

  it("liveLines draws real prices only — skips null and 0.0 (rule 4)", () => {
    const base: LivePosition = {
      position_id: 7, symbol: "XAUUSDc", symbol_base: "XAUUSD",
      direction: "buy", volume: 0.1, open_price: 2405, price_current: 2410,
      sl: 0, tp: null, profit: 100, observed_msc: 1,
    };
    const lines = liveLines(base);
    // entry drawn (2405); SL skipped (0 = none set); TP skipped (null = unknown)
    expect(lines.map((l) => l.price)).toEqual([2405]);
    expect(lines[0].color).toBe(LINE_COLORS.entry);
    expect(lines[0].kind).toBe("entry");

    const full = liveLines({ ...base, sl: 2398, tp: 2412 });
    expect(full.map((l) => l.price).sort()).toEqual([2398, 2405, 2412]);
    const byTitle = Object.fromEntries(full.map((l) => [l.title.split(" ")[0], l.color]));
    expect(byTitle.SL).toBe(LINE_COLORS.sl);
    expect(byTitle.TP).toBe(LINE_COLORS.tp);
    const byKind = Object.fromEntries(full.map((l) => [l.kind, l.price]));
    expect(byKind).toEqual({ entry: 2405, sl: 2398, tp: 2412 });
  });
});

describe("backfillWindow", () => {
  const M1 = 60_000;
  it("doubles the lookback span, keeping the same right edge", () => {
    const to = 1_700_000_000_000;
    const from = to - 5 * 60 * M1;          // a 5-hour window
    // next try is twice as far back, still ending at `to`
    expect(backfillWindow(from, to, 30 * 24 * 60 * M1)).toEqual([to - 10 * 60 * M1, to]);
  });

  it("caps the widened span at maxSpanMs instead of overshooting", () => {
    const to = 1_700_000_000_000;
    const from = to - 20 * 60 * M1;         // 20h window; doubling would be 40h
    const max = 30 * 60 * M1;               // but the cap is 30h
    expect(backfillWindow(from, to, max)).toEqual([to - max, to]);
  });

  it("returns null once the window already spans maxSpanMs (give up: no data in reach)", () => {
    const to = 1_700_000_000_000;
    const max = 30 * 60 * M1;
    expect(backfillWindow(to - max, to, max)).toBeNull();
    expect(backfillWindow(to - (max + 1), to, max)).toBeNull();  // already past the bound
  });
});

describe("capCandles", () => {
  const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 0 });

  it("returns the array unchanged when at or under the cap", () => {
    const cs = [bar(1), bar(2), bar(3)];
    expect(capCandles(cs, 3)).toBe(cs);
    expect(capCandles(cs, 10)).toBe(cs);
  });
  it("drops the OLDEST bars beyond maxBars, keeping the newest and order", () => {
    const cs = [bar(1), bar(2), bar(3), bar(4), bar(5)];
    expect(capCandles(cs, 2).map((c) => c.time_msc)).toEqual([4, 5]);
  });
});

describe("barCloseCountdown", () => {
  const bucket = 1_700_000_100_000;            // epoch-aligned to a whole minute

  it("counts down to the end of the CURRENT bucket", () => {
    expect(barCloseCountdown(bucket, "M1")).toBe("01:00");          // just opened
    expect(barCloseCountdown(bucket + 56_000, "M1")).toBe("00:04");
    expect(barCloseCountdown(bucket + 59_900, "M1")).toBe("00:01"); // sub-second rounds up
  });
  it("switches to H:MM:SS once more than an hour is left", () => {
    const h4 = bucket - (bucket % (240 * 60_000));
    expect(barCloseCountdown(h4 + 60_000, "H4")).toBe("3:59:00");
    expect(barCloseCountdown(h4 + 239 * 60_000, "H4")).toBe("01:00");
  });
});

describe("axisTickLabel", () => {
  // 2026-08-14 02:30 UTC = 09:30 WIB the same day.
  const t = Date.UTC(2026, 7, 14, 2, 30);

  it("prints time only on a Time tick, date only on a day/month/year tick", () => {
    expect(axisTickLabel(t, 3)).toBe("09:30");          // TickMarkType.Time
    expect(axisTickLabel(t, 4)).toBe("09:30");          // TimeWithSeconds
    expect(axisTickLabel(t, 2)).toBe("2026-08-14");     // DayOfMonth
    expect(axisTickLabel(t, 1)).toBe("2026-08-14");     // Month
    expect(axisTickLabel(t, 0)).toBe("2026-08-14");     // Year
  });

  it("never leaks the date under competitive hideDate, whatever the tick type", () => {
    for (const type of [0, 1, 2, 3, 4]) {
      expect(axisTickLabel(t, type, true)).toBe("09:30");
    }
  });

  it("reads WIB, so a late-UTC tick already belongs to the next day", () => {
    const evening = Date.UTC(2026, 7, 14, 20, 0);       // 03:00 WIB on the 15th
    expect(axisTickLabel(evening, 3)).toBe("03:00");
    expect(axisTickLabel(evening, 2)).toBe("2026-08-15");
  });
});
