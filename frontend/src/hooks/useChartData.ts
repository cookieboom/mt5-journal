import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCandles, initialWindow, mergeCandles, olderWindow, type Timeframe } from "../lib/candles";
import type { Candle } from "../lib/types";

export type ChartStatus = "loading" | "polling" | "ready" | "gaveup" | "error";

const POLL_MS = 2000;
const MAX_POLLS = 5;      // ~10s total, then give up (journal live likely not running)

export function useChartData(symbol: string, tf: Timeframe) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState<ChartStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const fromRef = useRef<number>(0);          // oldest loaded window bound (ms)
  const pollRef = useRef<number>(0);          // poll attempts for the current window
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const clearTimer = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };

  // Fetch [from,to], merge, and decide whether to keep polling this window.
  const load = useCallback(async (from: number, to: number, _isPoll: boolean) => {
    try {
      const resp = await fetchCandles(symbol, tf, from, to);
      if (!alive.current) return;
      setCandles((prev) => mergeCandles(prev, resp.candles));
      setError(null);
      const stillMissing = resp.missing.length > 0;
      if (!stillMissing) { setStatus("ready"); pollRef.current = 0; return; }
      // Missing remains. Aggregated bars may already render (pending true) — that
      // is fine; keep polling bounded so a dead queue can't spin forever.
      if (pollRef.current >= MAX_POLLS) { setStatus("gaveup"); return; }
      setStatus("polling");
      pollRef.current += 1;
      clearTimer();
      timer.current = setTimeout(() => load(from, to, true), POLL_MS);
    } catch (e) {
      if (alive.current) { setError(String(e)); setStatus("error"); }
    }
  }, [symbol, tf]);

  // (Re)load from scratch whenever symbol/tf changes.
  useEffect(() => {
    alive.current = true;
    setCandles([]); setStatus("loading"); setError(null); pollRef.current = 0;
    const [from, to] = initialWindow(tf, Date.now());
    fromRef.current = from;
    load(from, to, false);
    return () => { alive.current = false; clearTimer(); };
  }, [symbol, tf, load]);

  const retry = useCallback(() => {
    pollRef.current = 0;
    setStatus("polling");
    const [, to] = initialWindow(tf, Date.now());
    load(fromRef.current, to, false);
  }, [tf, load]);

  // Pan: extend the loaded window to the left. Does not disturb the poll state.
  const loadOlder = useCallback(() => {
    const [from, to] = olderWindow(fromRef.current, tf);
    fromRef.current = from;
    load(from, to, false);
  }, [tf, load]);

  const lastBarMs = candles.length ? candles[candles.length - 1].time_msc : null;
  return { candles, status, error, lastBarMs, retry, loadOlder };
}
