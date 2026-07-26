import { describe, it, expect } from "vitest";
import { tradeLines, navNeighbors } from "./tradeView";
import type { TradeFull, TradeRow } from "./types";

const base: TradeFull = {
  position_id: 2, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
  status: "closed", open_time_msc: 10, close_time_msc: 20, duration_s: 10,
  volume: 0.1, open_price: 4000, close_price: 4010, sl_initial: 3990,
  tp_initial: null, net_profit: 100, r_multiple: 1, mae_r: -0.2, mfe_r: 1.1, magic: null,
};

describe("tradeLines", () => {
  it("draws entry/exit/SL, skips null TP (rule 4)", () => {
    const titles = tradeLines(base).map((l) => l.title);
    expect(titles).toEqual(expect.arrayContaining(["entry", "exit", "SL"]));
    expect(titles).not.toContain("TP");
  });
  it("skips a 0.0 SL (none set, not a real price)", () => {
    expect(tradeLines({ ...base, sl_initial: 0 }).map((l) => l.title)).not.toContain("SL");
  });
});

describe("navNeighbors", () => {
  const rows = [3, 2, 1].map((position_id) => ({ position_id } as TradeRow)); // newest-first
  it("maps older=prev, newer=next", () => {
    expect(navNeighbors(rows, 2)).toEqual({ index: 1, prevId: 1, nextId: 3 });
    expect(navNeighbors(rows, 3)).toEqual({ index: 0, prevId: 2, nextId: null });
    expect(navNeighbors(rows, 1)).toEqual({ index: 2, prevId: null, nextId: 2 });
  });
  it("treats an unknown id as a singleton", () => {
    expect(navNeighbors(rows, 99)).toEqual({ index: -1, prevId: null, nextId: null });
  });
});
