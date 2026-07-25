// URL-param selection + persisted chart appearance. Selection defaults to
// XAUUSDc / M5 (overridable by saved defaults). Settings are versioned (v1);
// a legacy Phase B object {theme,grid} (no version) migrates in place. DB
// persistence + localStorage mirror live in hooks/useChartPrefs.ts.
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "./candles";

export type ChartTheme = "dark" | "light";
export type ChartType = "candle" | "bar" | "line" | "area";
export type CrosshairStyle = "normal" | "magnet" | "hidden";
export type PriceScaleMode = "linear" | "log";

export interface ChartSettings {
  version: 1;
  theme: ChartTheme;
  grid: boolean;
  colors: { up: string; down: string; wick: string };
  chartType: ChartType;
  crosshair: CrosshairStyle;
  priceScale: PriceScaleMode;
  autoScale: boolean;
  lastPriceLine: boolean;
  liveOverlay: boolean;
  defaultSymbol: Sym;
  defaultTimeframe: Timeframe;
  initialBars: number;
  maxBars: number;
}

export const DEFAULT_SETTINGS: ChartSettings = {
  version: 1,
  theme: "dark",
  grid: true,
  colors: { up: "#34d399", down: "#fb7185", wick: "#9a97c4" },
  chartType: "candle",
  crosshair: "normal",
  priceScale: "linear",
  autoScale: true,
  lastPriceLine: true,
  liveOverlay: true,
  defaultSymbol: "XAUUSDc",
  defaultTimeframe: "M5",
  initialBars: 300,
  maxBars: 3000,
};

const KEY = "mt5j.chart.settings";

// Bounds — also the numbers shown next to the inputs in the drawer.
const INITIAL_MIN = 100, INITIAL_MAX = 1000;
const MAX_MIN = 500, MAX_MAX = 10000;

function clampInt(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : fallback;
  return Math.min(hi, Math.max(lo, n));
}
function hex(v: unknown, fallback: string): string {
  return typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v) ? v : fallback;
}
function oneOf<T extends string>(v: unknown, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(v as string) ? (v as T) : fallback;
}

// Coerce any stored/DB object (legacy or corrupt) into a valid v1 ChartSettings.
// Legacy Phase B objects lack `version`; their theme/grid are kept, everything
// else filled from defaults. Numeric fields are clamped; maxBars is raised to
// initialBars if smaller.
export function normalizeSettings(raw: unknown): ChartSettings {
  if (raw === null || typeof raw !== "object") return { ...DEFAULT_SETTINGS };
  const p = raw as Record<string, unknown>;
  const c = (p.colors ?? {}) as Record<string, unknown>;
  const D = DEFAULT_SETTINGS;
  const initialBars = clampInt(p.initialBars, INITIAL_MIN, INITIAL_MAX, D.initialBars);
  let maxBars = clampInt(p.maxBars, MAX_MIN, MAX_MAX, D.maxBars);
  if (maxBars < initialBars) maxBars = initialBars;
  return {
    version: 1,
    theme: oneOf(p.theme, ["dark", "light"] as const, D.theme),
    grid: typeof p.grid === "boolean" ? p.grid : D.grid,
    colors: {
      up: hex(c.up, D.colors.up),
      down: hex(c.down, D.colors.down),
      wick: hex(c.wick, D.colors.wick),
    },
    chartType: oneOf(p.chartType, ["candle", "bar", "line", "area"] as const, D.chartType),
    crosshair: oneOf(p.crosshair, ["normal", "magnet", "hidden"] as const, D.crosshair),
    priceScale: oneOf(p.priceScale, ["linear", "log"] as const, D.priceScale),
    autoScale: typeof p.autoScale === "boolean" ? p.autoScale : D.autoScale,
    lastPriceLine: typeof p.lastPriceLine === "boolean" ? p.lastPriceLine : D.lastPriceLine,
    liveOverlay: typeof p.liveOverlay === "boolean" ? p.liveOverlay : D.liveOverlay,
    defaultSymbol: oneOf(p.defaultSymbol, SYMBOLS, D.defaultSymbol),
    defaultTimeframe: oneOf(p.defaultTimeframe, TIMEFRAMES, D.defaultTimeframe),
    initialBars,
    maxBars,
  };
}

export function loadChartSettings(store: Storage = localStorage): ChartSettings {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveChartSettings(s: ChartSettings, store: Storage = localStorage): void {
  try {
    store.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota / private mode — appearance-only, safe to ignore */
  }
}

// DB is authoritative. Present -> DB wins (normalized). Absent -> keep local; if
// the browser actually had a stored row, seed the DB from it (shouldImport).
export function reconcilePrefs(
  local: ChartSettings, dbParsed: unknown, localExists: boolean,
): { settings: ChartSettings; shouldImport: boolean } {
  if (dbParsed !== null && dbParsed !== undefined) {
    return { settings: normalizeSettings(dbParsed), shouldImport: false };
  }
  return { settings: local, shouldImport: localExists };
}

export function parseSelection(
  params: URLSearchParams,
  defaults: { symbol: Sym; tf: Timeframe } = { symbol: "XAUUSDc", tf: "M5" },
): { symbol: Sym; tf: Timeframe } {
  const s = params.get("symbol");
  const t = params.get("tf");
  return {
    symbol: (SYMBOLS as string[]).includes(s ?? "") ? (s as Sym) : defaults.symbol,
    tf: (TIMEFRAMES as string[]).includes(t ?? "") ? (t as Timeframe) : defaults.tf,
  };
}

// The localStorage key, exported so useChartPrefs can probe existence for the
// import decision.
export const STORAGE_KEY = KEY;
