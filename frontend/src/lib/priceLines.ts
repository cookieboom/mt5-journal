// Which horizontal price lines the chart draws, in which order — decided here,
// pure, so CandleChart's effect only has to apply the list to the series.
import { LINE_COLORS, liveLines } from "./candles";
import { PLANNED_ID, plannedTitle, positionTitle, type DraggablePosition, type LineKind } from "./sltpDrag";
import type { LiveData, PlannedOrder, PriceLineSpec } from "./types";

export interface LineMeta {
  positionId: number;
  kind: LineKind;
  direction: "buy" | "sell";
  entryPrice: number | null;
}

/** One line to draw. `meta` present = a position level the SL/TP drag can grab. */
export interface LineToDraw {
  price: number;
  color: string;
  title: string;
  meta?: LineMeta;
}

export interface PriceLineInput {
  draggablePositions?: DraggablePosition[];
  overlayLines?: PriceLineSpec[];
  liveOverlay: boolean;
  nowVisible: boolean;
  live: LiveData | null;
  symbol: string;
  plannedOrder?: PlannedOrder | null;
  entryTitle: string;
}

export function priceLinesToDraw(i: PriceLineInput): LineToDraw[] {
  const out: LineToDraw[] = [];
  const level = (positionId: number, kind: LineKind, price: number | null, color: string,
                 title: string, direction: "buy" | "sell", entryPrice: number | null) => {
    if (price === null || price === undefined || Math.abs(price) < 1e-9) return;
    out.push({ price, color, title, meta: { positionId, kind, direction, entryPrice } });
  };

  // Position lines first, from exactly one of three sources:
  // (1) draggable positions (replay, paper) — the caller says which;
  if (i.draggablePositions !== undefined) {
    for (const pos of i.draggablePositions) {
      level(pos.id, "entry", pos.entry_price, LINE_COLORS.entry,
            positionTitle("entry", pos.entry_price ?? 0, pos.entry_price), pos.direction, pos.entry_price);
      level(pos.id, "sl", pos.sl, LINE_COLORS.sl,
            positionTitle("sl", pos.sl, pos.entry_price), pos.direction, pos.entry_price);
      level(pos.id, "tp", pos.tp, LINE_COLORS.tp,
            positionTitle("tp", pos.tp, pos.entry_price), pos.direction, pos.entry_price);
    }
  // (2) explicit, non-draggable lines (older replay callers, static views);
  } else if (i.overlayLines !== undefined) {
    for (const line of i.overlayLines) out.push({ price: line.price, color: line.color, title: line.title });
  // (3) live positions on this symbol, only while "now" is in view: horizontal
  // lines have no time, so they would otherwise hang over history where those
  // levels never existed.
  } else if (i.liveOverlay && i.nowVisible && i.live && !i.live.live.empty) {
    for (const pos of i.live.live.positions.filter((p) => p.symbol === i.symbol)) {
      for (const line of liveLines(pos)) {
        level(pos.position_id, line.kind, line.price, line.color, line.title, pos.direction, pos.open_price);
      }
    }
  }
  const hasPositionLines = out.length > 0;

  // The planned order draws LAST: lightweight-charts paints axis labels in
  // creation order, and a plan sharing a price with a position would otherwise
  // have its label buried. `direction` is null until the stop picks a side.
  if (i.plannedOrder) {
    const p = i.plannedOrder;
    const dir = p.direction ?? "buy";
    level(PLANNED_ID, "entry", p.entry, LINE_COLORS.entry, i.entryTitle, dir, p.entry);
    // Planned stops vanish once a position is on the chart: the plan has been
    // acted on, and the levels that govern real money are the position's own.
    // The entry line stays — it is where price is now, and carries the countdown.
    if (!hasPositionLines) {
      if (p.sl !== null) level(PLANNED_ID, "sl", p.sl, LINE_COLORS.sl, plannedTitle("sl", p.sl, p.entry), dir, p.entry);
      if (p.tp !== null) level(PLANNED_ID, "tp", p.tp, LINE_COLORS.tp, plannedTitle("tp", p.tp, p.entry), dir, p.entry);
    }
  }
  return out;
}
