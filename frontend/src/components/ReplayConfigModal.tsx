import { useState } from "react";
import { SYMBOLS, TIMEFRAMES, timeframeMs, type Sym, type Timeframe } from "../lib/candles";
import {
  HISTORY_MIN, HISTORY_MAX, SPEED_MIN, SPEED_MAX, type ReplayFormPrefs,
} from "../lib/replayPrefs";
import type { ReplayConfig } from "../hooks/useReplaySession";
import Modal from "./Modal";

// Config for a new replay: symbol, timeframe, a start date (the reveal cursor),
// how many bars of history to show before it, and playback speed. range_start is
// cursor - historyBars*tf; range_end is "now" (reveal target). Fields are seeded
// from `initial` (last-launched prefs) and the raw form is handed back on submit.
export default function ReplayConfigModal(props: {
  initial: ReplayFormPrefs;
  onStart: (cfg: ReplayConfig, form: ReplayFormPrefs) => void;
  onCancel: () => void;
}) {
  const [symbol, setSymbol] = useState<Sym>(props.initial.symbol);
  const [tf, setTf] = useState<Timeframe>(props.initial.timeframe);
  const [startDate, setStartDate] = useState<string>(props.initial.startDate);
  const [historyBars, setHistoryBars] = useState(props.initial.historyBars);
  const [speed, setSpeed] = useState(props.initial.speed);

  const [compMode, setCompMode] = useState(props.initial.competitiveMode);
  const [compHideDate, setCompHideDate] = useState(props.initial.competitiveHideDate);
  const [compRounds, setCompRounds] = useState(props.initial.competitiveRounds);

  const submit = () => {
    let cursor = startDate ? new Date(startDate + "T00:00:00Z").getTime() : Date.now() - timeframeMs(tf) * 100;
    if (compMode) {
      const endMs = Date.now() - 14 * 24 * 3600 * 1000;
      const startMs = Date.now() - 2 * 365 * 24 * 3600 * 1000;
      cursor = Math.floor(startMs + Math.random() * (endMs - startMs));
    }
    const range_start_msc = cursor - timeframeMs(tf) * historyBars;
    props.onStart(
      {
        symbol, timeframe: tf,
        range_start_msc, range_end_msc: Date.now(),
        cursor_start_msc: cursor, speed,
      },
      { 
        version: 1, symbol, timeframe: tf, startDate, historyBars, speed,
        competitiveMode: compMode, competitiveHideDate: compHideDate, competitiveRounds: compRounds
      },
    );
  };

  return (
    <Modal label="Mulai replay" width="w-[min(22.5rem,calc(100vw-2rem))]" onClose={props.onCancel}>
      <div className="space-y-3">
        <h2 className="text-title font-semibold">Mulai Replay</h2>
        <label className="block text-body">Simbol
          <select className="glass mt-1 w-full px-2 py-1" value={symbol}
                  onChange={(e) => setSymbol(e.target.value as Sym)}>
            {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="block text-body">Timeframe
          <select className="glass mt-1 w-full px-2 py-1" value={tf}
                  onChange={(e) => setTf(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="block text-body flex items-center gap-2 mt-4 text-warn font-semibold">
          <input type="checkbox" checked={compMode} onChange={(e) => setCompMode(e.target.checked)} />
          Competitive Mode
        </label>
        {compMode && (
          <div className="pl-4 space-y-2 border-l border-warn/50">
            <label className="block text-body flex items-center gap-2">
              <input type="checkbox" checked={compHideDate} onChange={(e) => setCompHideDate(e.target.checked)} />
              Sembunyikan Tanggal
            </label>
            <label className="block text-body">Jumlah Skenario (0 = tak terbatas): {compRounds}
              <input type="range" min={0} max={20} className="w-full" value={compRounds}
                     onChange={(e) => setCompRounds(Number(e.target.value))} />
            </label>
          </div>
        )}
        {!compMode && (
          <label className="block text-body mt-2">Mulai dari tanggal (UTC)
            <input type="date" className="glass mt-1 w-full px-2 py-1" value={startDate}
                   onChange={(e) => setStartDate(e.target.value)} />
          </label>
        )}
        <label className="block text-body">Bar histori sebelum mulai: {historyBars}
          <input type="range" min={HISTORY_MIN} max={HISTORY_MAX} step={50} className="w-full" value={historyBars}
                 onChange={(e) => setHistoryBars(Number(e.target.value))} />
        </label>
        <label className="block text-body">Kecepatan: {speed} bar/dtk
          <input type="range" min={SPEED_MIN} max={SPEED_MAX} className="w-full" value={speed}
                 onChange={(e) => setSpeed(Number(e.target.value))} />
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <button className="glass px-3 py-1 text-muted" onClick={props.onCancel}>Batal</button>
          <button className="glass px-3 py-1 text-cyan" onClick={submit}>Mulai</button>
        </div>
      </div>
    </Modal>
  );
}
