import { useState } from "react";
import { SYMBOLS, TIMEFRAMES, timeframeMs, type Sym, type Timeframe } from "../lib/candles";
import type { ReplayConfig } from "../hooks/useReplaySession";

// Config for a new replay: symbol, timeframe, a start date (the reveal cursor),
// how many bars of history to show before it, and playback speed. range_start is
// cursor - historyBars*tf; range_end is "now" (reveal target).
export default function ReplayConfigModal(props: {
  onStart: (cfg: ReplayConfig) => void;
  onCancel: () => void;
}) {
  const [symbol, setSymbol] = useState<Sym>("XAUUSDc");
  const [tf, setTf] = useState<Timeframe>("M15");
  const [startDate, setStartDate] = useState<string>(""); // yyyy-mm-dd
  const [historyBars, setHistoryBars] = useState(300);
  const [speed, setSpeed] = useState(4);

  const submit = () => {
    const cursor = startDate ? new Date(startDate + "T00:00:00Z").getTime() : Date.now() - timeframeMs(tf) * 100;
    const range_start_msc = cursor - timeframeMs(tf) * historyBars;
    props.onStart({
      symbol, timeframe: tf,
      range_start_msc, range_end_msc: Date.now(),
      cursor_start_msc: cursor, speed,
    });
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50">
      <div className="glass w-[360px] p-4 space-y-3">
        <h2 className="text-sm font-semibold">Mulai Replay</h2>
        <label className="block text-xs">Simbol
          <select className="glass mt-1 w-full px-2 py-1" value={symbol}
                  onChange={(e) => setSymbol(e.target.value as Sym)}>
            {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="block text-xs">Timeframe
          <select className="glass mt-1 w-full px-2 py-1" value={tf}
                  onChange={(e) => setTf(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="block text-xs">Mulai dari tanggal (UTC)
          <input type="date" className="glass mt-1 w-full px-2 py-1" value={startDate}
                 onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="block text-xs">Bar histori sebelum mulai: {historyBars}
          <input type="range" min={100} max={1000} step={50} className="w-full" value={historyBars}
                 onChange={(e) => setHistoryBars(Number(e.target.value))} />
        </label>
        <label className="block text-xs">Kecepatan: {speed} bar/dtk
          <input type="range" min={1} max={10} className="w-full" value={speed}
                 onChange={(e) => setSpeed(Number(e.target.value))} />
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <button className="glass px-3 py-1 text-muted" onClick={props.onCancel}>Batal</button>
          <button className="glass px-3 py-1 text-cyan" onClick={submit}>Mulai</button>
        </div>
      </div>
    </div>
  );
}
