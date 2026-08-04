// Pure display helpers + types for Chart Phase D replay. Time stays epoch-ms;
// money is USC (format with money()). No SL/TP detection here — the backend is
// authoritative (domain/replay_eval). Rule 4: 0 = none set, null = unknown.
import { LINE_COLORS, type Sym, type Timeframe } from "./candles";
import type { Candle, PriceLineSpec } from "./types";

export interface TrainingSession {
  id: number;
  symbol: Sym;
  symbol_base: string;
  timeframe: Timeframe;
  range_start_msc: number;
  range_end_msc: number;
  cursor_msc: number;
  status: "active" | "ended";
  created_at_msc: number;
}

export interface TrainingPosition {
  id: number;
  session_id: number;
  direction: "buy" | "sell";
  volume: number;
  decision_msc: number;
  entry_msc: number | null;
  entry_price: number | null;
  sl: number;                 // 0 = none set
  tp: number;                 // 0 = none set
  close_requested_msc: number | null;
  exit_msc: number | null;
  exit_price: number | null;
  exit_reason: "tp" | "sl" | "manual" | "eod" | null;
  status: "pending" | "open" | "closed";
  net_profit: number | null;  // USC
  r_multiple: number | null;
  mae: number | null;
  mfe: number | null;
  mae_r: number | null;
  mfe_r: number | null;
  created_at_msc: number;
}

export interface StepEvent {
  position_id: number;
  kind: "fill" | "exit";
  price: number;
  time_msc: number;
  reason: "tp" | "sl" | "manual" | null;
}

export interface TrainingSummary {
  n: number;
  win_rate: number | null;
  avg_r: number | null;
  total_r: number;
  avg_mae_r: number | null;
  avg_mfe_r: number | null;
}

// A brand-new session has no closed positions yet and the create endpoint does
// not carry a summary. Seed this instead of null so the card renders its rows
// (n 0, total 0.00R) from the first bar rather than a bare "—" — mirrors what
// training_store._summary returns for an empty session.
export const EMPTY_SUMMARY: TrainingSummary = {
  n: 0, win_rate: null, avg_r: null, total_r: 0, avg_mae_r: null, avg_mfe_r: null,
};

// How the closed positions of a session ended. The backend keeps the same
// counters in training_session_stats, but every closed position is already in
// `positions` — count them here instead of polling a second endpoint.
export interface OutcomeCounts {
  closed: number;
  sl: number;
  tp: number;
  manual: number;   // includes 'eod' (unresolved at end of range)
}

export function outcomeCounts(positions: TrainingPosition[]): OutcomeCounts {
  const out: OutcomeCounts = { closed: 0, sl: 0, tp: 0, manual: 0 };
  for (const p of positions) {
    if (p.status !== "closed") continue;
    out.closed += 1;
    if (p.exit_reason === "sl") out.sl += 1;
    else if (p.exit_reason === "tp") out.tp += 1;
    else out.manual += 1;
  }
  return out;
}

// Competitive mode runs each scenario as its own backend session, so no single
// session_summary spans the whole run. Mirror training_store._summary here over
// the positions the client already holds (current round + finished rounds).
export function summarize(positions: TrainingPosition[]): TrainingSummary {
  const resolved = positions.filter((p) => p.status === "closed" && p.net_profit !== null);
  const nums = (pick: (p: TrainingPosition) => number | null) =>
    resolved.map(pick).filter((v): v is number => v !== null);
  const rs = nums((p) => p.r_multiple);
  const maes = nums((p) => p.mae_r);
  const mfes = nums((p) => p.mfe_r);
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const n = resolved.length;
  const total_r = sum(rs);
  return {
    n,
    win_rate: n ? resolved.filter((p) => (p.net_profit ?? 0) > 0).length / n : null,
    avg_r: rs.length ? total_r / rs.length : null,
    total_r,
    avg_mae_r: maes.length ? sum(maes) / maes.length : null,
    avg_mfe_r: mfes.length ? sum(mfes) / mfes.length : null,
  };
}

// Only bars at or before the reveal cursor are drawn — the future is hidden.
export function clipToCursor(candles: Candle[], cursorMsc: number): Candle[] {
  return candles.filter((c) => c.time_msc <= cursorMsc);
}

// Overlay price-lines for the OPEN/PENDING fake positions only. Mirrors
// lib/candles.ts::liveLines: skips 0 (none set) and null (unknown).
export function replayLines(positions: TrainingPosition[]): PriceLineSpec[] {
  const out: PriceLineSpec[] = [];
  const add = (v: number | null, color: string, title: string) => {
    if (v !== null && v !== undefined && Math.abs(v) > 1e-9) out.push({ price: v, color, title });
  };
  for (const p of positions) {
    if (p.status === "closed") continue;
    add(p.entry_price, LINE_COLORS.entry, `entry #${p.id}`);
    add(p.sl, LINE_COLORS.sl, `SL #${p.id}`);
    add(p.tp, LINE_COLORS.tp, `TP #${p.id}`);
  }
  return out;
}

// Live, unrealized R for an OPEN position marked to the current bar's close.
// Null without an SL (rule 4) — R needs a risk distance.
export function unrealizedR(p: TrainingPosition, currentClose: number): number | null {
  if (p.entry_price === null || !p.sl || Math.abs(p.sl) < 1e-9) return null;
  const risk = Math.abs(p.entry_price - p.sl);
  if (risk < 1e-9) return null;
  const move = p.direction === "buy" ? currentClose - p.entry_price : p.entry_price - currentClose;
  return move / risk;
}

// Playback speed (1..10) → delay between auto-steps in ms. Faster = smaller.
export function msPerStep(speed: number): number {
  const s = Math.min(10, Math.max(1, speed));
  return Math.round(1000 / s);
}
