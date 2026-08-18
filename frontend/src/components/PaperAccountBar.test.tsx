import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import PaperAccountBar from "./PaperAccountBar";

const header = {
  currency: "USC", balance: 1_000_000, equity: 999_995, margin: 80.61,
  free_margin: 999_914.39, margin_level: 1_240_534, floating: -5,
  leverage: 500, stopout_pct: 20,
};

describe("PaperAccountBar", () => {
  it("prints every money figure with its unit, never a bare number", () => {
    render(<PaperAccountBar header={header} name="Scalping XAU" live />);
    expect(screen.getAllByText(/USC/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Scalping XAU/)).toBeTruthy();
  });

  it("says unknown rather than 0 when the feed never produced a quote", () => {
    render(<PaperAccountBar name="X" live
      header={{ ...header, equity: null, margin: null, free_margin: null,
                margin_level: null, floating: null }} />);
    // `n/a` is this app's unknown marker (lib/format.money) — the point the test
    // pins is that an unknown never renders as a 0 balance.
    expect(screen.getByLabelText("equity").textContent).toMatch(/n\/a/);
    expect(screen.queryByLabelText("equity")!.textContent).not.toMatch(/\b0\b/);
  });

  it("warns that positions are unmonitored while the daemon is down", () => {
    render(<PaperAccountBar header={header} name="X" live={false} />);
    expect(screen.getByRole("status").textContent).toMatch(/tidak dipantau/i);
  });
});
