import { useState } from "react";
import {
  THEMES, TF_OPTIONS, PAD_MIN, PAD_MAX, type TradePngSettings,
} from "../lib/tradePngPrefs";

export default function TradePngPanel(
  { settings, onChange }: { settings: TradePngSettings; onChange: (s: TradePngSettings) => void },
) {
  const [open, setOpen] = useState(false);
  const set = <K extends keyof TradePngSettings>(k: K, v: TradePngSettings[K]) =>
    onChange({ ...settings, [k]: v });
  const clampPad = (raw: string) => {
    const n = Number(raw);
    onChange({ ...settings, padBars: Math.min(PAD_MAX, Math.max(PAD_MIN, Math.round(n || PAD_MIN))) });
  };
  return (
    <div className="mb-2 text-body">
      <button className="text-cyan hover:underline" onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Render settings
      </button>
      {open && (
        <div className="glass mt-2 p-3 grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1">Theme
            <select aria-label="theme" className="bg-transparent border border-white/10 rounded px-1 py-0.5"
              value={settings.theme} onChange={(e) => set("theme", e.target.value as TradePngSettings["theme"])}>
              {THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">Context bars ({PAD_MIN}–{PAD_MAX})
            <input aria-label="context bars" type="number" min={PAD_MIN} max={PAD_MAX}
              className="bg-transparent border border-white/10 rounded px-1 py-0.5"
              value={settings.padBars} onChange={(e) => clampPad(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">Timeframe
            <select aria-label="timeframe" className="bg-transparent border border-white/10 rounded px-1 py-0.5"
              value={settings.tfOverride ?? ""} onChange={(e) => set("tfOverride", (e.target.value || null) as TradePngSettings["tfOverride"])}>
              {TF_OPTIONS.map((t) => <option key={t ?? "auto"} value={t ?? ""}>{t ?? "Auto"}</option>)}
            </select>
          </label>
          <fieldset className="col-span-2 flex flex-wrap gap-3">
            {([["showSltp","SL/TP"],["showMarkers","Markers"],["showVolume","Volume"],["showGrid","Grid"]] as const).map(
              ([k, lbl]) => (
                <label key={k} className="flex items-center gap-1">
                  <input type="checkbox" checked={settings[k]} onChange={(e) => set(k, e.target.checked)} /> {lbl}
                </label>
              ))}
          </fieldset>
          <p className="col-span-2 text-muted">Berlaku untuk semua gambar trade.</p>
        </div>
      )}
    </div>
  );
}
