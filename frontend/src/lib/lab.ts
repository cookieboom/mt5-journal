// Typed client for /api/lab/*. Impure fetchers only; regimeColor/formatAge/
// bestModel are pure and tested. Mirrors src/journal/lab/features.py
// PRICE_FEATURES and web/lab_api.py's payload shapes — see lib/types.ts.
import { postJson } from "./api";
import { palette, tint, white } from "./theme";
import { TIMEFRAMES, type Timeframe } from "./candles";
import type { LabModel, LabScore, LabStage } from "./types";

// Mirrors src/journal/lab/features.py::PRICE_FEATURES verbatim.
export const LAB_FEATURES = [
  "ret_1", "ret_5", "ret_20",
  "atr_rel",
  "ema20_dist", "ema50_dist",
  "body_ratio", "upper_wick", "lower_wick",
  "range_pct",
  "vol_rel",
  "spread",
  "hour_utc", "dow",
] as const;

export type TrainForm = {
  symbol: string;
  timeframe: string;
  n_bars: number;
  k_atr: number;
  rr: number;
  er_threshold: number;
  n_folds: number;
  threshold: number;
  default_spread_points: number;
  features: string[];
};

// Mirrors web/lab_api.py::train's body.get(...) defaults.
export const DEFAULT_TRAIN_FORM: TrainForm = {
  symbol: "XAUUSDc",
  timeframe: "H1",
  n_bars: 24,
  k_atr: 1,
  rr: 2,
  er_threshold: 0.35,
  n_folds: 5,
  threshold: 0.5,
  default_spread_points: 0,
  features: [...LAB_FEATURES],
};

export type TrainResponse = {
  model_ids: number[];
  models: LabModel[];
  dropped_features: Record<string, number>;
  spread_assumed: boolean;
  n_bars_read: number;
};

// Muted enough to sit behind candles without competing with them.
const REGIME_COLORS: Record<string, string> = {
  trend_up: tint(palette.pos, 0.1),
  trend_down: tint(palette.neg, 0.1),
  range: white(0.06),
};

export function regimeColor(regime: string): string {
  return REGIME_COLORS[regime] ?? white(0.04);
}

export function formatAge(ms: number | null): string {
  if (ms === null) return "never trained";
  const days = Math.floor(ms / 86_400_000);
  if (days >= 1) return `${days}d ago`;
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `${hours}h ago`;
  return "just now";
}

// Which timeframe to READ for a symbol that has no timeframe selector of its
// own (/live). The page's chart default is M5 while /lab trains H1 by default,
// so asking for the chart default renders "no model trained" on a machine
// that has a perfectly good model — ask the models themselves instead, and
// only fall back when the symbol has nothing trained at all.
export function modelTimeframe(models: LabModel[], fallback: Timeframe): Timeframe {
  const tf = bestModel(models, "timing")?.timeframe;
  return (TIMEFRAMES as string[]).includes(tf ?? "") ? (tf as Timeframe) : fallback;
}

// The active model for a stage, else the newest one trained. Null when the
// stage has no models at all.
export function bestModel(models: LabModel[], stage: LabStage): LabModel | null {
  const ofStage = models.filter((m) => m.stage === stage);
  if (ofStage.length === 0) return null;
  const active = ofStage.find((m) => m.active);
  if (active) return active;
  return ofStage.reduce((a, b) => (b.created_ms > a.created_ms ? b : a));
}

// NOTE: deliberately NOT the house pattern (caller checks .ok/.data, as
// replayApi.ts/storageApi.ts do and as this file's own Task-9 commit
// documents). Lab.tsx (Task 10) is the sole consumer and needs a plain
// resolve-with-payload / reject-on-failure promise — unwrap here instead of
// at the call site so a bad response surfaces as a catchable Error.
export async function trainModels(form: TrainForm): Promise<TrainResponse> {
  const r = await postJson<TrainResponse>("/api/lab/train", form);
  if (!r.ok || !r.data) throw new Error(r.error ?? "training failed");
  return r.data;
}

export async function activateModel(id: number): Promise<{ ok: boolean; id: number }> {
  const r = await postJson<{ ok: boolean; id: number }>(`/api/lab/models/${id}/activate`, {});
  if (!r.ok || !r.data) throw new Error(r.error ?? "activation failed");
  return r.data;
}

async function getJson<T>(path: string, params: Record<string, string>): Promise<T> {
  const r = await fetch(`${path}?${new URLSearchParams(params)}`);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const b = await r.json();
      if (b && typeof b.error === "string") msg = b.error;
    } catch {
      /* non-JSON error page — keep HTTP {status} */
    }
    throw new Error(msg);
  }
  return (await r.json()) as T;
}

// `timeframe` is optional — /live asks for every timeframe a symbol has, then
// picks one with modelTimeframe (the API's own filter is optional too).
export function fetchModels(symbol: string, timeframe?: string) {
  return getJson<{ models: LabModel[] }>(
    "/api/lab/models", timeframe ? { symbol, timeframe } : { symbol });
}

export function fetchScore(symbol: string, timeframe: string, bars = 300) {
  return getJson<LabScore>("/api/lab/score", { symbol, timeframe, bars: String(bars) });
}

export function fetchRegimes(symbol: string, timeframe: string, fromMs: number, toMs: number) {
  return getJson<LabScore>("/api/lab/regimes", {
    symbol, timeframe, from_ms: String(Math.floor(fromMs)), to_ms: String(Math.floor(toMs)),
  });
}
