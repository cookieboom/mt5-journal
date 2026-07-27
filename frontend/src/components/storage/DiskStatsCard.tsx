import type { StorageOverview } from "../../lib/storageApi";

export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const index = Math.min(i, sizes.length - 1);
  return `${parseFloat((bytes / Math.pow(k, index)).toFixed(dm))} ${sizes[index]}`;
}

export interface DiskStatsCardProps {
  overview?: StorageOverview | null;
  loading?: boolean;
}

export default function DiskStatsCard({ overview, loading }: DiskStatsCardProps) {
  if (loading || !overview) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="glass p-4 rounded-xl border border-panel-border animate-pulse">
            <div className="h-4 bg-white/10 rounded w-1/2 mb-3"></div>
            <div className="h-7 bg-white/10 rounded w-3/4 mb-2"></div>
            <div className="h-3 bg-white/5 rounded w-2/3"></div>
          </div>
        ))}
      </div>
    );
  }

  const {
    db_size_bytes,
    wal_size_bytes,
    total_m1_bars,
    total_trades,
    cache_size_bytes,
    cache_files_count,
    symbols,
  } = overview;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {/* 1. Database File Size */}
      <div className="glass p-4 rounded-xl border border-panel-border flex flex-col justify-between">
        <div>
          <div className="flex items-center justify-between text-muted text-xs font-medium mb-1">
            <span>Database Size</span>
            <svg
              className="w-4 h-4 text-cyan"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"
              />
            </svg>
          </div>
          <div className="text-2xl font-bold text-ink num">{formatBytes(db_size_bytes)}</div>
        </div>
        <div className="mt-3 pt-2 border-t border-panel-border/50 text-xs">
          {wal_size_bytes > 0 ? (
            <span className="inline-flex items-center gap-1 text-cyan font-mono bg-cyan/10 px-2 py-0.5 rounded border border-cyan/20">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse" />
              WAL: +{formatBytes(wal_size_bytes)}
            </span>
          ) : (
            <span className="text-muted">WAL Inactive (0 B)</span>
          )}
        </div>
      </div>

      {/* 2. Total M1 Bars */}
      <div className="glass p-4 rounded-xl border border-panel-border flex flex-col justify-between">
        <div>
          <div className="flex items-center justify-between text-muted text-xs font-medium mb-1">
            <span>Total M1 Bars</span>
            <svg
              className="w-4 h-4 text-violet"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
              />
            </svg>
          </div>
          <div className="text-2xl font-bold text-ink num">
            {total_m1_bars.toLocaleString()}
          </div>
        </div>
        <div className="mt-3 pt-2 border-t border-panel-border/50 text-xs text-muted">
          {symbols.length} symbol{symbols.length === 1 ? "" : "s"} tracked
        </div>
      </div>

      {/* 3. Reconstructed Trades */}
      <div className="glass p-4 rounded-xl border border-panel-border flex flex-col justify-between">
        <div>
          <div className="flex items-center justify-between text-muted text-xs font-medium mb-1">
            <span>Reconstructed Trades</span>
            <svg
              className="w-4 h-4 text-pos"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
              />
            </svg>
          </div>
          <div className="text-2xl font-bold text-ink num">
            {total_trades.toLocaleString()}
          </div>
        </div>
        <div className="mt-3 pt-2 border-t border-panel-border/50 text-xs text-muted">
          Deals & positions aggregated
        </div>
      </div>

      {/* 4. Cache Size & File Count */}
      <div className="glass p-4 rounded-xl border border-panel-border flex flex-col justify-between">
        <div>
          <div className="flex items-center justify-between text-muted text-xs font-medium mb-1">
            <span>PNG & Weekly Cache</span>
            <svg
              className="w-4 h-4 text-cyan"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 8h14M5 8a2 2 0 01-2-2V5a2 2 0 012-2h14a2 2 0 012 2v1a2 2 0 01-2 2M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"
              />
            </svg>
          </div>
          <div className="text-2xl font-bold text-ink num">
            {formatBytes(cache_size_bytes)}
          </div>
        </div>
        <div className="mt-3 pt-2 border-t border-panel-border/50 text-xs text-muted font-mono">
          {cache_files_count.toLocaleString()} file{cache_files_count === 1 ? "" : "s"}
        </div>
      </div>
    </div>
  );
}

export { DiskStatsCard };
