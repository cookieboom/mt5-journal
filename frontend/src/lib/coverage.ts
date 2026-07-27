import type { Candle } from "./types";
import { timeframeMs, type Timeframe } from "./candles";

export type SegmentKind = "covered" | "unfetched" | "closed";
export type Segment = { from: number; to: number; kind: SegmentKind };

const overlapsMissing = (a: number, b: number, missing: [number, number][]) =>
  missing.some(([lo, hi]) => lo <= b && hi >= a);

// Walk the window in bar-sized steps, labelling each slot: covered if a bar
// opens in it (a slot that already has a rendered bar — e.g. the live forming
// bar — is not a hole the user must backfill, even if it overlaps a missing
// range); else unfetched if it overlaps a missing (uncovered) range; else
// closed (inside coverage but no bar = market shut). Adjacent equal-kind
// slots merge.
export function classifyGaps(
  bars: Candle[], missing: [number, number][],
  window: [number, number], tf: Timeframe,
): Segment[] {
  const size = timeframeMs(tf);
  const [lo, hi] = window;
  const present = new Set(bars.map((b) => b.time_msc - (b.time_msc % size)));
  const out: Segment[] = [];
  for (let t = lo - (lo % size); t <= hi; t += size) {
    const end = t + size - 1;
    const kind: SegmentKind = present.has(t)
      ? "covered"
      : overlapsMissing(t, end, missing) ? "unfetched" : "closed";
    const last = out[out.length - 1];
    if (last && last.kind === kind && last.to + 1 === t) last.to = end;
    else out.push({ from: t, to: end, kind });
  }
  return out;
}
