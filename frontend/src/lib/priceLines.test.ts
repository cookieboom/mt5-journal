import { describe, expect, it } from "vitest";
import { priceLinesToDraw, type PriceLineInput } from "./priceLines";
import { PLANNED_ID } from "./sltpDrag";
import type { LiveData, LivePosition } from "./types";

const base: PriceLineInput = {
  liveOverlay: true, nowVisible: true, live: null, symbol: "XAUUSDc",
  entryTitle: "harga",
};

function livePos(over: Partial<LivePosition>): LivePosition {
  return {
    position_id: 7, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy", volume: 0.1,
    open_price: 4000, price_current: 4001, sl: 3990, tp: 4020, profit: 1, observed_msc: 0, ...over,
  };
}

function live(positions: LivePosition[]): LiveData {
  return { header: {} as LiveData["header"], live: { positions, empty: positions.length === 0 } as LiveData["live"] };
}

const kinds = (lines: ReturnType<typeof priceLinesToDraw>) =>
  lines.map((l) => `${l.meta?.positionId ?? "-"}:${l.meta?.kind ?? "plain"}`);

describe("priceLinesToDraw", () => {
  it("draws a live position's entry/SL/TP, draggable, only for the chart's symbol", () => {
    const lines = priceLinesToDraw({ ...base, live: live([livePos({}), livePos({ position_id: 8, symbol: "BTCUSDc" })]) });
    expect(kinds(lines)).toEqual(["7:entry", "7:sl", "7:tp"]);
    expect(lines[1]).toMatchObject({ price: 3990, meta: { direction: "buy", entryPrice: 4000 } });
  });

  it("hides live lines when now is out of view or the overlay is off", () => {
    expect(priceLinesToDraw({ ...base, nowVisible: false, live: live([livePos({})]) })).toEqual([]);
    expect(priceLinesToDraw({ ...base, liveOverlay: false, live: live([livePos({})]) })).toEqual([]);
  });

  it("skips an unset level (0 means none set, rule 4)", () => {
    const lines = priceLinesToDraw({ ...base, draggablePositions: [
      { id: 3, direction: "sell", entry_price: 4000, sl: 0, tp: 3980 },
    ] });
    expect(kinds(lines)).toEqual(["3:entry", "3:tp"]);
  });

  it("draggable positions win over live, even when the list is empty", () => {
    expect(priceLinesToDraw({ ...base, draggablePositions: [], live: live([livePos({})]) })).toEqual([]);
  });

  it("explicit overlay lines are drawn as given and are not draggable", () => {
    const lines = priceLinesToDraw({ ...base, overlayLines: [{ price: 1, color: "#fff", title: "x" }] });
    expect(lines).toEqual([{ price: 1, color: "#fff", title: "x" }]);
  });

  it("draws the planned order last, and drops its stops once a position shows", () => {
    const plannedOrder = { entry: 4000, sl: 3990, tp: 4030, direction: null };
    expect(kinds(priceLinesToDraw({ ...base, plannedOrder })))
      .toEqual([`${PLANNED_ID}:entry`, `${PLANNED_ID}:sl`, `${PLANNED_ID}:tp`]);
    const withPos = priceLinesToDraw({ ...base, plannedOrder, live: live([livePos({})]) });
    expect(kinds(withPos)).toEqual(["7:entry", "7:sl", "7:tp", `${PLANNED_ID}:entry`]);
    expect(withPos[3].title).toBe("harga");
  });
});
