import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MeasureOverlay from "./MeasureOverlay";
import type { MeasureMetrics } from "../lib/measure";

const metrics: MeasureMetrics = { dPrice: 12.34, pct: 0.617, bars: 9, dTimeMs: 8100_000, up: true };

describe("MeasureOverlay", () => {
  it("renders the label with Δprice, %, bars and span", () => {
    render(
      <MeasureOverlay
        anchor={{ x: 10, y: 200 }}
        cursor={{ x: 120, y: 60 }}
        metrics={metrics}
        upColor="#34d399"
        downColor="#fb7185"
      />,
    );
    const label = screen.getByTestId("measure-label");
    expect(label.textContent).toContain("12.34");
    expect(label.textContent).toContain("%");
    expect(label.textContent).toContain("9 bars");
    expect(label.textContent).toContain("2h 15m");
  });

  it("shows a dash for pct when null (zero anchor guard)", () => {
    render(
      <MeasureOverlay
        anchor={{ x: 0, y: 0 }}
        cursor={{ x: 50, y: 50 }}
        metrics={{ dPrice: 5, pct: null, bars: 1, dTimeMs: 60_000, up: true }}
        upColor="#34d399"
        downColor="#fb7185"
      />,
    );
    expect(screen.getByTestId("measure-label").textContent).toContain("—");
  });

  it("uses downColor when up=false", () => {
    const { container } = render(
      <MeasureOverlay
        anchor={{ x: 10, y: 10 }}
        cursor={{ x: 20, y: 40 }}
        metrics={{ ...metrics, dPrice: -3, up: false }}
        upColor="#34d399"
        downColor="#fb7185"
      />,
    );
    // the connecting line is stroked with the direction colour
    const line = container.querySelector('[data-testid="measure-line"]');
    expect(line?.getAttribute("stroke")).toBe("#fb7185");
  });
});
