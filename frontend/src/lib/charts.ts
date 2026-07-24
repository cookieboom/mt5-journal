// Pure geometry helpers for the /report analytics charts. Client computes
// binning/bucketing from the raw server series (rule: charts render client-side
// from raw numbers). null R/MAE/MFE trades are filtered by the caller BEFORE
// these run — these never see nulls and never invent a 0.

const BIN_EDGES = [-Infinity, -2, -1, 0, 1, 2, 3, Infinity];

export function histogramBins(
  values: number[],
): { from: number; to: number; label: string; count: number }[] {
  const bins = BIN_EDGES.slice(0, -1).map((from, i) => {
    const to = BIN_EDGES[i + 1];
    const label =
      from === -Infinity ? `(-∞,${to})`
      : to === Infinity ? `[${from},∞)`
      : `[${from},${to})`;
    return { from, to, label, count: 0 };
  });
  for (const v of values) {
    // left-closed, right-open: find the bin where from <= v < to.
    const idx = bins.findIndex((b) => v >= b.from && v < b.to);
    if (idx >= 0) bins[idx].count += 1;
  }
  return bins;
}

export function dayStartUtcMs(msc: number): number {
  const DAY = 86_400_000;
  return Math.floor(msc / DAY) * DAY;
}

export function calendarCells(
  series: { close_time_msc: number; net_profit: number }[],
): { day_ms: number; net: number; n: number }[] {
  const byDay = new Map<number, { day_ms: number; net: number; n: number }>();
  for (const t of series) {
    const day = dayStartUtcMs(t.close_time_msc);
    const cell = byDay.get(day) ?? { day_ms: day, net: 0, n: 0 };
    cell.net += t.net_profit;
    cell.n += 1;
    byDay.set(day, cell);
  }
  return [...byDay.values()].sort((a, b) => a.day_ms - b.day_ms);
}
