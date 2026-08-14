// Pure drag/hit-test logic for chart-based SL/TP editing. No chart or DOM
// access here — mirrors lib/measure.ts's pure-logic/reducer style. The
// component (CandleChart) owns pixel<->price projection and calls into this
// module with already-resolved prices.

export interface DraggablePosition {
  id: number;
  direction: "buy" | "sell";
  entry_price: number | null;
  sl: number;   // 0 = none set (rule 4)
  tp: number;   // 0 = none set (rule 4)
}

export type LineKind = "entry" | "sl" | "tp";

export const HIT_THRESHOLD_PX = 8;

// The `positionId` used for an order that does not exist yet. Real position ids
// are MT5 tickets — always positive — so a negative sentinel can never collide
// with one, and the whole existing hit-test/drag/ghost machinery works on a
// planned line with no branching inside it.
export const PLANNED_ID = -1;

// When dragging FROM the entry line (no sl/tp set yet), decide whether the
// dragged-to price should become the SL or the TP, based on direction and
// which side of entry the price landed on. If entry_price is unknown, this
// can't be resolved meaningfully — defaults to "sl" (callers should only
// invoke this for an entry-line drag, where entry_price is always known;
// the null case exists purely so the function total, not partial).
export function resolveDragTarget(pos: DraggablePosition, price: number): "sl" | "tp" {
  if (pos.entry_price === null) return "sl";
  const above = price > pos.entry_price;
  if (pos.direction === "buy") return above ? "tp" : "sl";
  return above ? "sl" : "tp";
}

// Title of a committed planned SL/TP line. Carries the distance to the price
// the chart is showing now, so the human reads the exact gap off the line the
// moment a drag ends instead of subtracting two axis labels by eye. Unsigned —
// the side is already obvious from where the line sits. 5 decimals, matching
// ghostTitle below, so the number does not change shape when the drag commits.
export function plannedTitle(
  kind: "sl" | "tp", price: number, currentPrice: number | null,
): string {
  const label = kind === "sl" ? "SL rencana" : "TP rencana";
  if (currentPrice === null) return label;
  return `${label} ${Math.abs(price - currentPrice).toFixed(5)}`;
}

// Ghost-line title while dragging: signed distance from entry, 5 decimals
// (matches lib/format.ts::price()'s full-precision philosophy — never round
// away a price digit). Falls back to the bare price if entry is unknown.
export function ghostTitle(kind: "sl" | "tp", entryPrice: number | null, price: number): string {
  const label = kind.toUpperCase();
  if (entryPrice === null) return `${label} → ${price.toFixed(5)}`;
  const distance = price - entryPrice;
  const sign = distance >= 0 ? "+" : "";
  return `${label} → ${sign}${distance.toFixed(5)}`;
}
