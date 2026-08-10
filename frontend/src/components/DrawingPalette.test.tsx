import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import DrawingPalette from "./DrawingPalette";

describe("DrawingPalette", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reports the tool that was clicked", () => {
    const onTool = vi.fn();
    render(<DrawingPalette tool="cursor" onTool={onTool} onClearAll={() => {}} count={0} />);
    fireEvent.click(screen.getByRole("button", { name: "trendline" }));
    expect(onTool).toHaveBeenCalledWith("trend");
  });

  it("marks the active tool as pressed", () => {
    render(<DrawingPalette tool="rect" onTool={() => {}} onClearAll={() => {}} count={0} />);
    expect(screen.getByRole("button", { name: "kotak" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "kursor" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("hides clear-all when there is nothing to clear", () => {
    render(<DrawingPalette tool="cursor" onTool={() => {}} onClearAll={() => {}} count={0} />);
    expect(screen.queryByRole("button", { name: /hapus semua/i })).toBeNull();
  });

  it("requires a second click to clear all", () => {
    const onClearAll = vi.fn();
    render(<DrawingPalette tool="cursor" onTool={() => {}} onClearAll={onClearAll} count={3} />);
    const btn = screen.getByRole("button", { name: /hapus semua/i });
    fireEvent.click(btn);
    expect(onClearAll).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /yakin/i }));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  it("auto-expires the confirmation state after 3 seconds", () => {
    const onClearAll = vi.fn();
    render(<DrawingPalette tool="cursor" onTool={() => {}} onClearAll={onClearAll} count={3} />);
    const btn = screen.getByRole("button", { name: /hapus semua/i });
    // First click arms the confirm
    fireEvent.click(btn);
    expect(screen.getByRole("button", { name: /yakin/i })).toBeInTheDocument();
    // Advance past the 3-second window
    act(() => { vi.advanceTimersByTime(3001); });
    // Button should return to neutral (expired)
    expect(screen.getByRole("button", { name: /hapus semua/i })).toBeInTheDocument();
    // Click again — now it re-arms instead of clearing
    fireEvent.click(screen.getByRole("button", { name: /hapus semua/i }));
    expect(screen.getByRole("button", { name: /yakin/i })).toBeInTheDocument();
    expect(onClearAll).not.toHaveBeenCalled();
  });
});
