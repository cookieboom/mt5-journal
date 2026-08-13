import { useMemo, useState } from "react";
import type { Candle } from "../lib/types";
import { classifyGaps } from "../lib/coverage";
import { postJson } from "../lib/api";
import { wib } from "../lib/format";
import type { Timeframe } from "../lib/candles";

export default function DataHealthPanel({
  bars, missing, window, tf, symbol, onBackfilled,
}: {
  bars: Candle[]; missing: [number, number][]; window: [number, number];
  tf: Timeframe; symbol: string; onBackfilled?: () => void;
}) {
  const segs = useMemo(() => classifyGaps(bars, missing, window, tf), [bars, missing, window, tf]);
  const holes = segs.filter((s) => s.kind === "unfetched");
  const coveredMs = segs.filter((s) => s.kind !== "unfetched").reduce((a, s) => a + (s.to - s.from), 0);
  const pct = Math.round((coveredMs / Math.max(1, window[1] - window[0])) * 100);
  const [busy, setBusy] = useState(false);

  const backfill = async () => {
    setBusy(true);
    await postJson("/api/backfill", { symbol, timeframe: tf, from_ms: window[0], to_ms: window[1] });
    setBusy(false);
    onBackfilled?.();
  };

  return (
    <div className="text-body rounded-lg bg-white/5 p-3">
      <div className="flex items-center justify-between">
        <span className="font-medium">Data health · {symbol} {tf}</span>
        <span className={pct >= 100 ? "text-cyan" : "text-neg"}>{pct}% tercover</span>
      </div>
      <div className="mt-2 text-muted">
        {holes.length === 0 ? "Tak ada lubang belum di-fetch di tampilan ini."
          : `${holes.length} lubang belum di-fetch:`}
      </div>
      {holes.length > 0 && (
        <ul className="mt-1 max-h-28 overflow-auto text-muted">
          {holes.map((h, i) => <li key={i}>{wib(h.from)} — {wib(h.to)}</li>)}
        </ul>
      )}
      <button disabled={busy || holes.length === 0} onClick={backfill}
        className="mt-2 px-2.5 py-1 rounded bg-cyan/15 text-cyan disabled:opacity-40">
        {busy ? "Mengantrikan…" : "Backfill rentang terlihat"}
      </button>
    </div>
  );
}
