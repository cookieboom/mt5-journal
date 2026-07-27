import { postJson } from "./api";

export interface StorageOverview {
  db_size_bytes: number;
  wal_size_bytes: number;
  total_m1_bars: number;
  total_trades: number;
  cache_size_bytes: number;
  cache_files_count: number;
  symbols: string[];
}

export interface CoveredRange {
  from_ms: number;
  to_ms: number;
}

export interface GapItem {
  from_ms: number;
  to_ms: number;
  duration_hours: number;
}

export interface CandleCompleteness {
  symbol: string;
  timeframe: string;
  total_bars: number;
  from_ms: number;
  to_ms: number;
  coverage_percent: number;
  covered_ranges: CoveredRange[];
  gaps: GapItem[];
}

export interface ClearCacheResult {
  cleared_files: number;
  freed_bytes: number;
}

export interface VacuumResult {
  status: string;
  db_size_after: number;
}

export interface RebuildResult {
  status: string;
  trades_rebuilt: number;
}

export interface BackfillResult {
  status: string;
  request_id: number;
}

export interface FillGapsResult {
  status: string;
  requests_count: number;
}

export interface PruneResult {
  status: string;
  deleted_bars: number;
}

export async function fetchStorageOverview(): Promise<StorageOverview> {
  const r = await fetch("/api/storage/overview");
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? `HTTP ${r.status}`);
  }
  return (await r.json()) as StorageOverview;
}

export function clearCache() {
  return postJson<ClearCacheResult>("/api/storage/maintenance/clear-cache", {});
}

export function vacuumDb() {
  return postJson<VacuumResult>("/api/storage/maintenance/vacuum", {});
}

export function rebuildTrades() {
  return postJson<RebuildResult>("/api/storage/maintenance/rebuild", {});
}

export async function fetchCompleteness(
  symbol: string,
  tf: string = "M1"
): Promise<CandleCompleteness> {
  const params = new URLSearchParams({ symbol, tf });
  const r = await fetch(`/api/storage/candles/completeness?${params.toString()}`);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? `HTTP ${r.status}`);
  }
  return (await r.json()) as CandleCompleteness;
}

export function fetchBackfill(
  symbol: string,
  tf: string,
  from_ms: number,
  to_ms: number
) {
  return postJson<BackfillResult>("/api/storage/candles/fetch", {
    symbol,
    timeframe: tf,
    from_ms,
    to_ms,
  });
}

export function fillAllGaps(symbol: string, tf: string) {
  return postJson<FillGapsResult>("/api/storage/candles/fill-gaps", {
    symbol,
    timeframe: tf,
  });
}

export function pruneCandles(symbol?: string, older_than_days?: number) {
  return postJson<PruneResult>("/api/storage/candles/prune", {
    symbol: symbol ?? "all",
    older_than_days: older_than_days ?? 180,
  });
}

export function getExportUrl(
  symbol: string,
  tf: string,
  format: "csv" | "json" = "json",
  from_ms?: number,
  to_ms?: number
): string {
  const params = new URLSearchParams({ symbol, tf, format });
  if (from_ms !== undefined) params.set("from_ms", String(from_ms));
  if (to_ms !== undefined) params.set("to_ms", String(to_ms));
  return `/api/storage/candles/export?${params.toString()}`;
}
