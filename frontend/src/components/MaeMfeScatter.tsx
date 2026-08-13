import { ChartTrade } from "../lib/types";
import { maeMfePoints } from "../lib/charts";
import { white } from "../lib/theme";

export default function MaeMfeScatter({ series }: { series: ChartTrade[] }) {
  const pts = maeMfePoints(series);
  const W = 320, H = 200, pad = 28;
  const xs = pts.map((p) => p.mae_r);
  const ys = pts.map((p) => p.mfe_r);
  const xmin = Math.min(0, ...xs), xmax = Math.max(0, ...xs);
  const ymin = Math.min(0, ...ys), ymax = Math.max(0, ...ys);
  const xspan = xmax - xmin || 1, yspan = ymax - ymin || 1;
  const X = (v: number) => pad + (W - 2 * pad) * (v - xmin) / xspan;
  const Y = (v: number) => H - pad - (H - 2 * pad) * (v - ymin) / yspan;
  const thin = pts.length < 20;
  return (
    <div className="glass p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-title font-semibold uppercase tracking-wider text-muted">MAE vs MFE (R)</h2>
        <span className={"text-meta " + (thin ? "text-muted/60" : "text-muted")}>
          n={pts.length}{thin ? " (perlu ≥20)" : ""}
        </span>
      </div>
      {pts.length === 0 ? (
        <div className="text-muted text-body py-8 text-center">Belum ada trade dengan MAE &amp; MFE (perlu candle + SL).</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className={"w-full h-[200px] " + (thin ? "opacity-60" : "")}>
          <line x1={X(0)} y1={pad} x2={X(0)} y2={H - pad} stroke={white(0.15)} strokeWidth="1" />
          <line x1={pad} y1={Y(0)} x2={W - pad} y2={Y(0)} stroke={white(0.15)} strokeWidth="1" />
          {pts.map((p) => (
            <circle key={p.position_id} cx={X(p.mae_r)} cy={Y(p.mfe_r)} r="3.5"
                    className={p.net_profit >= 0 ? "fill-pos/80" : "fill-neg/80"}>
              <title>#{p.position_id} {p.symbol_base}: MAE {p.mae_r}R, MFE {p.mfe_r}R</title>
            </circle>
          ))}
          <text x={W - pad} y={Y(0) - 4} textAnchor="end" className="fill-muted text-label">MAE →</text>
          <text x={X(0) + 4} y={pad + 8} className="fill-muted text-label">MFE ↑</text>
        </svg>
      )}
    </div>
  );
}
