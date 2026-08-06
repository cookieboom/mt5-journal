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

  // Text-only assertions above would pass even if every status rendered as
  // identical unstyled text — regression coverage for exactly that: the DOM
  // must mark an ok reading, a stale one, and a degraded one as provably
  // different, not merely say different words.
  it("marks an ok reading, a stale reading, and a degraded reading with different DOM status markers", () => {
    const { container: ok } = render(<LabBadge score={score()} />);
    const { container: stale } = render(<LabBadge score={score({ model_age_ms: 40 * 86_400_000 })} />);
    const { container: noModel } = render(<LabBadge score={score({ status: "no_model", bars: [] })} />);
    const { container: loading } = render(<LabBadge score={null} />);

    const statusOf = (c: HTMLElement) => c.querySelector("[data-status]")?.getAttribute("data-status");
    expect(statusOf(ok)).toBe("ok");
    expect(statusOf(stale)).toBe("stale");
    expect(statusOf(noModel)).toBe("no_model");
    expect(statusOf(loading)).toBe("loading");

    // Distinct markers imply distinct class lists (the actual styling bug):
    // an "ok" reading must not share its container class string with a
    // degraded one.
    const classesOf = (c: HTMLElement) => c.querySelector("[data-status]")?.className;
    expect(classesOf(ok)).not.toBe(classesOf(noModel));
    expect(classesOf(ok)).not.toBe(classesOf(stale));
  });
});
