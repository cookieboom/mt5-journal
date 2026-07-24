import { ChartTrade } from "../lib/types";
import { calendarCells } from "../lib/charts";
import { money, wib } from "../lib/format";

export default function CalendarHeatmap(
  { series, currency, offsetS }: { series: ChartTrade[]; currency: string; offsetS: number },
) {
  const cells = calendarCells(series);
  const maxAbs = Math.max(1, ...cells.map((c) => Math.abs(c.net)));
  return (
    <div className="glass p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted">Kalender P&amp;L harian</h2>
        <span className="text-[11px] text-muted">{cells.length} hari · net dalam {currency}</span>
      </div>
      {cells.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center">Belum ada trade tertutup.</div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {cells.map((c) => {
            const alpha = 0.18 + 0.72 * (Math.abs(c.net) / maxAbs);
            const bg = c.net >= 0
              ? `rgba(52,211,153,${alpha})` : `rgba(251,113,133,${alpha})`;
            const day = wib(c.day_ms, offsetS).slice(0, 10); // date part only
            return (
              <div key={c.day_ms} className="w-9 h-9 rounded flex items-center justify-center text-[8.5px] num text-ink"
                   style={{ backgroundColor: bg }}
                   title={`${day}: ${money(c.net, currency, { sign: true })} · ${c.n} trade`}>
                {c.n}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
