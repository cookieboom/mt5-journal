// Pure helpers for the Phase B chart. Time stays epoch-ms (broker SERVER = UTC);
// divide by 1000 only when feeding lightweight-charts. Rule 4: liveLines draws
// real prices only (skips null = unknown and 0.0 = none set).
import type { Candle, CandlesResponse, LivePosition } from "./types";

export type Timeframe = "M1" | "M5" | "M15" | "H1" | "H4" | "D1";
export const TIMEFRAMES: Timeframe[] = ["M1", "M5", "M15", "H1", "H4", "D1"];

export type Sym = "XAUUSDc" | "BTCUSDc" | "EURUSDc";
export const SYMBOLS: Sym[] = ["XAUUSDc", "BTCUSDc", "EURUSDc"];

const MIN = 60_000;
const TF_MS: Record<Timeframe, number> = {
  M1: 1 * MIN, M5: 5 * MIN, M15: 15 * MIN, H1: 60 * MIN, H4: 240 * MIN, D1: 1440 * MIN,
};

export function timeframeMs(tf: Timeframe): number {
  return TF_MS[tf];
}

export function toSeconds(ms: number): number {
  return Math.floor(ms / 1000);
}

export function initialWindow(tf: Timeframe, nowMs: number, bars = 300): [number, number] {
  return [nowMs - timeframeMs(tf) * bars, nowMs];
}

export function olderWindow(currentFromMs: number, tf: Timeframe, bars = 300): [number, number] {
  return [currentFromMs - timeframeMs(tf) * bars, currentFromMs - 1];
}

export function mergeCandles(existing: Candle[], incoming: Candle[]): Candle[] {
  const m = new Map<number, Candle>();
  for (const c of existing) m.set(c.time_msc, c);
  for (const c of incoming) m.set(c.time_msc, c); // incoming wins on collision
  return [...m.values()].sort((a, b) => a.time_msc - b.time_msc);
}

export function isNowVisible(
  lastBarMs: number | null, visibleToMs: number | null, tf: Timeframe,
): boolean {
  if (lastBarMs === null || visibleToMs === null) return false;
  return visibleToMs >= lastBarMs - timeframeMs(tf); // right edge within one bar of last
}

export const LINE_COLORS = { sl: "#fb7185", tp: "#34d399", entry: "#9a97c4" };

export function liveLines(pos: LivePosition): { price: number; color: string; title: string }[] {
  const out: { price: number; color: string; title: string }[] = [];
  const add = (v: number | null, color: string, title: string) => {
    if (v !== null && v !== undefined && Math.abs(v) > 1e-9) out.push({ price: v, color, title });
  };
  add(pos.open_price, LINE_COLORS.entry, `entry #${pos.position_id}`);
  add(pos.sl, LINE_COLORS.sl, `SL #${pos.position_id}`);
  add(pos.tp, LINE_COLORS.tp, `TP #${pos.position_id}`);
  return out;
}

export async function fetchCandles(
  symbol: string, tf: Timeframe, fromMs: number, toMs: number,
): Promise<CandlesResponse> {
  const q = new URLSearchParams({
    symbol, timeframe: tf, from: String(Math.floor(fromMs)), to: String(Math.floor(toMs)),
  });
  const r = await fetch(`/api/candles?${q}`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
  return body as CandlesResponse;
}
