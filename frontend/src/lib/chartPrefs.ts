// URL-param selection + persisted chart appearance. Selection defaults to
// XAUUSDc / M5. Settings persist in localStorage (Phase B scope: theme + grid;
// the full settings panel + broader preferences are Phase C).
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "./candles";

export type ChartTheme = "dark" | "light";
export interface ChartSettings {
  theme: ChartTheme;
  grid: boolean;
}
export const DEFAULT_SETTINGS: ChartSettings = { theme: "dark", grid: true };
const KEY = "mt5j.chart.settings";

export function loadChartSettings(store: Storage = localStorage): ChartSettings {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const p = JSON.parse(raw) as Partial<ChartSettings>;
    return {
      theme: p.theme === "light" ? "light" : "dark",
      grid: typeof p.grid === "boolean" ? p.grid : true,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveChartSettings(s: ChartSettings, store: Storage = localStorage): void {
  try {
    store.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota / private mode — appearance-only, safe to ignore */
  }
}

export function parseSelection(params: URLSearchParams): { symbol: Sym; tf: Timeframe } {
  const s = params.get("symbol");
  const t = params.get("tf");
  return {
    symbol: (SYMBOLS as string[]).includes(s ?? "") ? (s as Sym) : "XAUUSDc",
    tf: (TIMEFRAMES as string[]).includes(t ?? "") ? (t as Timeframe) : "M5",
  };
}
