import type { OutcomeCounts, TrainingSummary } from "../lib/replay";

// Replay summaries are ungated (no §8 sample floor — sessions are too small for
// it to ever pass). A metric is greyed (—) only when the backend had no input
// for it at all: no closed trades, or no SL so R is unknown (rule 4).
function Metric(props: { label: string; value: number | null; suffix?: string; pct?: boolean }) {
  const v = props.value;
  const text = v === null ? "—" : props.pct ? `${(v * 100).toFixed(0)}%` : `${v.toFixed(2)}${props.suffix ?? ""}`;
  return (
    <div className="flex justify-between">
      <span className="text-muted">{props.label}</span>
      <span className={v === null ? "text-muted/50" : ""}>{text}</span>
    </div>
  );
}

// How the closed positions ended — raw counts, never derived.
function Count(props: { label: string; value: number }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{props.label}</span>
      <span>{props.value}</span>
    </div>
  );
}

export default function ReplaySummary(props: {
  title: string; s: TrainingSummary | null; counts?: OutcomeCounts;
}) {
  const s = props.s;
  const c = props.counts;
  return (
    <div className="glass p-3 space-y-1 text-body">
      <div className="font-semibold">{props.title}</div>
      {!s ? <div className="text-muted">—</div> : (
        <>
          <Metric label="n" value={s.n} />
          <Metric label="Win rate" value={s.win_rate} pct />
          <Metric label="Avg R" value={s.avg_r} suffix="R" />
          <Metric label="Total R" value={s.total_r} suffix="R" />
          <Metric label="Avg MAE" value={s.avg_mae_r} suffix="R" />
          <Metric label="Avg MFE" value={s.avg_mfe_r} suffix="R" />
          {c && (
            <div className="pt-1 border-t border-white/10 space-y-1">
              <Count label="Kena TP" value={c.tp} />
              <Count label="Kena SL" value={c.sl} />
              <Count label="Manual/EOD" value={c.manual} />
            </div>
          )}
          {s.n > 0 && s.n < 20 && (
            <div className="text-muted/60 pt-1">n kecil — angka belum stabil</div>
          )}
        </>
      )}
    </div>
  );
}
