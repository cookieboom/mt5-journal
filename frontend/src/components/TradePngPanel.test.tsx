import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import TradePngPanel from "./TradePngPanel";
import { DEFAULT_TRADE_PNG } from "../lib/tradePngPrefs";

describe("TradePngPanel", () => {
  it("emits a clamped padBars and a theme change", () => {
    const onChange = vi.fn();
    render(<TradePngPanel settings={DEFAULT_TRADE_PNG} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /render settings/i }));
    fireEvent.change(screen.getByLabelText(/context bars/i), { target: { value: "999" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ padBars: 120 }));
    fireEvent.change(screen.getByLabelText(/theme/i), { target: { value: "nightclouds" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ theme: "nightclouds" }));
  });
});
