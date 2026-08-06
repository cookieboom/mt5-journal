import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Lab from "./Lab";
import type { LabModel } from "../lib/types";

const trainModels = vi.fn();
const fetchModels = vi.fn();
const activateModel = vi.fn();

vi.mock("../lib/lab", async () => {
  const actual = await vi.importActual<typeof import("../lib/lab")>("../lib/lab");
  return {
    ...actual,
    trainModels: (...a: unknown[]) => trainModels(...a),
    fetchModels: (...a: unknown[]) => fetchModels(...a),
    activateModel: (...a: unknown[]) => activateModel(...a),
    fetchRegimes: vi.fn().mockResolvedValue({ status: "no_model", bars: [] }),
  };
});

const model = (over: Partial<LabModel>): LabModel =>
  ({
    id: 1, created_ms: Date.now(), symbol: "XAUUSDc", timeframe: "H1",
    stage: "timing", regime: null, kind: "lgbm", pooled: true, active: true,
    n_rows: 900, train_from_ms: 0, train_to_ms: 1,
    config: {}, metrics: { n: 900, n_taken: 400, win_rate: 0.41,
      expectancy_r: 0.12, auc: 0.55, baseline_expectancy_r: -0.03,
      calibration: [], folds: [] },
    ...over,
  } as LabModel);

beforeEach(() => {
  trainModels.mockReset();
  fetchModels.mockReset().mockResolvedValue({ models: [] });
  activateModel.mockReset().mockResolvedValue({ ok: true, id: 1 });
});

describe("Lab page", () => {
  it("renders a checkbox per feature, all on by default", async () => {
    render(<Lab />);
    const boxes = await screen.findAllByRole("checkbox");
    expect(boxes).toHaveLength(14);
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);
  });

  it("posts the form when Train is pressed", async () => {
    trainModels.mockResolvedValue({ model_ids: [1], models: [model({})],
      dropped_features: {}, spread_assumed: false, n_bars_read: 1000 });
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: /train/i }));
    await waitFor(() => expect(trainModels).toHaveBeenCalledTimes(1));
    expect(trainModels.mock.calls[0][0]).toMatchObject({
      symbol: "XAUUSDc", n_bars: 24, rr: 2,
    });
  });

  it("shows expectancy in R beside the baseline", async () => {
    fetchModels.mockResolvedValue({ models: [model({})] });
    render(<Lab />);
    expect(await screen.findByText(/0\.12/)).toBeInTheDocument();
    expect(await screen.findByText(/-0\.03/)).toBeInTheDocument();
  });

  it("suppresses a rate computed from fewer than 20 rows", async () => {
    fetchModels.mockResolvedValue({
      models: [model({ metrics: { n: 5, n_taken: 5, win_rate: 0.8,
        expectancy_r: 3.0, auc: null, baseline_expectancy_r: 0,
        calibration: [], folds: [] } })],
    });
    render(<Lab />);
    expect(await screen.findByText(/n\s*=\s*5/)).toBeInTheDocument();
    expect(screen.queryByText(/80%/)).not.toBeInTheDocument();
  });

  it("surfaces a dropped feature after training", async () => {
    trainModels.mockResolvedValue({ model_ids: [1], models: [model({})],
      dropped_features: { spread: 0.9 }, spread_assumed: true, n_bars_read: 900 });
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: /train/i }));
    // Scoped to the warning itself (data-testid), not a bare /spread/i text
    // search — "spread" also appears in the form's field label and the
    // feature checklist, and an unanchored matcher can't tell those apart
    // from the warning it's meant to target.
    expect(await screen.findByTestId("dropped-features-warning")).toHaveTextContent(/spread/i);
  });

  it("shows the server's message when training is refused, and un-sticks the button", async () => {
    trainModels.mockRejectedValue(new Error("not enough labelled rows to train: 12"));
    render(<Lab />);
    const button = screen.getByRole("button", { name: /train/i });
    await userEvent.click(button);
    expect(await screen.findByText(/not enough labelled rows/i)).toBeInTheDocument();
    // A rejected trainModels must not leave the button reading "Training…"
    // forever — the busy state has to clear even on the error path.
    expect(button).toHaveTextContent(/^train$/i);
    expect(button).not.toBeDisabled();
  });

  it("shows a visible error when the model list fails to load", async () => {
    fetchModels.mockReset().mockRejectedValue(new Error("database is locked"));
    render(<Lab />);
    expect(await screen.findByText(/database is locked/i)).toBeInTheDocument();
  });

  it("activates a model when its button is pressed", async () => {
    fetchModels.mockResolvedValue({
      models: [model({ id: 7, kind: "logreg", active: false })],
    });
    render(<Lab />);
    await userEvent.click(await screen.findByRole("button", { name: /activate/i }));
    await waitFor(() => expect(activateModel).toHaveBeenCalledWith(7));
  });
});
