import type { LabBarScore, Regime } from "./types";

export type Band = { from: number; to: number; regime: Regime };

/** Consecutive bars sharing a regime become one band, so the overlay draws a
 *  handful of rectangles instead of one per bar. Bands tile contiguously: a
 *  closing band's `to` extends to the time_msc of the FIRST bar of the next
 *  regime (the boundary), not just the last bar that shared its own regime —
 *  otherwise a single-bar band would leave a gap up to the next band's `from`. */
export function toBands(bars: LabBarScore[]): Band[] {
  if (bars.length === 0) return [];
  const sorted = [...bars].sort((a, b) => a.time_msc - b.time_msc);
  const out: Band[] = [];
  let current: Band = {
    from: sorted[0].time_msc, to: sorted[0].time_msc, regime: sorted[0].regime,
  };
  for (const bar of sorted.slice(1)) {
    if (bar.regime === current.regime) {
      current.to = bar.time_msc;
    } else {
      current.to = bar.time_msc; // extend to the boundary before closing it out
      out.push(current);
      current = { from: bar.time_msc, to: bar.time_msc, regime: bar.regime };
    }
  }
  out.push(current);
  return out;
}
