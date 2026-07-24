import { describe, it, expect } from "vitest";
import { histogramBins, dayStartUtcMs, calendarCells, rValues, maeMfePoints } from "./charts";

describe("charts", () => {
  it("histogramBins: fixed bins with open ends, all present, correct counts", () => {
    const bins = histogramBins([-5, -1.5, -0.2, 0.5, 1.5, 2.5, 9]);
    expect(bins.length).toBe(7);
    const counts = bins.map((b) => b.count);
    // (-inf,-2):[-5]  [-2,-1):[-1.5]  [-1,0):[-0.2]  [0,1):[0.5]
    // [1,2):[1.5]  [2,3):[2.5]  [3,inf):[9]
    expect(counts).toEqual([1, 1, 1, 1, 1, 1, 1]);
    expect(bins[3].label).toBe("[0,1)");
  });

  it("histogramBins: boundary values land in the LEFT-closed bin", () => {
    // 0 → [0,1) not [-1,0);  1 → [1,2);  -1 → [-1,0)
    const b = histogramBins([0, 1, -1]);
    expect(b[2].count).toBe(1); // [-1,0): the -1
    expect(b[3].count).toBe(1); // [0,1): the 0
    expect(b[4].count).toBe(1); // [1,2): the 1
  });

  it("dayStartUtcMs: floors to UTC midnight", () => {
    const noon = Date.UTC(2026, 0, 15, 12, 30, 0); // 2026-01-15 12:30 UTC
    expect(dayStartUtcMs(noon)).toBe(Date.UTC(2026, 0, 15, 0, 0, 0));
  });

  it("calendarCells: groups by UTC day, sums net, counts, ascending", () => {
    const d15a = Date.UTC(2026, 0, 15, 9);
    const d15b = Date.UTC(2026, 0, 15, 20);
    const d16 = Date.UTC(2026, 0, 16, 3);
    const cells = calendarCells([
      { close_time_msc: d16, net_profit: -10 },
      { close_time_msc: d15a, net_profit: 250 },
      { close_time_msc: d15b, net_profit: -50 },
    ]);
    expect(cells.length).toBe(2);
    expect(cells[0]).toEqual({ day_ms: Date.UTC(2026, 0, 15), net: 200, n: 2 });
    expect(cells[1]).toEqual({ day_ms: Date.UTC(2026, 0, 16), net: -10, n: 1 });
  });

  it("rValues: drops null r_multiple, keeps reals incl. a genuine 0", () => {
    const base = { symbol_base: "XAUUSD", close_time_msc: 0, net_profit: 0, mae_r: null, mfe_r: null };
    const s = [
      { ...base, position_id: 1, r_multiple: 1.5 },
      { ...base, position_id: 2, r_multiple: null },
      { ...base, position_id: 3, r_multiple: 0 },
    ];
    expect(rValues(s)).toEqual([1.5, 0]); // rule 4: null dropped, real 0 kept
  });

  it("maeMfePoints: drops a trade missing either MAE or MFE", () => {
    const base = { symbol_base: "XAUUSD", close_time_msc: 0, net_profit: 0, r_multiple: null };
    const s = [
      { ...base, position_id: 1, mae_r: -0.4, mfe_r: 2.1 },
      { ...base, position_id: 2, mae_r: -0.4, mfe_r: null },
      { ...base, position_id: 3, mae_r: null, mfe_r: 2.1 },
    ];
    expect(maeMfePoints(s).map((p) => p.position_id)).toEqual([1]);
  });

  it("histogramBins: -2 and 3 land left-closed at their edge bins", () => {
    const b = histogramBins([-2, 3]);
    expect(b[1].count).toBe(1); // [-2,-1): the -2
    expect(b[6].count).toBe(1); // [3,∞): the 3
  });

  it("dayStartUtcMs: midnight is a no-op; end-of-day floors to that midnight", () => {
    const mid = Date.UTC(2026, 0, 15, 0, 0, 0);
    expect(dayStartUtcMs(mid)).toBe(mid);
    const eod = Date.UTC(2026, 0, 15, 23, 59, 59, 999);
    expect(dayStartUtcMs(eod)).toBe(mid);
  });
});
