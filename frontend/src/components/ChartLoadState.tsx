import type { ChartStatus } from "../hooks/useChartData";

interface LoadState {
  status: ChartStatus;
  error: string | null;
  onRetry: () => void;
}

/** In place of the chart while there are no bars to draw yet. */
export function ChartPlaceholder({ status, error, onRetry, symbol, tf }: LoadState & {
  symbol: string; tf: string;
}) {
  return (
    <div className="glass h-full flex items-center justify-center text-muted text-body">
      {status === "loading" || status === "polling" ? (
        <span>Memuat data {symbol} {tf}…</span>
      ) : status === "gaveup" ? (
        <div className="text-center">
          <div>Belum ada data ter-cache untuk rentang ini.</div>
          <div className="mt-1">Jalankan <code>journal live</code> untuk mengisi cache.</div>
          <button onClick={onRetry} className="glass mt-2 px-3 py-1 text-cyan">Coba lagi</button>
        </div>
      ) : (
        <span className="text-neg">Gagal memuat: {error}</span>
      )}
    </div>
  );
}

/** A non-blocking banner over a chart that already shows bars. */
export function ChartLoadBanner({ status, error, onRetry }: LoadState) {
  if (status === "loading" || status === "polling") {
    return <div className="glass absolute top-2 left-2 px-2 py-1 text-meta text-muted">memuat data…</div>;
  }
  if (status === "gaveup") {
    return (
      <div className="glass absolute top-2 left-2 px-2 py-1 text-meta text-muted flex items-center gap-2">
        <span>Data belum lengkap — jalankan <code>journal live</code>.</span>
        <button onClick={onRetry} className="text-cyan">Coba lagi</button>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="glass absolute top-2 left-2 px-2 py-1 text-meta text-neg flex items-center gap-2">
        <span>Gagal memuat: {error}</span>
        <button onClick={onRetry} className="text-cyan">Coba lagi</button>
      </div>
    );
  }
  return null;
}
