import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ChartToolbar from "./ChartToolbar";
import { DEFAULT_SETTINGS } from "../lib/chartPrefs";

function setup(paperMode = false, onPaperMode = vi.fn()) {
  render(<ChartToolbar symbol="XAUUSDc" tf="M5" settings={DEFAULT_SETTINGS}
    onSymbol={vi.fn()} onTf={vi.fn()} onSettings={vi.fn()} onReset={vi.fn()}
    onJumpNow={vi.fn()} onReplay={vi.fn()} paperMode={paperMode}
    onPaperMode={onPaperMode} />);
  return onPaperMode;
}

describe("ChartToolbar paper toggle", () => {
  it("says which mode is active, out loud", () => {
    setup(true);
    expect(screen.getByRole("button", { name: /paper/i }).getAttribute("aria-pressed"))
      .toBe("true");
  });

  it("asks for the other mode when pressed", () => {
    const onPaperMode = setup(false);
    fireEvent.click(screen.getByRole("button", { name: /paper/i }));
    expect(onPaperMode).toHaveBeenCalledWith(true);
  });
});
