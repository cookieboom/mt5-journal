// Strategy Tester (spec 2026-10-07-indicators-strategy-tester §1.8). The server
// simulates (domain/indicators/backtest); this module types the payload,
// normalises the per-indicator tester settings kept in the layout blob, and
// turns trades into chart markers. Pure except the api helpers.
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { palette } from "./theme";
import type { InputValue } from "./indicators";

export interface BacktestSettings {
  rangeDays: number;          // window = last N days up to the last closed bar
  split: number;              // IS share, 0.05..0.95
  sl: "atr" | "points" | "none";
  slValue: number;
  atrLen: number;
  tpR: number | null;
  maxHold: number | null;
  oppositeCloses: boolean;
  overlap: boolean;
  spreadFallback: number | null;
  showOnChart: boolean;
}

export const DEFAULT_BACKTEST: BacktestSettings = {
  rangeDays: 90, split: 0.7, sl: "atr", slValue: 1.5, atrLen: 14, tpR: 2,
  maxHold: null, oppositeCloses: true, overlap: false, spreadFallback: null, showOnChart: true,
};

function num(v: unknown, lo: number, hi: number, dflt: number): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt;
}
function numOrNull(v: unknown, lo: number, hi: number): number | null {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : null;
}

export function normalizeBacktest(raw: unknown): BacktestSettings {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const d = DEFAULT_BACKTEST;
  return {
    rangeDays: Math.round(num(o.rangeDays, 1, 3650, d.rangeDays)),
    split: num(o.split, 0.05, 0.95, d.split),
    sl: o.sl === "points" || o.sl === "none" || o.sl === "atr" ? o.sl : d.sl,
    slValue: num(o.slValue, 0.0001, 1e9, d.slValue),
    atrLen: Math.round(num(o.atrLen, 1, 5000, d.atrLen)),
    tpR: o.tpR === null ? null : numOrNull(o.tpR, 0.01, 100) ?? d.tpR,
    maxHold: o.maxHold == null ? null : (() => { const m = numOrNull(o.maxHold, 1, 1e6); return m === null ? null : Math.round(m); })(),
    oppositeCloses: typeof o.oppositeCloses === "boolean" ? o.oppositeCloses : d.oppositeCloses,
    overlap: typeof o.overlap === "boolean" ? o.overlap : d.overlap,
    spreadFallback: numOrNull(o.spreadFallback, 0, 1e9),
    showOnChart: typeof o.showOnChart === "boolean" ? o.showOnChart : d.showOnChart,
  };
}

export type ExitReason = "sl" | "tp" | "exit" | "opposite" | "max_hold" | "open";

export interface TesterTrade {
  id: number;
  side: "long" | "short";
  decision_msc: number;
  entry_msc: number;
  entry: number;
  sl: number;
  tp: number;
  exit_msc: number | null;
  exit: number | null;
  reason: ExitReason;
  r: number | null;
  r_gross: number | null;
  mae_r: number | null;
  mfe_r: number | null;
  spread_fallback: boolean;
}

export interface Segment {
  n: number;
  n_r: number;
  win_rate: number | null;
  avg_r: number | null;
  total_r: number;
  profit_factor: number | null;
  max_dd_r: number | null;
  max_win_streak: number;
  max_loss_streak: number;
}

export interface Bucket {
  key: number | string; n: number; win_rate: number | null; avg_r: number | null;
  total_r: number | null;                       // null: no trade in it has a known R (rule 4)
}
/** A day / week / session instance `[key, end_msc)` UTC, for the replay jump per period. */
export interface Period extends Bucket {
  key: number; end_msc: number;
  last_exit_msc: number;                       // where a replay must reach (never the future)
  segment: "is" | "oos" | "mixed";             // by decision time, as the segments split
}
export type PeriodKind = "day" | "week" | "session";
export interface Breakdown {
  hour: Bucket[]; session: Bucket[]; dow: Bucket[];
  periods?: Record<PeriodKind, Period[]>;     // only under breakdown.all
}

// Trade context (spec 2026-10-08-indicators-trade-context §2 A): my closed
// trades split by whether a same-side signal fired in the last N closed bars.
// Real cells come pre-gated from the server (n < 20 → null, `gated` true).
export interface ContextCell {
  n: number; n_r: number; win_rate: number | null; avg_r: number | null;
  total_r: number | null; gated: boolean;
}
export interface ContextSource { true: ContextCell; false: ContextCell; n_unknown: number }
export type ContextSourceKey = "real" | "replay" | "paper";
export interface ContextResult {
  computed_ms: number; source_hash: string; window: number;
  warm_from_msc: number | null; pending: boolean;
  clipped_from_msc: number | null;            // span too long: trades before this read unknown
  sources: Record<ContextSourceKey, ContextSource>;
}
export interface ContextQuery {
  script: string; inputs: Record<string, InputValue>; symbol: string; tf: string; rev: number;
}

