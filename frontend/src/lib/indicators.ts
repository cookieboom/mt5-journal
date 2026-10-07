// Indicators on the chart (spec 2026-10-07-indicators §1.6). The server is the
// only place values are computed (domain/indicators); this module types its
// payloads, persists which scripts are attached (the layout), and turns results
// into lightweight-charts data. Pure except the fetch helpers at the bottom.
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { palette } from "./theme";

export type InputValue = number | boolean;

export interface InputSpec {
  name: string;
  kind: "int" | "float" | "bool";
  default: InputValue;
  min: number | null;
  max: number | null;
  step: number | null;
  title: string;
}

export interface PlotResult {
  title: string;
  color: string;
  pane: string;
  style: "line" | "histogram" | "dots";
  width: number;
  values: (number | null)[];
}

export interface HLineResult { value: number; title: string; pane: string; color: string }
export interface SignalHit { time_msc: number; side: "long" | "short" }

export interface IndicatorResult {
  times: number[];
  plots: PlotResult[];
  hlines: HLineResult[];
  signals: SignalHit[];
  lookback: number;
  warm_from_msc: number | null;
  forming_msc: number | null;
  pending: boolean;
}

export interface ScriptInfo {
  id: string;
  name: string;
  source: string;
  readonly: boolean;
  updated_ms: number | null;
}

export interface ScriptErrorInfo { line: number; col: number; message: string }

export interface IndicatorInstance {
  id: string;
  script: string;
  inputs: Record<string, InputValue>;
  visible: boolean;
}

export interface IndicatorLayout { version: 1; items: IndicatorInstance[] }

export const EMPTY_LAYOUT: IndicatorLayout = { version: 1, items: [] };

export function normalizeLayout(raw: unknown): IndicatorLayout {
  const items = (raw as { items?: unknown } | null)?.items;
  if (!Array.isArray(items)) return { version: 1, items: [] };
  const out: IndicatorInstance[] = [];
  for (const it of items) {
    const o = it as Record<string, unknown> | null;
    if (!o || typeof o.id !== "string" || typeof o.script !== "string") continue;
    const inputs: Record<string, InputValue> = {};
    for (const [k, v] of Object.entries((o.inputs ?? {}) as Record<string, unknown>)) {
      if (typeof v === "boolean" || (typeof v === "number" && Number.isFinite(v))) inputs[k] = v;
    }
    out.push({ id: o.id, script: o.script, inputs, visible: o.visible !== false });
  }
  return { version: 1, items: out };
}

// Replace everything from the tail's first bar on (its earlier bars may have
// been the forming bar, now final) and append the rest.
export function mergeTail(prev: IndicatorResult, tail: IndicatorResult): IndicatorResult {
  if (tail.times.length === 0) return prev;
  if (tail.plots.length !== prev.plots.length) return tail;
  const cut = tail.times[0];
  let k = prev.times.findIndex((t) => t >= cut);
  if (k === -1) k = prev.times.length;
  return {
    ...tail,
    times: [...prev.times.slice(0, k), ...tail.times],
    plots: tail.plots.map((p, i) => ({ ...p, values: [...prev.plots[i].values.slice(0, k), ...p.values] })),
    signals: [...prev.signals.filter((s) => s.time_msc < cut), ...tail.signals],
    warm_from_msc: prev.warm_from_msc,
  };
}

function indexOfTime(times: number[], t: number): number {
  let lo = 0, hi = times.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] === t) return mid;
    if (times[mid] < t) lo = mid + 1; else hi = mid - 1;
  }
  return -1;
}

export function valueAt(r: IndicatorResult, timeMs: number): (number | null)[] {
  const i = indexOfTime(r.times, timeMs);
  return r.plots.map((p) => (i === -1 ? null : p.values[i]));
}

// "price" is pane 0, shared by everyone. Any other pane name is private to its
// instance (two RSI instances get two panes), numbered in layout order.
export function paneAssignments(
  instances: { id: string; plots: string[] }[],
): Record<string, number> {
  const out: Record<string, number> = {};
  let next = 1;
  for (const inst of instances) {
    for (const pane of inst.plots) {
      const key = `${inst.id}:${pane}`;
      if (key in out) continue;
      out[key] = pane === "price" ? 0 : next++;
    }
  }
  return out;
}

export function toLineData(
  times: number[], values: (number | null)[],
): ({ time: UTCTimestamp; value: number } | { time: UTCTimestamp })[] {
  return times.map((t, i) => {
    const time = Math.floor(t / 1000) as UTCTimestamp;
    const v = values[i];
    return v === null || v === undefined ? { time } : { time, value: v };
  });
}

export function signalMarkers(lists: SignalHit[][]): SeriesMarker<Time>[] {
  return lists.flat()
    .sort((a, b) => a.time_msc - b.time_msc)
    .map((s) => ({
      time: Math.floor(s.time_msc / 1000) as UTCTimestamp,
      position: s.side === "long" ? "belowBar" : "aboveBar",
      shape: s.side === "long" ? "arrowUp" : "arrowDown",
      color: s.side === "long" ? palette.pos : palette.neg,
    }));
}

export function plotColor(token: string): string {
  return (palette as Record<string, string>)[token] ?? palette.muted;
}

// --- API -------------------------------------------------------------------

export interface ApiResult<T> { ok: boolean; data?: T; error?: string; scriptError?: ScriptErrorInfo }

async function call<T>(method: string, path: string, body?: unknown): Promise<ApiResult<T>> {
  try {
    const r = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) return { ok: false, error: j?.error ?? `HTTP ${r.status}`, scriptError: j?.script_error };
    return { ok: true, data: j as T };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export interface ComputeRequest {
  script: string;
  inputs: Record<string, InputValue>;
  symbol: string;
  timeframe: string;
  from_ms: number;
  to_ms: number;
  session_id?: number | null;
  include_forming?: boolean;
}

export type ValidateResult =
  | { ok: true; inputs: InputSpec[]; plots: Omit<PlotResult, "values">[]; hlines: HLineResult[];
      signals: string[]; lookback: Record<string, number> }
  | { ok: false; error: ScriptErrorInfo };

export const indicatorsApi = {
  compute: (b: ComputeRequest) => call<IndicatorResult>("POST", "/api/indicators/compute", b),
  scripts: () => call<{ scripts: ScriptInfo[] }>("GET", "/api/indicators/scripts"),
  create: (name: string, source: string) =>
    call<ScriptInfo>("POST", "/api/indicators/scripts", { name, source }),
  update: (id: string, name: string, source: string) =>
    call<ScriptInfo>("PUT", `/api/indicators/scripts/${encodeURIComponent(id)}`, { name, source }),
  remove: (id: string) => call<{ ok: true }>("DELETE", `/api/indicators/scripts/${encodeURIComponent(id)}`),
  validate: (source: string) => call<ValidateResult>("POST", "/api/indicators/validate", { source }),
  getLayout: () => call<{ layout: unknown }>("GET", "/api/indicators/layout"),
  putLayout: (l: IndicatorLayout) => call<{ ok: true }>("PUT", "/api/indicators/layout", l),
};
