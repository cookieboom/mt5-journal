import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import CoverageShadeOverlay from "./CoverageShadeOverlay";
import type { Segment } from "../lib/coverage";

// A fake projector maps ms→px linearly so the test is deterministic without a real chart.
const project = (ms: number) => ms / 1000;
const segs: Segment[] = [
  { from: 0, to: 300_000, kind: "unfetched" },
  { from: 300_001, to: 600_000, kind: "covered" },
];

describe("CoverageShadeOverlay", () => {
  it("renders one band per non-covered segment", () => {
    const { container } = render(
      <CoverageShadeOverlay segments={segs} project={project} height={200} />,
    );
    // only the unfetched segment is painted (covered is skipped)
    expect(container.querySelectorAll("[data-shade]").length).toBe(1);
  });
});
