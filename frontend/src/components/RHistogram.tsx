import { ChartTrade } from "../lib/types";
import { histogramBins, rValues } from "../lib/charts";

export default function RHistogram({ series }: { series: ChartTrade[] }) {
  const values = rValues(series);
  const bins = histogramBins(values);
  const max = Math.max(1, ...bins.map((b) => b.count));
  const thin = values.length < 20;
  return (
    <div className="glass p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted">Distribusi R</h2>
        <span className={"text-[11px] " + (thin ? "text-muted/60" : "text-muted")}>
          n={values.length}{thin ? " (perlu ≥20)" : ""}
        </span>
      </div>
      {values.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center">Belum ada trade dengan R diketahui.</div>
      ) : (
        <div className={"flex items-end gap-1.5 h-[140px] " + (thin ? "opacity-60" : "")}>
          {bins.map((b) => (
            <div key={b.label} className="flex-1 flex flex-col items-center justify-end gap-1">
              <span className="text-[10px] num text-muted">{b.count || ""}</span>
              <div className={"w-full rounded-t " + (b.from >= 0 ? "bg-cyan/70" : "bg-violet/60")}
                   style={{ height: `${(b.count / max) * 100}%` }} title={`${b.label}: ${b.count}`} />
              <span className="text-[8.5px] num text-muted whitespace-nowrap">{b.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
