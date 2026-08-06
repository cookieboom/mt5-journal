import type { LabModel } from "../lib/types";
import { formatAge } from "../lib/lab";

const MIN_BUCKET_N = 20;   // CLAUDE.md §8, mirrored from lab/evaluate.py

/** A rate from fewer than 20 rows is noise with a decimal point. Render a dash
 *  rather than a number that invites a decision. */
function rate(value: number | null, n: number): string {
  if (value === null || value === undefined || n < MIN_BUCKET_N) return "—";
  return `${(value * 100).toFixed(0)}%`;
}

function r(value: number | null, n: number): string {
  if (value === null || value === undefined || n < MIN_BUCKET_N) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`;
}

const rowClass = (active: boolean) => "border-t border-white/5" + (active ? " bg-cyan/5" : "");
const naCell = <td className="py-2 num text-muted" title="not measured for a regime model">—</td>;

function ActivateCell({ model, onActivate }: { model: LabModel; onActivate: (id: number) => void }) {
  return (
    <td className="py-2">
      {model.active ? (
        <span className="px-1.5 py-0.5 rounded text-[10px] bg-cyan/15 text-cyan">active</span>
      ) : (
        <button
          className="px-2 py-1 rounded bg-white/5 ring-1 ring-panel-border text-ink text-[11px] hover:bg-white/10"
          onClick={() => onActivate(model.id)}
        >
          Activate
        </button>
      )}
    </td>
  );
}

// LabModel.metrics is a discriminated union on `stage` (lib/types.ts): a
// regime model's metrics carry no expectancy_r/auc/baseline_expectancy_r —
// the backend evaluates the 3-class classifier through a binary helper fed a
// constant probability, so those would always read as 0/0.5 filler. Narrow
// on `m.stage` before touching either shape; reading the wrong branch's
// fields is a compile error by design, not something to cast around.
function ModelRow({ model, onActivate }: { model: LabModel; onActivate: (id: number) => void }) {
  const n = model.metrics.n;
  const ageCell = <td className="py-2 whitespace-nowrap text-muted">{formatAge(Date.now() - model.created_ms)}</td>;
  const idCells = (
    <>
      <td className="py-2">{model.stage}</td>
      <td className="py-2">{model.pooled ? "pooled" : model.regime ?? "—"}</td>
      <td className="py-2">{model.kind}</td>
    </>
  );

  if (model.stage === "regime") {
    // No expectancy/baseline/AUC exist for a regime classifier — dash them
    // rather than fabricate a number. Accuracy stands in for "win".
    return (
      <tr className={rowClass(model.active)}>
        {idCells}
        {naCell}
        {naCell}
        <td className="py-2 num whitespace-nowrap">{rate(model.metrics.accuracy, model.metrics.n_taken)}</td>
        {naCell}
        <td className="py-2 num whitespace-nowrap">n = {n}</td>
        {ageCell}
        <ActivateCell model={model} onActivate={onActivate} />
      </tr>
    );
  }

  const m = model.metrics;
  return (
    <tr className={rowClass(model.active)}>
      {idCells}
      <td className="py-2 num whitespace-nowrap">{r(m.expectancy_r, m.n_taken)}</td>
      <td className="py-2 num whitespace-nowrap">{r(m.baseline_expectancy_r, n)}</td>
      <td className="py-2 num whitespace-nowrap">{rate(m.win_rate, m.n_taken)}</td>
      <td className="py-2 num">{m.auc?.toFixed(2) ?? "—"}</td>
      <td className="py-2 num whitespace-nowrap">n = {n}</td>
      {ageCell}
      <ActivateCell model={model} onActivate={onActivate} />
    </tr>
  );
}

export default function LabMetrics({
  models, onActivate,
}: {
  models: LabModel[];
  onActivate: (id: number) => void;
}) {
  if (models.length === 0) return <p className="text-muted text-sm py-6">No models trained yet.</p>;
  return (
    <div className="glass p-4 overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="text-muted text-left">
            {["stage", "regime", "model", "expectancy", "baseline", "win / acc", "AUC", "n", "age", ""].map((h) => (
              <th key={h} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {models.map((m) => <ModelRow key={m.id} model={m} onActivate={onActivate} />)}
        </tbody>
      </table>
    </div>
  );
}
