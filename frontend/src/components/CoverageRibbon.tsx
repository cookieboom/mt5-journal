import { useMemo } from "react";
import type { Candle } from "../lib/types";
import { classifyGaps } from "../lib/coverage";
import type { Timeframe } from "../lib/candles";
import { palette } from "../lib/theme";

const COLOR = {
  covered: palette.cyan, unfetched: palette.neg, closed: palette["closed-slate"],
};

export default function CoverageRibbon({
  bars, missing, window, tf, onBackfill,
}: {
  bars: Candle[]; missing: [number, number][];
  window: [number, number]; tf: Timeframe; onBackfill?: () => void;
}) {
  const segs = useMemo(() => classifyGaps(bars, missing, window, tf), [bars, missing, window, tf]);
  const span = Math.max(1, window[1] - window[0]);
  const holes = segs.filter((s) => s.kind === "unfetched").length;
  return (
    <div className="mt-1">
      <div className="flex h-1.5 w-full overflow-hidden rounded">
        {segs.map((s, i) => (
          <div key={i} title={s.kind}
            style={{ width: `${((s.to - s.from) / span) * 100}%`, background: COLOR[s.kind] }} />
        ))}
      </div>
      <div className="mt-1 flex items-center gap-2 text-meta text-muted">
        {holes > 0 ? (
          <>
            <span className="text-neg">{holes} lubang belum di-fetch di tampilan ini</span>
            {onBackfill && (
              <button onClick={onBackfill}
                className="px-2 py-0.5 rounded bg-white/5 hover:bg-white/10">Backfill</button>
            )}
          </>
        ) : (
          <span>data tampilan lengkap</span>
        )}
      </div>
    </div>
  );
}
