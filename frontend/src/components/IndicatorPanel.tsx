import { useEffect, useState } from "react";
import ScriptEditor from "./ScriptEditor";
import {
  indicatorsApi, plotColor, valueAt,
  type IndicatorLayout, type IndicatorResult, type InputSpec, type InputValue, type ScriptInfo,
} from "../lib/indicators";

// The side-panel list of attached indicators: add, hide, tune inputs, edit the
// script, reorder (= pane order), remove — plus each plot's value at the
// crosshair. Same panel in live and replay; the layout is shared.
export default function IndicatorPanel({
  layout, onLayout, scripts, onScriptSaved, results, errors, hoverMs, firstShownMs,
}: {
  layout: IndicatorLayout;
  onLayout: (l: IndicatorLayout) => void;
  scripts: ScriptInfo[];
  onScriptSaved: (s: ScriptInfo) => void;
  results: Record<string, IndicatorResult>;
  errors: Record<string, string>;
  hoverMs: number | null;
  firstShownMs: number | null;
}) {
  const [editing, setEditing] = useState<ScriptInfo | "new" | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const specs = useInputSpecs(scripts, layout.items.map((i) => i.script));
  const byId = new Map(scripts.map((s) => [s.id, s]));

  const set = (items: IndicatorLayout["items"]) => onLayout({ ...layout, items });
  const patch = (id: string, p: Partial<IndicatorLayout["items"][number]>) =>
    set(layout.items.map((i) => (i.id === id ? { ...i, ...p } : i)));
  const move = (k: number, d: -1 | 1) => {
    const items = [...layout.items];
    const j = k + d;
    if (j < 0 || j >= items.length) return;
    [items[k], items[j]] = [items[j], items[k]];
    set(items);
  };
  const add = (script: string) => {
    if (!script) return;
    set([...layout.items, { id: crypto.randomUUID(), script, inputs: {}, visible: true }]);
  };

  return (
    <div className="glass p-3 space-y-2 text-body">
      <div className="flex items-center justify-between">
        <div className="font-semibold">Indikator</div>
        <button className="text-meta text-muted hover:text-ink" onClick={() => setEditing("new")}>
          + Script baru
        </button>
      </div>

      {layout.items.length === 0 && <div className="text-muted">Belum ada indikator.</div>}

      {layout.items.map((inst, k) => {
        const s = byId.get(inst.script);
        const r = results[inst.id];
        const values = r ? (hoverMs !== null ? valueAt(r, hoverMs) : r.plots.map((p) => lastKnown(p.values))) : [];
        const cold = r && firstShownMs !== null && (r.warm_from_msc === null || r.warm_from_msc > firstShownMs);
        return (
          <div key={inst.id} className="border-t border-panel-border pt-2 space-y-1">
            <div className="flex items-center gap-1">
              <input type="checkbox" aria-label={`Tampilkan ${s?.name ?? inst.script}`}
                     checked={inst.visible} onChange={(e) => patch(inst.id, { visible: e.target.checked })} />
              <button className={`flex-1 text-left truncate ${inst.visible ? "" : "text-muted"}`}
                      onClick={() => setOpen(open === inst.id ? null : inst.id)}
                      aria-expanded={open === inst.id}>
                {s?.name ?? `${inst.script} (hilang)`}
              </button>
              <button aria-label="Naik" className="text-muted hover:text-ink px-1" onClick={() => move(k, -1)}>↑</button>
              <button aria-label="Turun" className="text-muted hover:text-ink px-1" onClick={() => move(k, 1)}>↓</button>
              <button aria-label={`Hapus ${s?.name ?? inst.script}`} className="text-muted hover:text-neg px-1"
                      onClick={() => set(layout.items.filter((i) => i.id !== inst.id))}>×</button>
            </div>

            {errors[inst.id] && <div className="text-neg text-meta">{errors[inst.id]}</div>}
            {cold && <div className="text-warn text-meta">Data warm-up kurang — nilai awal kosong.</div>}

            {inst.visible && r && r.plots.map((p, i) => (
              <div key={i} className="flex justify-between text-meta">
                <span style={{ color: plotColor(p.color) }}>{p.title}</span>
                <span className="num">{values[i] == null ? "—" : fmtValue(values[i] as number)}</span>
              </div>
            ))}

            {open === inst.id && (
              <div className="space-y-1 pt-1">
                {(specs[inst.script] ?? []).map((spec) => (
                  <InputField key={spec.name} spec={spec} value={inst.inputs[spec.name]}
                              onChange={(v) => patch(inst.id, { inputs: { ...inst.inputs, [spec.name]: v } })} />
                ))}
                {s && (
                  <button className="text-meta text-cyan" onClick={() => setEditing(s)}>
                    {s.readonly ? "Lihat / salin script" : "Edit script"}
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}

      <select aria-label="Tambah indikator" className="glass w-full px-2 py-1" value=""
              onChange={(e) => add(e.target.value)}>
        <option value="">+ Tambah indikator…</option>
        <optgroup label="Bawaan">
          {scripts.filter((s) => s.readonly).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </optgroup>
        {scripts.some((s) => !s.readonly) && (
          <optgroup label="Script saya">
            {scripts.filter((s) => !s.readonly).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </optgroup>
        )}
      </select>

      {editing && (
        <ScriptEditor
          script={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            const wasCopy = editing !== "new" && editing.readonly;
            setEditing(null);
            onScriptSaved(saved);
            if (editing === "new" || wasCopy) add(saved.id);
          }}
        />
      )}
    </div>
  );
}

// Indicator values are derived, not prices: EMA of gold carries ten digits
// nobody reads. Two decimals from 10 up (gold, RSI, ADX), five below (EURUSD,
// MACD of a small mover), never trailing zeros.
function fmtValue(v: number): string {
  return v.toLocaleString("en-US", {
    maximumFractionDigits: Math.abs(v) >= 10 ? 2 : 5, useGrouping: false,
  });
}

function lastKnown(values: (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return values[i];
  return null;
}

function InputField({ spec, value, onChange }: {
  spec: InputSpec; value: InputValue | undefined; onChange: (v: InputValue) => void;
}) {
  const v = value ?? spec.default;
  if (spec.kind === "bool") {
    return (
      <label className="flex items-center justify-between text-meta">
        <span className="text-muted">{spec.title}</span>
        <input type="checkbox" checked={v as boolean} onChange={(e) => onChange(e.target.checked)} />
      </label>
    );
  }
  return (
    <label className="flex items-center justify-between gap-2 text-meta">
      <span className="text-muted">{spec.title}</span>
      <input type="number" className="glass w-20 px-1 py-0.5 num"
             min={spec.min ?? undefined} max={spec.max ?? undefined}
             step={spec.step ?? (spec.kind === "int" ? 1 : "any")}
             value={v as number}
             onChange={(e) => {
               const n = Number(e.target.value);
               // The server clamps; an empty field mid-typing is not sent.
               if (e.target.value !== "" && Number.isFinite(n)) onChange(n);
             }} />
    </label>
  );
}

// Input specs come from the server's validator (one source of truth for the
// language), fetched once per script source.
function useInputSpecs(scripts: ScriptInfo[], wanted: string[]): Record<string, InputSpec[]> {
  const [specs, setSpecs] = useState<Record<string, { source: string; inputs: InputSpec[] }>>({});
  const key = wanted.join("|");
  useEffect(() => {
    for (const id of new Set(wanted)) {
      const s = scripts.find((x) => x.id === id);
      if (!s || specs[id]?.source === s.source) continue;
      void indicatorsApi.validate(s.source).then((r) => {
        if (r.ok && r.data && r.data.ok) {
          const inputs = r.data.inputs;
          setSpecs((p) => ({ ...p, [id]: { source: s.source, inputs } }));
        }
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, scripts]);
  return Object.fromEntries(Object.entries(specs).map(([k, v]) => [k, v.inputs]));
}
