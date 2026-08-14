import { describe, it, expect } from "vitest";
import { resolveDragTarget, ghostTitle, plannedTitle, positionTitle, HIT_THRESHOLD_PX, type DraggablePosition } from "./sltpDrag";

const buyPos: DraggablePosition = { id: 1, direction: "buy", entry_price: 100, sl: 0, tp: 0 };
const sellPos: DraggablePosition = { id: 2, direction: "sell", entry_price: 100, sl: 0, tp: 0 };

describe("resolveDragTarget", () => {
  it("buy: price below entry resolves to sl", () => {
    expect(resolveDragTarget(buyPos, 95)).toBe("sl");
  });
  it("buy: price above entry resolves to tp", () => {
    expect(resolveDragTarget(buyPos, 105)).toBe("tp");
  });
  it("sell: price above entry resolves to sl", () => {
    expect(resolveDragTarget(sellPos, 105)).toBe("sl");
  });
  it("sell: price below entry resolves to tp", () => {
    expect(resolveDragTarget(sellPos, 95)).toBe("tp");
  });
  it("no entry_price known: defaults to sl (caller must not rely on this for entry-drag)", () => {
    const pending: DraggablePosition = { ...buyPos, entry_price: null };
    expect(resolveDragTarget(pending, 95)).toBe("sl");
  });
});

describe("positionTitle", () => {
  it("names the entry line without the position id", () => {
    expect(positionTitle("entry", 105, 105)).toBe("entry");
  });
  it("says the SL's unsigned distance from entry, 2 decimals", () => {
    expect(positionTitle("sl", 4030.125, 4035)).toBe("SL 4.88");
  });
  it("says the TP's unsigned distance from entry, 2 decimals", () => {
    expect(positionTitle("tp", 4045.5, 4035)).toBe("TP 10.50");
  });
  it("says the label alone when entry is unknown (rule 4)", () => {
    expect(positionTitle("sl", 4030, null)).toBe("SL");
  });
  it("says the label alone when entry is 0 = none set (rule 4)", () => {
    expect(positionTitle("tp", 4045, 0)).toBe("TP");
  });
});

describe("ghostTitle", () => {
  it("shows signed distance from entry for sl", () => {
    expect(ghostTitle("sl", 100, 95)).toBe("SL → -5.00");
  });
  it("shows signed distance from entry for tp with positive sign", () => {
    expect(ghostTitle("tp", 100, 105)).toBe("TP → +5.00");
  });
  it("falls back to bare price when entry is unknown", () => {
    expect(ghostTitle("sl", null, 95)).toBe("SL → 95.00000");
  });
});

describe("plannedTitle", () => {
  it("carries the unsigned distance to the current price for sl", () => {
    expect(plannedTitle("sl", 4030, 4035)).toBe("SL rencana 5.00");
  });
  it("carries the unsigned distance to the current price for tp", () => {
    expect(plannedTitle("tp", 4045.5, 4035)).toBe("TP rencana 10.50");
  });
  it("says the label alone when the current price is unknown", () => {
    expect(plannedTitle("sl", 4030, null)).toBe("SL rencana");
  });
});

describe("HIT_THRESHOLD_PX", () => {
  it("is a small pixel tolerance", () => {
    expect(HIT_THRESHOLD_PX).toBe(8);
  });
});