export interface BacktestResult {
  computed_ms: number;
  source_hash: string;
  split_msc: number;
  segments: { all: Segment; is: Segment; oos: Segment };
  equity: { t: number; r: number }[];
  breakdown: { all: Breakdown; oos: Breakdown };
  trades: TesterTrade[];
  counts: { rejected: number; open: number; spread_fallback: number; unknown_cost: number; no_stop: number };
  warm_from_msc: number | null;
  pending: boolean;
}

export const DAY_MS = 86_400_000;

export function backtestBody(a: {
  script: string; inputs: Record<string, InputValue>; symbol: string; tf: string;
  toMs: number; s: BacktestSettings;
}) {
  return {
    script: a.script, inputs: a.inputs, symbol: a.symbol, timeframe: a.tf,
    from_ms: a.toMs - a.s.rangeDays * DAY_MS, to_ms: a.toMs, split: a.s.split,
    exits: {
      sl: a.s.sl, sl_value: a.s.slValue, atr_len: a.s.atrLen, tp_r: a.s.tpR,
      max_hold: a.s.maxHold, opposite_closes: a.s.oppositeCloses, overlap: a.s.overlap,
    },
    spread_fallback: a.s.spreadFallback,
  };
}

/** `signal()` outside a comment. The parser is the authority; this only decides
 *  whether the tester strip is worth showing (a false hit gets the server's 400). */
export function hasSignal(source: string): boolean {
  return source.split("\n").some((l) => /\bsignal\s*\(/.test(l.split("#")[0]));
}

const sec = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

/** Entry arrow + exit dot with its R, per simulated trade. */
export function tradeMarkers(trades: TesterTrade[]): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const t of trades) {
    const long = t.side === "long";
    out.push({
      time: sec(t.entry_msc), position: long ? "belowBar" : "aboveBar",
      shape: long ? "arrowUp" : "arrowDown", color: long ? palette.pos : palette.neg,
    });
    if (t.exit_msc !== null) {
      out.push({
        time: sec(t.exit_msc), position: long ? "aboveBar" : "belowBar", shape: "circle",
        color: t.r === null ? palette.muted : t.r > 0 ? palette.pos : palette.neg,
        text: t.r === null ? "?" : `${t.r > 0 ? "+" : ""}${t.r.toFixed(2)}R`,
      });
    }
  }
  return out.sort((a, b) => (a.time as number) - (b.time as number));
}

export function fmtR(v: number | null, digits = 2): string {
  if (v === null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}

export function ageLabel(computedMs: number, nowMs: number): string {
  const m = Math.max(0, Math.round((nowMs - computedMs) / 60_000));
  if (m < 1) return "baru saja";
  if (m < 60) return `${m} mnt lalu`;
  return `${Math.round(m / 60)} jam lalu`;
}

/** The collapsed strip's headline — OOS first, always with n and age (rule 9). */
export function headline(r: BacktestResult, nowMs: number): string {
  const o = r.segments.oos;
  return `OOS ${fmtR(o.avg_r)} R/trade · n ${o.n} · ${ageLabel(r.computed_ms, nowMs)}`;
}

export interface ApiOut<T> { ok: boolean; data?: T; error?: string }

async function post<T>(path: string, body: unknown): Promise<ApiOut<T>> {
  try {
    const r = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      const se = j?.script_error;
      return { ok: false, error: se ? `baris ${se.line}: ${se.message}` : j?.error ?? `HTTP ${r.status}` };
    }
    return { ok: true, data: j as T };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export const backtestApi = {
  run: (body: ReturnType<typeof backtestBody>) => post<BacktestResult>("/api/indicators/backtest", body),
  replay: (b: { symbol: string; timeframe: string; decision_msc: number; exit_msc: number | null }) =>
    post<{ session: unknown; pending: boolean }>("/api/indicators/backtest/replay", b),
  context: (q: ContextQuery, window: number) => post<ContextResult>("/api/indicators/context", {
    script: q.script, inputs: q.inputs, symbol: q.symbol, timeframe: q.tf, window,
  }),
};
