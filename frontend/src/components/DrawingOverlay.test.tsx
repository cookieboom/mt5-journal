import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import DrawingOverlay from "./DrawingOverlay";
import { projectDrawing, type ProjectCtx } from "../lib/drawings";

const candles = Array.from({ length: 10 }, (_, i) => ({ time_msc: 1_000_000 + i * 60_000 }));
const ctx: ProjectCtx = {
  width: 500,
  candles,
  logicalToX: (i) => i * 10,
  priceToY: (p) => 200 - (p - 100) * 10,
};

const trend = projectDrawing(
  { id: "t", kind: "trend", a: { timeMs: 1_000_000, price: 100 }, b: { timeMs: 1_240_000, price: 110 } },
  ctx,
);
const hline = projectDrawing({ id: "h", kind: "hline", price: 105 }, ctx);
const rect = projectDrawing(
  { id: "r", kind: "rect", a: { timeMs: 1_000_000, price: 110 }, b: { timeMs: 1_240_000, price: 100 } },
  ctx,
);
const text = projectDrawing(
  { id: "x", kind: "text", a: { timeMs: 1_060_000, price: 105 }, text: "supply" },
  ctx,
);

describe("DrawingOverlay", () => {
  it("renders one element per kind", () => {
    render(<DrawingOverlay projected={[trend, hline, rect, text]} selectedId={null} />);
    expect(screen.getByTestId("drawing-t").tagName.toLowerCase()).toBe("line");
    expect(screen.getByTestId("drawing-h").tagName.toLowerCase()).toBe("line");
    expect(screen.getByTestId("drawing-r").tagName.toLowerCase()).toBe("rect");
    expect(screen.getByTestId("drawing-x").textContent).toBe("supply");
  });

  it("places the trend line on its projected endpoints", () => {
    render(<DrawingOverlay projected={[trend]} selectedId={null} />);
    const line = screen.getByTestId("drawing-t");
    expect(line.getAttribute("x1")).toBe("0");
    expect(line.getAttribute("y1")).toBe("200");
    expect(line.getAttribute("x2")).toBe("40");
    expect(line.getAttribute("y2")).toBe("100");
  });

  it("normalises a rect drawn from any corner", () => {
    render(<DrawingOverlay projected={[rect]} selectedId={null} />);
    const r = screen.getByTestId("drawing-r");
    expect(r.getAttribute("x")).toBe("0");
    expect(r.getAttribute("y")).toBe("100");
    expect(r.getAttribute("width")).toBe("40");
    expect(r.getAttribute("height")).toBe("100");
  });

  it("shows handles only for the selected object", () => {
    const { rerender } = render(<DrawingOverlay projected={[trend]} selectedId={null} />);
    expect(screen.queryByTestId("handle-t-a")).toBeNull();
    rerender(<DrawingOverlay projected={[trend]} selectedId="t" />);
    expect(screen.getByTestId("handle-t-a")).toBeTruthy();
    expect(screen.getByTestId("handle-t-b")).toBeTruthy();
  });

  it("omits an object whose projection is null", () => {
    const off = projectDrawing(
      { id: "off", kind: "trend", a: { timeMs: 1, price: 100 }, b: { timeMs: 1_240_000, price: 110 } },
      ctx,
    );
    render(<DrawingOverlay projected={[off]} selectedId={null} />);
    expect(screen.queryByTestId("drawing-off")).toBeNull();
  });

  it("does not intercept pointer events", () => {
    const { container } = render(<DrawingOverlay projected={[trend]} selectedId={null} />);
    const svg = container.querySelector("svg")!;
    expect(svg.style.pointerEvents).toBe("none");
  });
});
