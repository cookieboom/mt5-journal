import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import LiveLabBadge from "./LiveLabBadge";
import type { LabModel, LabScore } from "../lib/types";

const fetchModels = vi.fn();
const fetchScore = vi.fn();

vi.mock("../lib/lab", async () => {
  const actual = await vi.importActual<typeof import("../lib/lab")>("../lib/lab");
  return {
    ...actual,
    fetchModels: (...a: unknown[]) => fetchModels(...a),
    fetchScore: (...a: unknown[]) => fetchScore(...a),
  };
});

const model = (over: Partial<LabModel>): LabModel =>
  ({
    id: 1, created_ms: 0, symbol: "XAUUSDc", timeframe: "H1", stage: "timing",
    regime: null, kind: "lgbm", pooled: true, active: true, n_rows: 900,
    train_from_ms: 0, train_to_ms: 1, config: {},
    metrics: { n: 900, n_taken: 400, win_rate: 0.5, expectancy_r: 0.1,
               auc: 0.55, baseline_expectancy_r: 0.02, calibration: [], folds: [] },
    ...over,
  } as LabModel);

const score: LabScore = {
  symbol: "XAUUSDc", timeframe: "H1", status: "ok", model_age_ms: 3_600_000,
  expectancy_r: 0.1, expectancy_n: 400, baseline_expectancy_r: 0.02,
  baseline_n: 900, pooled: true,
  bars: [{ time_msc: 1, regime: "trend_up",
           regime_proba: { trend_up: 0.8, trend_down: 0.1, range: 0.1 },
           p_tp_long: 0.6, p_tp_short: 0.3 }],
};

beforeEach(() => {
  fetchModels.mockReset().mockResolvedValue({ models: [] });
  fetchScore.mockReset().mockResolvedValue(score);
});

describe("LiveLabBadge", () => {
  // The M10 regression: /live asked for the page's chart default (M5) while
  // /lab trains H1 by default, so the badge read "No model trained for this
  // symbol and timeframe" on an account with a working H1 model.
  it("scores the timeframe the symbol's model was actually trained on", async () => {
    fetchModels.mockResolvedValue({ models: [model({ timeframe: "H1" })] });
    render(<LiveLabBadge symbol="XAUUSDc" fallbackTf="M5" />);
    await waitFor(() => expect(fetchScore).toHaveBeenCalledWith("XAUUSDc", "H1"));
    expect(await screen.findByText(/XAUUSDc · H1/)).toBeInTheDocument();
    expect(fetchScore).not.toHaveBeenCalledWith("XAUUSDc", "M5");
  });

  it("falls back to the page default when the symbol has nothing trained", async () => {
    fetchModels.mockResolvedValue({ models: [] });
    render(<LiveLabBadge symbol="BTCUSDc" fallbackTf="M15" />);
    await waitFor(() => expect(fetchScore).toHaveBeenCalledWith("BTCUSDc", "M15"));
    expect(await screen.findByText(/BTCUSDc · M15/)).toBeInTheDocument();
  });

  it("still shows a badge when the model lookup itself fails", async () => {
    fetchModels.mockRejectedValue(new Error("database is locked"));
    render(<LiveLabBadge symbol="XAUUSDc" fallbackTf="M5" />);
    await waitFor(() => expect(fetchScore).toHaveBeenCalledWith("XAUUSDc", "M5"));
  });
});
