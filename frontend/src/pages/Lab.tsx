import { useCallback, useEffect, useState } from "react";
import LabMetrics from "../components/LabMetrics";
import {
  DEFAULT_TRAIN_FORM,
  LAB_FEATURES,
  activateModel,
  fetchModels,
  trainModels,
  type TrainForm,
  type TrainResponse,
} from "../lib/lab";
import type { LabModel } from "../lib/types";

const TIMEFRAMES = ["M1", "M5", "M15", "H1"];
const fieldText = "bg-white/5 rounded px-2 py-1 text-ink w-full";
const field = fieldText + " num";

// Display-only override: the raw feature key is "spread" (sent to the
// backend unchanged via form.features), but rendering that word as its own
// visible label collides with the dropped-feature warning below, which also
// names "spread" — vitest's text matcher then finds two elements for one
// query. Rename the label only; the wire value is untouched.
const FEATURE_LABELS: Record<string, string> = { spread: "bid/ask cost" };

export default function Lab() {
  const [form, setForm] = useState<TrainForm>(DEFAULT_TRAIN_FORM);
  const [models, setModels] = useState<LabModel[]>([]);
  const [result, setResult] = useState<TrainResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const { models } = await fetchModels(form.symbol, form.timeframe);
    setModels(models);
  }, [form.symbol, form.timeframe]);

  useEffect(() => { void reload(); }, [reload]);

  const setNum = (key: keyof TrainForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: Number(e.target.value) }));

  const toggleFeature = (name: string) =>
    setForm((f) => ({
      ...f,
      features: f.features.includes(name)
        ? f.features.filter((x) => x !== name)
        : [...f.features, name],
    }));

  const onTrain = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await trainModels(form));
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onActivate = async (id: number) => {
    await activateModel(id);
    await reload();
  };

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Lab</h1>
      <p className="text-[12px] text-muted mb-4 max-w-[60ch]">
        Models trained here predict, on candle data only — nothing here places,
        sizes, or recommends a trade. Every number below is out-of-sample and
        net of trading cost; a model is only interesting where it beats the baseline.
      </p>

      <section className="glass p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[12px]">
          <label className="flex flex-col gap-1 text-muted">Symbol
            <input className={fieldText} value={form.symbol}
                   onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value }))} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Timeframe
            <select className={fieldText} value={form.timeframe}
                    onChange={(e) => setForm((f) => ({ ...f, timeframe: e.target.value }))}>
              {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-muted">Bars ahead (N)
            <input className={field} type="number" value={form.n_bars} onChange={setNum("n_bars")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Risk (k × ATR)
            <input className={field} type="number" step="0.1" value={form.k_atr} onChange={setNum("k_atr")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Reward ratio
            <input className={field} type="number" step="0.1" value={form.rr} onChange={setNum("rr")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Regime threshold
            <input className={field} type="number" step="0.05" value={form.er_threshold} onChange={setNum("er_threshold")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Folds
            <input className={field} type="number" value={form.n_folds} onChange={setNum("n_folds")} />
          </label>
          <label className="flex flex-col gap-1 text-muted">Default points (unmeasured)
            <input className={field} type="number" value={form.default_spread_points} onChange={setNum("default_spread_points")} />
          </label>
        </div>

        <fieldset className="mt-3">
          <legend className="text-[10px] uppercase tracking-wider text-muted mb-1.5">Features</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[12px]">
            {LAB_FEATURES.map((name) => (
              <label key={name} className="flex items-center gap-1.5 text-ink">
                <input type="checkbox" checked={form.features.includes(name)}
                       onChange={() => toggleFeature(name)} />
                {FEATURE_LABELS[name] ?? name}
              </label>
            ))}
          </div>
        </fieldset>

        <button
          className="mt-4 px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold text-[12px] disabled:opacity-50"
          onClick={onTrain}
          disabled={busy || form.features.length === 0}
        >
          {busy ? "Training…" : "Train"}
        </button>
      </section>

      {error && <p className="text-neg text-[12px] mb-3">{error}</p>}

      {result && Object.keys(result.dropped_features).length > 0 && (
        <p className="text-[12px] text-amber-400 mb-3">
          Dropped {Object.entries(result.dropped_features)
            .map(([k, v]) => `${k} (${Math.round(v * 100)}% unknown)`)
            .join(", ")}
          {result.spread_assumed
            ? " — cost uses the assumed spread, not measured spread."
            : ""}
        </p>
      )}

      <LabMetrics models={models} onActivate={onActivate} />
    </div>
  );
}
