import { useEffect, useState } from "react";
import Modal from "./Modal";
import { indicatorsApi, type ScriptErrorInfo, type ScriptInfo, type ValidateResult } from "../lib/indicators";

const VALIDATE_MS = 400;

const HELP = `Sintaks Python terbatas. Seri: open high low close volume spread hl2 hlc3 ohlc4.
x[n] = n bar lalu. Fungsi: sma ema wma rma stdev highest lowest change roc tr atr rsi
macd_line macd_signal macd_hist bb_upper bb_mid bb_lower stoch_k stoch_d vwap
donchian_upper donchian_lower adx crossover crossunder abs log sqrt min max where nz.
n = input(14, min=1, max=200, title="Len")
plot(expr, title=, color=, pane="price"|"<nama>", style="line"|"histogram"|"dots", width=)
hline(70, pane="rsi")   signal("long"|"short", kondisi)`;

// Library scripts are read-only: saving one creates the user's own copy.
export default function ScriptEditor({ script, onSaved, onClose }: {
  script: Pick<ScriptInfo, "id" | "name" | "source" | "readonly"> | null;
  onSaved: (s: ScriptInfo) => void;
  onClose: () => void;
}) {
  const copying = script?.readonly ?? false;
  const [name, setName] = useState(script ? (copying ? `${script.name} (salinan)` : script.name) : "");
  const [source, setSource] = useState(script?.source ?? "plot(ema(close, 20))\n");
  const [check, setCheck] = useState<ValidateResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      void indicatorsApi.validate(source).then((r) => { if (alive && r.ok && r.data) setCheck(r.data); });
    }, VALIDATE_MS);
    return () => { alive = false; clearTimeout(t); };
  }, [source]);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    const r = script && !copying
      ? await indicatorsApi.update(script.id, name, source)
      : await indicatorsApi.create(name, source);
    setSaving(false);
    if (r.ok && r.data) onSaved(r.data);
    else setSaveError(r.scriptError ? fmt(r.scriptError) : r.error ?? "gagal menyimpan");
  };

  const err = check && !check.ok ? check.error : null;
  return (
    <Modal label="Editor script indikator" width="w-[min(720px,95vw)]" onClose={onClose}>
      <div className="space-y-3 text-body">
        <div className="text-headline">{script && !copying ? "Edit script" : "Script baru"}</div>
        <label className="block">
          <span className="text-muted">Nama</span>
          <input className="glass mt-1 w-full px-2 py-1" value={name} maxLength={80}
                 onChange={(e) => setName(e.target.value)} />
        </label>
        <textarea
          aria-label="Sumber script"
          className="glass w-full h-72 px-2 py-1 font-mono text-body"
          spellCheck={false}
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
        <div aria-live="polite" className="min-h-[1.5em]">
          {err ? <span className="text-neg">{fmt(err)}</span>
            : check?.ok ? (
              <span className="text-muted">
                OK · {check.plots.length} plot · {check.signals.length} sinyal ·
                warm-up M5 {check.lookback.M5} bar
              </span>
            ) : null}
        </div>
        {saveError && <div className="text-neg">{saveError}</div>}
        <details className="text-meta text-muted">
          <summary className="cursor-pointer">Bantuan sintaks</summary>
          <pre className="whitespace-pre-wrap mt-1">{HELP}</pre>
        </details>
        <div className="flex justify-end gap-2">
          <button className="glass px-3 py-1 text-muted hover:text-ink" onClick={onClose}>Batal</button>
          <button className="glass px-3 py-1 text-cyan disabled:opacity-40"
                  disabled={saving || !!err || name.trim() === ""} onClick={save}>
            {copying ? "Simpan salinan" : "Simpan"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function fmt(e: ScriptErrorInfo): string {
  return e.line ? `baris ${e.line}:${e.col} — ${e.message}` : e.message;
}
