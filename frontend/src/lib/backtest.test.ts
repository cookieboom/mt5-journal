import { expect, it } from "vitest";
import {
  DEFAULT_BACKTEST, backtestBody, hasSignal, headline, normalizeBacktest, tradeMarkers,
  type BacktestResult, type TesterTrade,
} from "./backtest";
import { normalizeLayout } from "./indicators";

it("normalises tester settings, clamping and defaulting", () => {
  expect(normalizeBacktest(undefined)).toEqual(DEFAULT_BACKTEST);
  const s = normalizeBacktest({ split: 2, sl: "pips", tpR: null, maxHold: 3.6, rangeDays: -4, overlap: true });
  expect(s.split).toBe(0.95);
  expect(s.sl).toBe("atr");
  expect(s.tpR).toBeNull();
  expect(s.maxHold).toBe(4);
  expect(s.rangeDays).toBe(1);
  expect(s.overlap).toBe(true);
});

it("layout keeps tester settings only where an instance has them", () => {
  const l = normalizeLayout({ items: [
    { id: "a", script: "x", inputs: {}, backtest: { split: 0.5 } },
    { id: "b", script: "y", inputs: {} },
  ] });
  expect(l.items[0].backtest?.split).toBe(0.5);
  expect("backtest" in l.items[1]).toBe(false);
});

it("builds the request window from the last closed bar", () => {
  const b = backtestBody({ script: "s", inputs: {}, symbol: "XAUUSDc", tf: "M5", toMs: 100 * 86_400_000,
                           s: { ...DEFAULT_BACKTEST, rangeDays: 30 } });
  expect(b.from_ms).toBe(70 * 86_400_000);
  expect(b.exits).toMatchObject({ sl: "atr", sl_value: 1.5, atr_len: 14, tp_r: 2, opposite_closes: true });
});

it("finds signal() outside comments", () => {
  expect(hasSignal('signal("long", close > open)')).toBe(true);
  expect(hasSignal("plot(close)  # signal(later)")).toBe(false);
  expect(hasSignal("plot(macd_signal(close))")).toBe(false);
});

const trade = (p: Partial<TesterTrade>): TesterTrade => ({
  id: 1, side: "long", decision_msc: 0, entry_msc: 60_000, entry: 1, sl: 0.5, tp: 2,
  exit_msc: 120_000, exit: 2, reason: "tp", r: 1.5, r_gross: 1.6, mae_r: 0, mfe_r: 2,
  spread_fallback: false, ...p,
});

it("marks entry and exit with its R", () => {
  const m = tradeMarkers([trade({}), trade({ id: 2, side: "short", entry_msc: 180_000, exit_msc: null, reason: "open" })]);
  expect(m.map((x) => [x.time, x.shape])).toEqual([[60, "arrowUp"], [120, "circle"], [180, "arrowDown"]]);
  expect(m[1].text).toBe("+1.50R");
});

it("headline leads with OOS, n and age", () => {
  const seg = { n: 34, n_r: 34, win_rate: 0.5, avg_r: 0.21, total_r: 7, profit_factor: 1.3,
                max_dd_r: 2, max_win_streak: 3, max_loss_streak: 2 };
  const r = { computed_ms: 0, segments: { all: seg, is: seg, oos: seg } } as unknown as BacktestResult;
  expect(headline(r, 3 * 60_000)).toBe("OOS +0.21 R/trade · n 34 · 3 mnt lalu");
});
