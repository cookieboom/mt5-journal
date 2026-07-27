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
