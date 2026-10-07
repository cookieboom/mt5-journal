import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import StrategyTester from "./StrategyTester";
import {
  backtestApi, DEFAULT_BACKTEST, type BacktestResult, type ContextCell, type ContextResult, type Period,
} from "../lib/backtest";

const seg = (avg: number, n: number) => ({ n, n_r: n, win_rate: 0.5, avg_r: avg, total_r: avg * n,
  profit_factor: 1.2, max_dd_r: 1, max_win_streak: 2, max_loss_streak: 1 });
const bk = { hour: [], session: [], dow: [] };
const DAY = 86_400_000;
const per = (key: number, total_r: number, oos = false): Period =>
  ({ key, end_msc: key + DAY, oos, n: 3, win_rate: 0.5, avg_r: total_r / 3, total_r });
const periods = {
  day: [per(0, 1.0), per(DAY, -2.0), per(2 * DAY, 3.0, true)],
  week: [per(-3 * DAY, 2.0)],
  session: [per(0, 1.0)],
};
const result: BacktestResult = {
  computed_ms: 0, source_hash: "h", split_msc: 0,
  segments: { all: seg(0.1, 50), is: seg(0.05, 16), oos: seg(0.21, 34) },
  equity: [], breakdown: { all: { ...bk, periods }, oos: bk },
  trades: [{ id: 1, side: "long", decision_msc: 0, entry_msc: 60_000, entry: 1, sl: 0.5, tp: 2,
             exit_msc: 120_000, exit: 2, reason: "tp", r: 1.5, r_gross: 1.6, mae_r: 0, mfe_r: 2,
             spread_fallback: false }],
  counts: { rejected: 0, open: 0, spread_fallback: 0, unknown_cost: 0, no_stop: 0 },
  warm_from_msc: 0, pending: false,
};

const QUERY = { script: "s1", inputs: {}, symbol: "XAUUSDc", tf: "M5", rev: 0 };

function setup(r: BacktestResult | null = result) {
  const onTradeClick = vi.fn(), onReplay = vi.fn(), onPeriodReplay = vi.fn();
  render(<StrategyTester candidates={[{ id: "a", name: "EMA cross" }]} activeId="a" onActive={() => {}}
    settings={DEFAULT_BACKTEST} onSettings={() => {}} result={r} error={null} loading={false}
    armed onRun={() => {}} onTradeClick={onTradeClick} onReplay={onReplay}
    onPeriodReplay={onPeriodReplay} context={QUERY} nowMs={180_000} />);
  return { onTradeClick, onReplay, onPeriodReplay };
}

afterEach(() => { vi.restoreAllMocks(); });

it("collapsed strip shows the OOS headline with n and age", () => {
  setup();
  expect(screen.getByText("OOS +0.21 R/trade · n 34 · 3 mnt lalu")).toBeTruthy();
});

it("summary puts the OOS column first", () => {
  setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  const heads = screen.getAllByRole("columnheader").map((h) => h.textContent).filter(Boolean);
  expect(heads.slice(0, 3)).toEqual(["OOS", "IS", "Semua"]);
});

it("a trade row scrolls the chart; Replay jumps without scrolling", () => {
  const { onTradeClick, onReplay } = setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  fireEvent.click(screen.getByRole("tab", { name: "Daftar trade" }));
  fireEvent.click(screen.getByText("TP"));
  expect(onTradeClick).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("Replay"));
  expect(onReplay).toHaveBeenCalledTimes(1);
  expect(onTradeClick).toHaveBeenCalledTimes(1);
});

// --- replay jump per period -------------------------------------------------------

it("periods list ranks by total R, best first, and toggles worst first", () => {
  setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  fireEvent.click(screen.getByRole("tab", { name: "Breakdown" }));
  const rows = () => screen.getAllByTestId("period-row").map((r) => r.getAttribute("data-key"));
  expect(rows()).toEqual([String(2 * DAY), "0", String(DAY)]);
  fireEvent.click(screen.getByRole("button", { name: "Urutkan periode" }));
  expect(rows()).toEqual([String(DAY), "0", String(2 * DAY)]);
  fireEvent.click(screen.getByRole("button", { name: "Minggu" }));
  expect(rows()).toEqual([String(-3 * DAY)]);
});

it("a period row marks OOS and its Replay jumps to that period", () => {
  const { onPeriodReplay } = setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  fireEvent.click(screen.getByRole("tab", { name: "Breakdown" }));
  const best = screen.getAllByTestId("period-row")[0];
  expect(best.textContent).toContain("OOS");
  fireEvent.click(best.querySelector("button")!);
  expect(onPeriodReplay).toHaveBeenCalledWith(periods.day[2]);
});

// --- trade context ------------------------------------------------------------------

const cell = (c: Partial<ContextCell>): ContextCell =>
  ({ n: 0, n_r: 0, win_rate: null, avg_r: null, total_r: null, gated: false, ...c });
const ctx: ContextResult = {
  computed_ms: 0, source_hash: "h", window: 3, warm_from_msc: 0, pending: false,
  sources: {
    real: { true: cell({ n: 70, n_r: 18, win_rate: 0.58, gated: true }),
            false: cell({ n: 60, n_r: 18, win_rate: 0.4, gated: true }), n_unknown: 7 },
    replay: { true: cell({ n: 4, n_r: 4, win_rate: 0.75, avg_r: 0.5, total_r: 2 }),
              false: cell({ n: 2, n_r: 2, win_rate: 0, avg_r: -1, total_r: -2 }), n_unknown: 0 },
    paper: { true: cell({}), false: cell({}), n_unknown: 0 },
  },
};

it("trade context is fetched only when its tab opens, and needs no tester result", async () => {
  const spy = vi.spyOn(backtestApi, "context").mockResolvedValue({ ok: true, data: ctx });
  setup(null);
  fireEvent.click(screen.getByLabelText("Buka tester"));
  expect(spy).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("tab", { name: "Trade saya" }));
  await waitFor(() => expect(spy).toHaveBeenCalledWith(QUERY, 3));
  expect(await screen.findByText("58%")).toBeTruthy();
});

it("gated real cells say why; ungated sim cells show their numbers", async () => {
  vi.spyOn(backtestApi, "context").mockResolvedValue({ ok: true, data: ctx });
  setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  fireEvent.click(screen.getByRole("tab", { name: "Trade saya" }));
  await screen.findByText("58%");
  expect(screen.getAllByTitle("n R 18 < 20").length).toBeGreaterThan(0);
  expect(screen.getByText("tidak diketahui: 7")).toBeTruthy();
  expect(screen.getByText("+2.00")).toBeTruthy();                      // replay total R
  expect(screen.getByText(/OOS \+0\.21 R\/trade/, { selector: "[data-testid=ctx-headline]" })).toBeTruthy();
});

it("changing the window refetches with it", async () => {
  const spy = vi.spyOn(backtestApi, "context").mockResolvedValue({ ok: true, data: ctx });
  setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  fireEvent.click(screen.getByRole("tab", { name: "Trade saya" }));
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  await act(async () => {
    fireEvent.change(screen.getByLabelText("Jendela (bar)"), { target: { value: "5" } });
  });
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(QUERY, 5));
});

it("a context error is shown, not swallowed", async () => {
  vi.spyOn(backtestApi, "context").mockResolvedValue({ ok: false, error: "baris 1: boom" });
  setup();
  fireEvent.click(screen.getByLabelText("Buka tester"));
  fireEvent.click(screen.getByRole("tab", { name: "Trade saya" }));
  expect(await screen.findByText("baris 1: boom")).toBeTruthy();
});
