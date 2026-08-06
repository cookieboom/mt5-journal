import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LabBadge from "./LabBadge";
import type { LabScore } from "../lib/types";

const score = (over: Partial<LabScore> = {}): LabScore =>
  ({
    symbol: "XAUUSDc", timeframe: "M15", status: "ok",
    model_age_ms: 3 * 86_400_000, expectancy_r: 0.08, pooled: false,
    bars: [{ time_msc: 1, regime: "trend_up",
             regime_proba: { trend_up: 0.7, trend_down: 0.1, range: 0.2 },
             p_tp_long: 0.62, p_tp_short: 0.31 }],
    ...over,
  } as LabScore);

describe("LabBadge", () => {
  it("shows the regime and both probabilities", () => {
    render(<LabBadge score={score()} />);
    expect(screen.getByText(/trend up/i)).toBeInTheDocument();
    expect(screen.getByText(/62%/)).toBeInTheDocument();
    expect(screen.getByText(/31%/)).toBeInTheDocument();
  });

  it("always shows model age and out-of-sample expectancy next to them", () => {
    render(<LabBadge score={score()} />);
    expect(screen.getByText(/3d ago/)).toBeInTheDocument();
    expect(screen.getByText(/\+0\.08R/)).toBeInTheDocument();
  });

  it("marks a model older than 30 days as stale", () => {
    render(<LabBadge score={score({ model_age_ms: 40 * 86_400_000 })} />);
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
  });

  it("shows no probability at all when there is no model", () => {
    render(<LabBadge score={score({ status: "no_model", bars: [] })} />);
    expect(screen.getByText(/no model/i)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("asks for a retrain when the artifact is gone", () => {
    render(<LabBadge score={score({ status: "artifact_missing", bars: [] })} />);
    expect(screen.getByText(/retrain/i)).toBeInTheDocument();
  });

  it("says pooled when the timing model is not regime-specific", () => {
    render(<LabBadge score={score({ pooled: true })} />);
    expect(screen.getByText(/pooled/i)).toBeInTheDocument();
  });

  it("renders nothing but a placeholder while loading", () => {
    render(<LabBadge score={null} />);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});
