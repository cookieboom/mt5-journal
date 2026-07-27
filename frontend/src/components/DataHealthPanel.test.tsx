import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import DataHealthPanel from "./DataHealthPanel";
import type { Candle } from "../lib/types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 1 } as unknown as Candle);

describe("DataHealthPanel", () => {
  it("lists the number of unfetched holes and a backfill button", () => {
    render(
      <DataHealthPanel bars={[bar(0)]} missing={[[300_000, 600_000]]}
        window={[0, 600_000]} tf="M5" symbol="XAUUSDc" />,
    );
    expect(screen.getByRole("button", { name: /backfill/i })).toBeInTheDocument();
    // NOTE: brief's literal `getByText(/1/)` is ambiguous — wib()'s rendered
    // date string ("1970-01-01 07:05 WIB") also matches /1/, alongside the
    // "1 lubang belum di-fetch:" count text. Scoped to the count text so the
    // assertion is unambiguous while still checking the hole count renders.
    expect(screen.getByText(/1 lubang/i)).toBeInTheDocument();
  });
});
