import { useState } from "react";
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import ChartSettingsDrawer from "./ChartSettingsDrawer";

export default function ChartToolbar({
  symbol, tf, settings, onSymbol, onTf, onSettings, onReset, onJumpNow, onReplay, replayActive,
}: {
  symbol: Sym;
  tf: Timeframe;
  settings: ChartSettings;
  onSymbol: (s: Sym) => void;
  onTf: (t: Timeframe) => void;
  onSettings: (s: ChartSettings) => void;
  onReset: () => void;
  onJumpNow: () => void;
  onReplay: () => void;
  replayActive?: boolean;
}) {
  const [gear, setGear] = useState(false);
  return (
    // Below lg the toolbar owns its own full-width line and wraps inside it.
    // Unwrapped, the row overflows a phone and everything after the timeframes
    // — "Ke sekarang", Replay, and the ⚙ that opens the settings drawer — sits
    // past the right edge with no way to scroll to it.
    <div className="flex flex-wrap items-center gap-2 mb-3 w-full lg:w-auto">
      <select
        value={symbol}
        onChange={(e) => onSymbol(e.target.value as Sym)}
        className="glass px-2 py-1 text-[13px] bg-transparent"
        aria-label="symbol"
      >
        {SYMBOLS.map((s) => (
          <option key={s} value={s} className="bg-bg">{s}</option>
        ))}
      </select>

      <div className="glass flex overflow-hidden text-[12px]">
        {TIMEFRAMES.map((t) => (
          <button
            key={t}
            onClick={() => onTf(t)}
            className={
              "px-2.5 py-1 " +
              (t === tf ? "bg-violet/25 text-ink" : "text-muted hover:text-ink")
            }
          >
            {t}
          </button>
        ))}
      </div>

      <button
        onClick={onJumpNow}
        className="glass px-2.5 py-1 text-[12px] text-muted hover:text-ink"
      >
        Ke sekarang
      </button>

      <button
        className="glass px-3 py-1 text-cyan"
        onClick={onReplay}
        disabled={replayActive}
        title="Mode replay/training"
      >
        ▶ Replay
      </button>

      <div className="ml-auto">
        <button
          onClick={() => setGear((g) => !g)}
          className="glass px-2.5 py-1 text-[13px] text-muted hover:text-ink"
          aria-label="settings"
        >
          ⚙
        </button>
        {gear && (
          <ChartSettingsDrawer
            settings={settings}
            onChange={onSettings}
            onReset={onReset}
            onClose={() => setGear(false)}
          />
        )}
      </div>
    </div>
  );
}
