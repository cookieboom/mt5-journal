import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import StrategyTester from "./StrategyTester";
import { DEFAULT_BACKTEST, type BacktestResult } from "../lib/backtest";

const seg = (avg: number, n: number) => ({ n, n_r: n, win_rate: 0.5, avg_r: avg, total_r: avg * n,
  profit_factor: 1.2, max_dd_r: 1, max_win_streak: 2, max_loss_streak: 1 });
const bk = { hour: [], session: [], dow: [] };
const result: BacktestResult = {
  computed_ms: 0, source_hash: "h", split_msc: 0,
  segments: { all: seg(0.1, 50), is: seg(0.05, 16), oos: seg(0.21, 34) },
  equity: [], breakdown: { all: bk, oos: bk },
  trades: [{ id: 1, side: "long", decision_msc: 0, entry_msc: 60_000, entry: 1, sl: 0.5, tp: 2,
             exit_msc: 120_000, exit: 2, reason: "tp", r: 1.5, r_gross: 1.6, mae_r: 0, mfe_r: 2,
             spread_fallback: false }],
  counts: { rejected: 0, open: 0, spread_fallback: 0, unknown_cost: 0, no_stop: 0 },
  warm_from_msc: 0, pending: false,
};

function setup() {
  const onTradeClick = vi.fn(), onReplay = vi.fn();
  render(<StrategyTester candidates={[{ id: "a", name: "EMA cross" }]} activeId="a" onActive={() => {}}
    settings={DEFAULT_BACKTEST} onSettings={() => {}} result={result} error={null} loading={false}
    armed onRun={() => {}} onTradeClick={onTradeClick} onReplay={onReplay} nowMs={180_000} />);
  return { onTradeClick, onReplay };
}

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
