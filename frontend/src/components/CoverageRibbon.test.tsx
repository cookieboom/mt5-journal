import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import CoverageRibbon from "./CoverageRibbon";
import type { Candle } from "../lib/types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 1 } as unknown as Candle);

describe("CoverageRibbon", () => {
  it("reports the count of unfetched holes in view", () => {
    render(
      <CoverageRibbon bars={[bar(0)]} missing={[[300_000, 600_000]]}
        window={[0, 600_000]} tf="M5" />,
    );
    expect(screen.getByText(/1 lubang belum di-fetch/i)).toBeInTheDocument();
  });
  it("shows no-hole state when fully covered", () => {
    render(
      <CoverageRibbon bars={[bar(0), bar(300_000), bar(600_000)]} missing={[]}
        window={[0, 600_000]} tf="M5" />,
    );
    expect(screen.getByText(/lengkap/i)).toBeInTheDocument();
  });
});
