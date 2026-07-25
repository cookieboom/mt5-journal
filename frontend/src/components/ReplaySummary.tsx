import type { TrainingSummary } from "../lib/replay";

// §8: n and total_r always show; rate/average metrics are greyed (—) when null.
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

export default function ReplaySummary(props: { title: string; s: TrainingSummary | null }) {
  const s = props.s;
  return (
    <div className="glass p-3 space-y-1 text-xs">
      <div className="font-semibold">{props.title}</div>
      {!s ? <div className="text-muted">—</div> : (
        <>
          <Metric label="n" value={s.n} />
          <Metric label="Win rate" value={s.win_rate} pct />
          <Metric label="Avg R" value={s.avg_r} suffix="R" />
          <Metric label="Total R" value={s.total_r} suffix="R" />
          <Metric label="Avg MAE" value={s.avg_mae_r} suffix="R" />
          <Metric label="Avg MFE" value={s.avg_mfe_r} suffix="R" />
          {s.n < 20 && <div className="text-muted/60 pt-1">n &lt; 20 — rasio disembunyikan (§8)</div>}
        </>
      )}
    </div>
  );
}
