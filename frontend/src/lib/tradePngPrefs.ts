// Mirrors the Python RenderOpts/normalize_opts contract on the client. Accepts
// either camelCase (UI) or snake_case (DB blob, matching the Python field
// names) keys — normalizeTradePng/fromApi are the same function; toApi maps
// back to the snake_case shape the DB stores.
import { TIMEFRAMES, type Timeframe } from "./candles";

export type PngTheme = "charles" | "nightclouds" | "yahoo";
export const THEMES: PngTheme[] = ["charles", "nightclouds", "yahoo"];
export const TF_OPTIONS: (Timeframe | null)[] = [null, ...TIMEFRAMES];
export const PAD_MIN = 5, PAD_MAX = 120;

export interface TradePngSettings {
  theme: PngTheme;
  padBars: number;
  tfOverride: Timeframe | null;
  showSltp: boolean;
  showMarkers: boolean;
  showVolume: boolean;
  showGrid: boolean;
}

export const DEFAULT_TRADE_PNG: TradePngSettings = {
  theme: "charles", padBars: 15, tfOverride: null,
  showSltp: true, showMarkers: true, showVolume: false, showGrid: true,
};

const clampPad = (v: unknown): number => {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : DEFAULT_TRADE_PNG.padBars;
  return Math.min(PAD_MAX, Math.max(PAD_MIN, n));
};
const bool = (v: unknown, d: boolean) => (typeof v === "boolean" ? v : d);

// Accepts either camelCase (UI) or snake_case (DB blob) keys.
export function normalizeTradePng(raw: unknown): TradePngSettings {
  if (raw === null || typeof raw !== "object") return { ...DEFAULT_TRADE_PNG };
  const p = raw as Record<string, unknown>;
  const theme = p.theme;
  const tf = (p.tfOverride ?? p.tf_override) as unknown;
  return {
    theme: THEMES.includes(theme as PngTheme) ? (theme as PngTheme) : "charles",
    padBars: clampPad(p.padBars ?? p.pad_bars),
    tfOverride: (TIMEFRAMES as string[]).includes(tf as string) ? (tf as Timeframe) : null,
    showSltp: bool(p.showSltp ?? p.show_sltp, true),
    showMarkers: bool(p.showMarkers ?? p.show_markers, true),
    showVolume: bool(p.showVolume ?? p.show_volume, false),
    showGrid: bool(p.showGrid ?? p.show_grid, true),
  };
}

// DB blob uses snake_case matching the Python RenderOpts fields.
export function toApi(s: TradePngSettings): Record<string, unknown> {
  return {
    theme: s.theme, pad_bars: s.padBars, tf_override: s.tfOverride,
    show_sltp: s.showSltp, show_markers: s.showMarkers,
    show_volume: s.showVolume, show_grid: s.showGrid,
  };
}
export const fromApi = (raw: unknown): TradePngSettings => normalizeTradePng(raw);
