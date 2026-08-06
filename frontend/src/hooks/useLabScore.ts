import { useEffect, useState } from "react";
import { fetchScore } from "../lib/lab";
import type { LabScore } from "../lib/types";

/** Refetches once per closed bar. The models were trained on closed bars, so an
 *  intrabar score would be a different and untested quantity.
 *
 *  fetchScore throws on failure (lib/lab.ts is throw-style, not envelope-style
 *  — see its own header comment). Uncaught, that would surface as an
 *  unhandled promise rejection from the fire-and-forget `void load()`/
 *  `window.setTimeout(load, ...)` calls below; caught here into `error` so a
 *  caller can render a visible failure instead. */
export function useLabScore(symbol: string, timeframe: string, timeframeMs: number) {
  const [score, setScore] = useState<LabScore | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;

    const load = async () => {
      try {
        const next = await fetchScore(symbol, timeframe);
        if (alive) { setScore(next); setError(null); }
      } catch (e) {
        if (alive) { setScore(null); setError(e instanceof Error ? e.message : String(e)); }
      } finally {
        if (alive) setLoading(false);
      }
      if (!alive) return;
      const msToNextBar = timeframeMs - (Date.now() % timeframeMs) + 1_000;
      timer = window.setTimeout(load, msToNextBar);
    };

    void load();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [symbol, timeframe, timeframeMs]);

  return { score, loading, error };
}
