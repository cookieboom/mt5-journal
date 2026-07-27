// Pure helpers for the price-measurement gesture (Spec B). No chart access here.
// Time is epoch-ms (broker server, UTC). Price/pct are unit-free ratios of price.

export interface Point {
  price: number;      // exact price at the pointer (from coordinateToPrice)
  barTimeMs: number;  // time of the bar under the pointer (gap-aware Δtime)
  logical: number;    // fractional bar index (from coordinateToLogical)
}

export interface MeasureMetrics {
  dPrice: number;         // cursor.price - anchor.price
  pct: number | null;     // null when anchor.price == 0 (guard divide-by-zero)
  bars: number;           // rounded |Δlogical|
  dTimeMs: number;        // |Δ bar time|, gap-aware
  up: boolean;            // dPrice >= 0
}

export function computeMetrics(anchor: Point, cursor: Point): MeasureMetrics {
  const dPrice = cursor.price - anchor.price;
  const pct = Math.abs(anchor.price) < 1e-9 ? null : (dPrice / anchor.price) * 100;
  const bars = Math.round(Math.abs(cursor.logical - anchor.logical));
  const dTimeMs = Math.abs(cursor.barTimeMs - anchor.barTimeMs);
  return { dPrice, pct, bars, dTimeMs, up: dPrice >= 0 };
}

// Human span for the measurement readout. Unlike format.ts:dur (mirrors Python,
// caps at hours), a chart measurement can cross days, so this adds a day bucket.
export function fmtSpan(ms: number): string {
  const totalMin = Math.floor(ms / 60_000);
  const days = Math.floor(totalMin / 1440);
  const hours = Math.floor((totalMin % 1440) / 60);
  const mins = totalMin % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export type MeasureState =
  | { phase: "idle" }
  | { phase: "measuring"; anchor: Point; cursor: Point }
  | { phase: "frozen"; anchor: Point; cursor: Point };

export type MeasureEvent =
  | { t: "start"; anchor: Point }
  | { t: "move"; cursor: Point }
  | { t: "release" }
  | { t: "clear" };

export const IDLE: MeasureState = { phase: "idle" };

// Pure transition. `move`/`release` outside `measuring` are no-ops; `start`
// always (re)opens a fresh measurement; `clear` always returns to idle.
export function measureReducer(s: MeasureState, e: MeasureEvent): MeasureState {
  switch (e.t) {
    case "start":
      return { phase: "measuring", anchor: e.anchor, cursor: e.anchor };
    case "move":
      return s.phase === "measuring" ? { ...s, cursor: e.cursor } : s;
    case "release":
      return s.phase === "measuring"
        ? { phase: "frozen", anchor: s.anchor, cursor: s.cursor }
        : s;
    case "clear":
      return IDLE;
  }
}

export const DBLCLICK_MS = 350;
export const DBLCLICK_PX = 5;

// The second mousedown of a double-click, held: within DBLCLICK_MS of the last
// pointerup and within DBLCLICK_PX of where it happened.
export function isDoubleClickHold(
  prevUpMs: number | null, prevX: number, prevY: number,
  nowMs: number, nowX: number, nowY: number,
): boolean {
  if (prevUpMs === null) return false;
  if (nowMs - prevUpMs > DBLCLICK_MS) return false;
  return Math.hypot(nowX - prevX, nowY - prevY) < DBLCLICK_PX;
}
