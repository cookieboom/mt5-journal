// Runs the Strategy Tester for one indicator instance. The first run is a
// click (nothing is computed until asked); after that the result is kept
// current — re-run, debounced, on every settings change and whenever the
// chart's last CLOSED bar moves. Switching symbol/TF/script drops the result:
// markers must never sit beside another context's numbers (rule 9).
import { useCallback, useEffect, useRef, useState } from "react";
import type { IndicatorInstance } from "../lib/indicators";
import { backtestApi, backtestBody, normalizeBacktest, type BacktestResult } from "../lib/backtest";

const DEBOUNCE_MS = 800;
const PENDING_RETRY_MS = 3000;

export function useBacktest(a: {
  symbol: string;
  tf: string;
  item: IndicatorInstance | null;
  lastClosedMs: number | null;
  rev: number;            // bumped when a script's source changes
}) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [armed, setArmed] = useState(false);
  const [retry, setRetry] = useState(0);

  const ctxKey = JSON.stringify([a.symbol, a.tf, a.item?.id ?? null, a.item?.script ?? null]);
  const settings = normalizeBacktest(a.item?.backtest);
  const runKey = JSON.stringify([ctxKey, a.item?.inputs ?? null, settings, a.rev, a.lastClosedMs, retry]);

  useEffect(() => { setResult(null); setError(null); setArmed(false); }, [ctxKey]);

  const latest = useRef(a);
  latest.current = a;
  const gen = useRef(0);

  useEffect(() => {
    const cur = latest.current;
    if (!armed || !cur.item || cur.lastClosedMs === null) return;
    const g = ++gen.current;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const t = setTimeout(async () => {
      setLoading(true);
      const r = await backtestApi.run(backtestBody({
        script: cur.item!.script, inputs: cur.item!.inputs, symbol: cur.symbol, tf: cur.tf,
        toMs: cur.lastClosedMs!, s: normalizeBacktest(cur.item!.backtest),
      }));
      if (g !== gen.current) return;
      setLoading(false);
      if (r.ok && r.data) {
        setResult(r.data); setError(null);
        if (r.data.pending) retryTimer = setTimeout(() => setRetry((x) => x + 1), PENDING_RETRY_MS);
      } else {
        setResult(null); setError(r.error ?? "gagal menjalankan tester");
      }
    }, DEBOUNCE_MS);
    return () => { clearTimeout(t); if (retryTimer) clearTimeout(retryTimer); };
  }, [armed, runKey]);

  const run = useCallback(() => { setArmed(true); setRetry((x) => x + 1); }, []);
  return { result, error, loading, armed, run, settings };
}
