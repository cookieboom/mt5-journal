import { useEffect } from "react";
import { SYMBOLS, TIMEFRAMES } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";

// Number inputs advertise their clamp bounds; normalizeSettings enforces them.
const INITIAL_MIN = 100, INITIAL_MAX = 1000, MAX_MIN = 500, MAX_MAX = 10000;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="text-muted text-[11px] uppercase tracking-wide mb-2">{title}</div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center justify-between text-[12px] gap-2">
      <span className="text-muted">{label}</span>
      {children}
    </label>
  );
}

export default function ChartSettingsDrawer({
  settings, onChange, onReset, onClose,
}: {
  settings: ChartSettings;
  onChange: (s: ChartSettings) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const set = <K extends keyof ChartSettings>(k: K, v: ChartSettings[K]) =>
    onChange({ ...settings, [k]: v });
  const setColor = (k: keyof ChartSettings["colors"], v: string) =>
    onChange({ ...settings, colors: { ...settings.colors, [k]: v } });

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div
        className="glass fixed right-0 top-0 z-40 h-full w-[300px] p-4
                   flex flex-col"
        role="dialog"
        aria-label="Pengaturan chart"
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-ink text-[13px]">Pengaturan chart</div>
          <button onClick={onClose} className="text-muted hover:text-ink" aria-label="tutup">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          <Section title="Tampilan">
            <Field label="Tema">
              <div className="flex gap-1">
                {(["dark", "light"] as const).map((t) => (
                  <button key={t} onClick={() => set("theme", t)}
                    className={"px-2 py-1 rounded-md capitalize text-[12px] " +
                      (settings.theme === t
                        ? "bg-violet/25 ring-1 ring-inset ring-violet/35 text-ink"
                        : "text-muted hover:text-ink")}>{t}</button>
                ))}
              </div>
            </Field>
            <Field label="Garis grid">
              <input type="checkbox" checked={settings.grid}
                onChange={(e) => set("grid", e.target.checked)} />
            </Field>
            <Field label="Warna naik">
              <input type="color" value={settings.colors.up}
                onChange={(e) => setColor("up", e.target.value)} />
            </Field>
            <Field label="Warna turun">
              <input type="color" value={settings.colors.down}
                onChange={(e) => setColor("down", e.target.value)} />
            </Field>
            <Field label="Warna wick">
              <input type="color" value={settings.colors.wick}
                onChange={(e) => setColor("wick", e.target.value)} />
            </Field>
            <Field label="Tipe chart">
              <select value={settings.chartType} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("chartType", e.target.value as ChartSettings["chartType"])}>
                <option value="candle" className="bg-bg">Candle</option>
                <option value="bar" className="bg-bg">Bar</option>
                <option value="line" className="bg-bg">Line</option>
                <option value="area" className="bg-bg">Area</option>
              </select>
            </Field>
            <Field label="Crosshair">
              <select value={settings.crosshair} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("crosshair", e.target.value as ChartSettings["crosshair"])}>
                <option value="normal" className="bg-bg">Normal</option>
                <option value="magnet" className="bg-bg">Magnet</option>
                <option value="hidden" className="bg-bg">Hidden</option>
              </select>
            </Field>
          </Section>

          <Section title="Skala">
            <Field label="Mode harga">
              <div className="flex gap-1">
                {(["linear", "log"] as const).map((m) => (
                  <button key={m} onClick={() => set("priceScale", m)}
                    className={"px-2 py-1 rounded-md capitalize text-[12px] " +
                      (settings.priceScale === m
                        ? "bg-violet/25 ring-1 ring-inset ring-violet/35 text-ink"
                        : "text-muted hover:text-ink")}>{m}</button>
                ))}
              </div>
            </Field>
            <Field label="Auto-scale">
              <input type="checkbox" checked={settings.autoScale}
                onChange={(e) => set("autoScale", e.target.checked)} />
            </Field>
            <Field label="Garis harga terakhir">
              <input type="checkbox" checked={settings.lastPriceLine}
                onChange={(e) => set("lastPriceLine", e.target.checked)} />
            </Field>
          </Section>

          <Section title="Data">
            <Field label={`Bar awal (${INITIAL_MIN}–${INITIAL_MAX})`}>
              <input type="number" min={INITIAL_MIN} max={INITIAL_MAX} value={settings.initialBars}
                className="glass bg-transparent w-20 px-1 py-0.5 num text-right"
                onChange={(e) => set("initialBars", Number(e.target.value))} />
            </Field>
            <Field label={`Maks bar (${MAX_MIN}–${MAX_MAX})`}>
              <input type="number" min={MAX_MIN} max={MAX_MAX} value={settings.maxBars}
                className="glass bg-transparent w-20 px-1 py-0.5 num text-right"
                onChange={(e) => set("maxBars", Number(e.target.value))} />
            </Field>
          </Section>

          <Section title="Perilaku">
            <Field label="Overlay live (SL/TP/entry)">
              <input type="checkbox" checked={settings.liveOverlay}
                onChange={(e) => set("liveOverlay", e.target.checked)} />
            </Field>
            <Field label="Symbol default">
              <select value={settings.defaultSymbol} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("defaultSymbol", e.target.value as ChartSettings["defaultSymbol"])}>
                {SYMBOLS.map((s) => <option key={s} value={s} className="bg-bg">{s}</option>)}
              </select>
            </Field>
            <Field label="Timeframe default">
              <select value={settings.defaultTimeframe} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("defaultTimeframe", e.target.value as ChartSettings["defaultTimeframe"])}>
                {TIMEFRAMES.map((t) => <option key={t} value={t} className="bg-bg">{t}</option>)}
              </select>
            </Field>
          </Section>
        </div>

        <button onClick={onReset}
          className="glass mt-2 px-3 py-1.5 text-[12px] text-muted hover:text-ink self-start">
          Reset ke default
        </button>
      </div>
    </>
  );
}
