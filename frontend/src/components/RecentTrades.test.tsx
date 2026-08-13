// The dashboard's last-trades strip was a dead end: it named a time and a
// running total, and clicked through to nothing. Every row must reach its trade.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect } from "vitest";
import RecentTrades from "./RecentTrades";
import { Equity } from "../lib/types";

const svg = { empty: true, viewbox: "0 0 1 1", points: "", area: "", baseline_y: 1 };

function equity(series: Equity["series"]): Equity {
  return { n: series.length, n_with_r: 0, equity_last: null, r_last: null,
           series, equity_svg: svg, r_svg: svg };
}

const ROWS = [
  { close_time_msc: 1_700_000_000_000, equity: 5, position_id: 7, symbol_base: "XAUUSD" },
  { close_time_msc: 1_700_000_100_000, equity: 3, position_id: 8, symbol_base: "BTCUSD" },
];

describe("RecentTrades", () => {
  it("links every row to its trade by position_id", () => {
    render(<MemoryRouter><RecentTrades equity={equity(ROWS)} currency="USC" offsetS={0} /></MemoryRouter>);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/trades/8", "/trades/7"]);
    // newest first, and the link says which symbol it opens
    expect(links.map((a) => a.textContent)).toEqual(["BTCUSD", "XAUUSD"]);
  });

  it("says so plainly when nothing has closed yet", () => {
    render(<MemoryRouter><RecentTrades equity={equity([])} currency="USC" offsetS={0} /></MemoryRouter>);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText(/Belum ada trade tertutup/)).toBeInTheDocument();
  });
});
