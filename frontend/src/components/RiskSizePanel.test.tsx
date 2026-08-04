import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RiskSizePanel from "./RiskSizePanel";
import type { RiskPrefs, SizeResult } from "../lib/types";

const PREFS: RiskPrefs = { mode: "pct", value: 1 };
const OK: SizeResult = {
  volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: 2,
  direction: "buy", error: null,
};

function setup(over: Partial<React.ComponentProps<typeof RiskSizePanel>> = {}) {
  const onSubmit = vi.fn();
  const onPrefsChange = vi.fn();
  const onSlChange = vi.fn();
  render(
    <RiskSizePanel
      disabled={false} currency="USC" prefs={PREFS} onPrefsChange={onPrefsChange}
      entry={4035} sl={4030} tp={4045}
      onSlChange={onSlChange} onTpChange={vi.fn()}
      result={OK} loading={false} onSubmit={onSubmit}
      {...over}
    />,
  );
  return { onSubmit, onPrefsChange, onSlChange };
}

describe("RiskSizePanel", () => {
  it("labels the action button from the derived direction", () => {
    setup();
    expect(screen.getByRole("button", { name: /buy/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /sell/i })).toBeNull();
  });

  it("labels it Sell when the stop sits above the price", () => {
    setup({ sl: 4040, result: { ...OK, direction: "sell" } });
    expect(screen.getByRole("button", { name: /sell/i })).toBeTruthy();
  });

  it("shows the lot, the realised risk in the account currency, and R:R", () => {
    setup();
    expect(screen.getByTestId("lot").textContent).toContain("0.13");
    const risk = screen.getByTestId("risk").textContent ?? "";
    expect(risk).toContain("65");
    expect(risk).toContain("USC");
    expect(risk).not.toContain("$");   // USC is not dollars (Trap 14)
    expect(screen.getByTestId("rr").textContent).toContain("2.00");
  });

  it("submits the server's volume and direction, never its own", () => {
    const { onSubmit } = setup();
    fireEvent.click(screen.getByRole("button", { name: /buy/i }));
    expect(onSubmit).toHaveBeenCalledWith({ direction: "buy", volume: 0.13 });
  });

  it("disables the action and shows the server's reason on a refusal", () => {
    setup({ result: { ...OK, volume: null, direction: null,
                      error: "Risiko 60.00 melebihi batas keras 5%" } });
    const btn = screen.getByRole("button", { name: /buka posisi/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("size-error").textContent).toContain("5%");
  });

  it("disables the action while no stop has been placed", () => {
    setup({ sl: null, result: null });
    const btn = screen.getByRole("button", { name: /buka posisi/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("size-hint").textContent).toMatch(/SL/i);
  });

  it("switching the risk mode reports the new prefs upward", () => {
    const { onPrefsChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "USC" }));
    expect(onPrefsChange).toHaveBeenCalledWith({ mode: "usc", value: 1 });
  });

  it("typing an SL reports null when the field is cleared", () => {
    const { onSlChange } = setup();
    fireEvent.change(screen.getByLabelText(/SL/i), { target: { value: "" } });
    expect(onSlChange).toHaveBeenCalledWith(null);
  });
});
