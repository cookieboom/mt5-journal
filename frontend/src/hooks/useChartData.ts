import { useCallback, useEffect, useRef, useState } from "react";
import { backfillWindow, capCandles, fetchCandles, initialWindow, mergeCandles, olderWindow, timeframeMs, type Timeframe } from "../lib/candles";
import type { Candle } from "../lib/types";

export type ChartStatus = "loading" | "polling" | "ready" | "gaveup" | "error";

const POLL_MS = 2000;
const MAX_POLLS = 5;      // ~10s total, then give up (journal live likely not running)

export function useChartData(symbol: string, tf: Timeframe, initialBars: number, maxBars: number) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState<ChartStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const fromRef = useRef<number>(0);          // oldest loaded window bound (ms)
  const pollRef = useRef<number>(0);          // poll attempts for the current window
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);
  // Bumped on every symbol/tf reset and every retry(). Each load() call
  // captures the generation in force when IT started; any result (success or
  // error) that arrives after the generation has moved on is a no-op. This is
  // what stops a still-filling window's poll cycle from being corrupted by a
  // stale fetch that was in flight when the user switched symbol/tf or hit retry.
  const genRef = useRef(0);
  // loadOlder is a one-shot historical fetch, independent of the fill-poll
  // cycle above. This guard collapses the burst of onRequestOlder calls a
  // single pan gesture fires (subscribeVisibleLogicalRangeChange keeps
  // reporting barsBefore < 20 on every frame of the drag) into one in-flight
  // fetch instead of a stampede of overlapping requests.
  const loadingOlderRef = useRef(false);
  // True once the in-memory array has reached maxBars. loadOlder becomes a
  // no-op so a pan can't keep re-loading bars that capCandles would re-drop.
  // Reset on every symbol/tf reset (the effect below) and on retry.
  const atCapRef = useRef(false);
  // False until the CURRENT window has ever yielded a bar. While still false and
  // a fetch comes back empty, load() widens the window backward (backfillWindow)
  // instead of just polling — so an initial view anchored at Date.now() that
  // lands in a weekend/holiday gap walks back to the last real bar rather than
  // rendering blank. Reset on every symbol/tf reset and on retry.
  const hasDataRef = useRef(false);

  const clearTimer = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };

  // Fetch [from,to] for the CURRENT window, merge, and bounded-poll while data
  // is still missing. Owns pollRef/timer/status(polling|gaveup) exclusively —
  // loadOlder below must never touch them.
  const load = useCallback(async (from: number, to: number) => {
    const gen = genRef.current;
    try {
      const resp = await fetchCandles(symbol, tf, from, to);
      if (!alive.current || gen !== genRef.current) return;
      if (resp.candles.length > 0) hasDataRef.current = true;
      setCandles((prev) => {
        const merged = capCandles(mergeCandles(prev, resp.candles), maxBars);
        atCapRef.current = merged.length >= maxBars;
        return merged;
      });
      setError(null);
      // Nothing found yet and this window came back empty → the view is anchored
      // in a market-closed gap. Widen backward and retry immediately (older bars
      // are already stored, so this renders without waiting on a fill) until data
      // appears or the lookback bound is hit. Bounded by maxBars so we never load
      // more than capCandles would keep. Skips the poll cycle for this iteration.
      if (!hasDataRef.current) {
        const wider = backfillWindow(from, to, timeframeMs(tf) * maxBars);
        if (wider) {
          fromRef.current = wider[0];
          clearTimer();
          setStatus("loading");
          return load(wider[0], wider[1]);
        }
        // wider === null: reached the bound with no data anywhere in range — fall
        // through so the missing/poll logic below can still surface pending fills.
      }
      const stillMissing = resp.missing.length > 0;
      if (!stillMissing) { setStatus("ready"); pollRef.current = 0; return; }
      // Missing remains. Aggregated bars may already render (pending true) — that
      // is fine; keep polling bounded so a dead queue can't spin forever.
      if (pollRef.current >= MAX_POLLS) { setStatus("gaveup"); return; }
      setStatus("polling");
      pollRef.current += 1;
      clearTimer();
      timer.current = setTimeout(() => {
        // gen is closed over from THIS load() call, not re-read from genRef —
        // so a poll scheduled by a superseded window/generation stays dead
        // even though genRef.current itself has since moved on and a fresh
        // load() would trivially match it.
        if (gen !== genRef.current) return;
        load(from, to);
      }, POLL_MS);
    } catch (e) {
      if (alive.current && gen === genRef.current) { setError(String(e)); setStatus("error"); }
    }
  }, [symbol, tf, maxBars]);

  // (Re)load from scratch whenever symbol/tf changes.
  useEffect(() => {
    alive.current = true;
    genRef.current += 1;
    clearTimer();
    setCandles([]); setStatus("loading"); setError(null); pollRef.current = 0;
    atCapRef.current = false;
    hasDataRef.current = false;
    const [from, to] = initialWindow(tf, Date.now(), initialBars);
    fromRef.current = from;
    load(from, to);
    return () => { alive.current = false; clearTimer(); };
  }, [symbol, tf, initialBars, load]);

  const retry = useCallback(() => {
    genRef.current += 1;
    clearTimer();
    pollRef.current = 0;
    atCapRef.current = false;
    hasDataRef.current = false;
    setStatus("polling");
    const [, to] = initialWindow(tf, Date.now(), initialBars);
    load(fromRef.current, to);
  }, [tf, initialBars, load]);

  // Pan: extend the loaded window to the left. Does NOT touch pollRef, does
  // NOT schedule a poll, and does NOT flip status to polling/gaveup — it only
  // ever sets status on its own failure path (error), so a bad pan fetch is
  // surfaced rather than silently dropped. fromRef only advances on success,
  // so a failed pan can be retried (a later onRequestOlder recomputes the
  // same window rather than skipping past a gap).
  const loadOlder = useCallback(async () => {
    if (atCapRef.current) return;         // history bound reached — raise maxBars to go further
    if (loadingOlderRef.current) return;
    loadingOlderRef.current = true;
    const gen = genRef.current;
    const [from, to] = olderWindow(fromRef.current, tf);
    try {
      const resp = await fetchCandles(symbol, tf, from, to);
      if (!alive.current || gen !== genRef.current) return;
      fromRef.current = from;
      setCandles((prev) => {
        const merged = capCandles(mergeCandles(prev, resp.candles), maxBars);
        atCapRef.current = merged.length >= maxBars;
        return merged;
      });
      setError(null);
    } catch (e) {
      if (alive.current && gen === genRef.current) { setError(String(e)); setStatus("error"); }
    } finally {
      loadingOlderRef.current = false;
    }
  }, [symbol, tf, maxBars]);

  const lastBarMs = candles.length ? candles[candles.length - 1].time_msc : null;
  return { candles, status, error, lastBarMs, retry, loadOlder };
}
