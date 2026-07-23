import { Report } from "../lib/types";
import { pct, rmult, isGated } from "../lib/format";

export default function SymbolBars({ report }: { report: Report }) {
  const rows = report.by_symbol;
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.avg_r ?? 0) * r.n));
  return (
    <div className="flex flex-col gap-3 mt-1">
      {rows.map((r) => {
        const gated = isGated(r.n, r.avg_r);
        const w = gated ? 0 : Math.min(100, (Math.abs((r.avg_r ?? 0) * r.n) / max) * 100);
        return (
          <div key={r.label}>
            <div className="flex justify-between text-[11.5px] mb-1.5">
              <b className="text-white font-semibold">{r.label}</b>
              <span className={gated ? "text-muted/60" : "text-muted"}>
                {gated ? `n=${r.n} (perlu ≥20)` : `${rmult(r.avg_r)} · ${pct(r.win_rate)}`}
              </span>
            </div>
            <div className="h-2 rounded bg-white/[0.06] overflow-hidden">
              <div className="h-full rounded bg-gradient-to-r from-violet to-cyan"
                   style={{ width: `${w}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
