import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Lab from "./Lab";
import type { LabModel } from "../lib/types";

const trainModels = vi.fn();
const fetchModels = vi.fn();
const activateModel = vi.fn();
const fetchRegimes = vi.fn();

vi.mock("../lib/lab", async () => {
  const actual = await vi.importActual<typeof import("../lib/lab")>("../lib/lab");
  return {
    ...actual,
    trainModels: (...a: unknown[]) => trainModels(...a),
    fetchModels: (...a: unknown[]) => fetchModels(...a),
    activateModel: (...a: unknown[]) => activateModel(...a),
    fetchRegimes: (...a: unknown[]) => fetchRegimes(...a),
  };
});

// The real CandleChart (lightweight-charts + canvas) is mocked the same way
// Chart.test.tsx mocks it: a forwardRef stub that captures the props Lab.tsx
// wires to it and exposes a controllable timeToX through the imperative
// handle. That lets a test simulate the chart's time scale actually moving
// (a pan/zoom) independently of the onNowVisibleChange boolean it fires —
// which is exactly the distinction the staleness bug lived in.
let capturedCandleChartProps: any = null;
const timeToXImpl = vi.fn((ms: number) => ms / 1000);
vi.mock("../components/CandleChart", () => ({
  default: React.forwardRef((props: any, ref: any) => {
    capturedCandleChartProps = props;
    React.useImperativeHandle(ref, () => ({
      jumpToNow: () => {},
      timeToX: (ms: number) => timeToXImpl(ms),
    }));
    return <div data-testid="candle-chart" />;
  }),
}));

// useChartData's fetchCandles hits real `fetch("/api/candles?...")` — stub it
// globally so the chart section's hasBars gate can flip true on demand.
// Empty by default (most tests never look at the chart section at all).
function stubFetch(candles: { time_msc: number; o: number; h: number; l: number; c: number; v: number }[] = []) {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.startsWith("/api/candles")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ symbol: "XAUUSDc", timeframe: "H1", candles, missing: [], pending: false }),
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  }));
}

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
  fetchRegimes.mockReset().mockResolvedValue({ status: "no_model", bars: [] });
  capturedCandleChartProps = null;
  timeToXImpl.mockReset().mockImplementation((ms: number) => ms / 1000);
  stubFetch();
});

afterEach(() => { vi.unstubAllGlobals(); });

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

  // Regression for the staleness bug found in review: Lab.tsx used to force a
  // re-render only via setNowVisible(v), a DERIVED BOOLEAN — React bails out
  // of re-rendering on an Object.is-equal setter call, so a pan that never
  // flips "is now visible" (or a zoom that doesn't) left the overlay/strip
  // frozen at stale pixel coordinates while the chart moved underneath. This
  // fires onNowVisibleChange with the SAME boolean it already holds (the
  // initial `false`) and asserts the overlay still reprojects — a render
  // must happen regardless of whether the boolean itself changed.
  it("reprojects the regime overlay when the chart's time scale moves, even if onNowVisibleChange repeats the same boolean", async () => {
    stubFetch([
      { time_msc: 0, o: 1, h: 1, l: 1, c: 1, v: 1 },
      { time_msc: 3_600_000, o: 1, h: 1, l: 1, c: 1, v: 1 },
    ]);
    fetchRegimes.mockResolvedValue({
      symbol: "XAUUSDc", timeframe: "H1", status: "ok",
      model_age_ms: 3_600_000, expectancy_r: 0.2, expectancy_n: 50, pooled: true,
      bars: [
        { time_msc: 0, regime: "trend_up", regime_proba: { trend_up: 1, trend_down: 0, range: 0 },
          p_tp_long: 0.6, p_tp_short: 0.4 },
        { time_msc: 3_600_000, regime: "range", regime_proba: { trend_up: 0, trend_down: 0, range: 1 },
          p_tp_long: 0.5, p_tp_short: 0.5 },
      ],
    });

    render(<Lab />);
    await screen.findByTestId("candle-chart");
    await waitFor(() => expect(fetchRegimes).toHaveBeenCalled());

    // The real CandleChart fires onNowVisibleChange itself right after it
    // mounts (lightweight-charts' initial visible-range event) — that's what
    // gives the overlay its first correct projection, since chartRef.current
    // only attaches during commit, one tick after CandleChart first renders.
    // The static mock never does this on its own, so settle the SAME way a
    // real mount would before taking the "before" snapshot.
    await act(async () => { capturedCandleChartProps.onNowVisibleChange(false); });
    const rectsBefore = await waitFor(() => {
      const rects = document.querySelectorAll("svg.regime-overlay rect");
      expect(rects.length).toBeGreaterThan(0);
      return Array.from(rects).map((r) => r.getAttribute("x"));
    });

    // The chart's own time scale changed (a real pan/zoom would do this) —
    // every timestamp now projects 500px further right...
    timeToXImpl.mockImplementation((ms: number) => ms / 1000 + 500);
    // ...but "is now visible" reports the exact same value it already held.
    expect(capturedCandleChartProps.onNowVisibleChange).toBeTypeOf("function");
    act(() => { capturedCandleChartProps.onNowVisibleChange(false); });

    await waitFor(() => {
      const rectsAfter = Array.from(document.querySelectorAll("svg.regime-overlay rect"))
        .map((r) => r.getAttribute("x"));
      expect(rectsAfter).not.toEqual(rectsBefore);
      expect(rectsAfter.every((x) => Number(x) >= 500)).toBe(true);
    });
  });
});
