import { useCallback, useEffect, useRef, useState } from "react";
import { backfillWindow, bucketStart, capCandles, fetchCandles, initialWindow, mergeCandles, olderWindow, timeframeMs, type Timeframe } from "../lib/candles";
import type { Candle } from "../lib/types";

export type ChartStatus = "loading" | "polling" | "ready" | "gaveup" | "error";

const POLL_MS = 2000;
const MAX_POLLS = 5;      // ~10s total, then give up (journal live likely not running)
const FORWARD_CHUNK = 200; // bars fetched ahead per loadUpTo (batches replay stepping)

// `anchorMs` sets the RIGHT edge of the initial window. Live mode leaves it
// undefined → anchors at Date.now(). Replay passes the session's start cursor so
// the initial fetch lands on the chosen historical date instead of "now" — the
// now-anchored window would hold only recent bars, which clipToCursor(<=cursor)
// then filters to nothing (blank chart). See useReplaySession.anchorMsc.
export function useChartData(
  symbol: string, tf: Timeframe, initialBars: number, maxBars: number,
  anchorMs?: number,
) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState<ChartStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<[number, number][]>([]);
  const fromRef = useRef<number>(0);          // oldest loaded window bound (ms)
  const toRef = useRef<number>(0);            // newest loaded window bound (ms)
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
  const loadingNewerRef = useRef(false);      // same collapse-the-burst guard, forward side
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
      setMissing(resp.missing as [number, number][]);
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
    const [from, to] = initialWindow(tf, anchorMs ?? Date.now(), initialBars);
    fromRef.current = from;
    toRef.current = to;
    load(from, to);
    return () => { alive.current = false; clearTimer(); };
  }, [symbol, tf, initialBars, anchorMs, load]);

  const retry = useCallback(() => {
    genRef.current += 1;
    clearTimer();
    pollRef.current = 0;
    atCapRef.current = false;
    hasDataRef.current = false;
    setStatus("polling");
    const [, to] = initialWindow(tf, anchorMs ?? Date.now(), initialBars);
    toRef.current = Math.max(toRef.current, to);
    load(fromRef.current, to);
  }, [tf, initialBars, anchorMs, load]);

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

  // Replay: extend the loaded window to the RIGHT so a forward-advancing reveal
  // cursor never outruns the loaded bars. No-op once the target is already
  // covered. Fetches a FORWARD_CHUNK past the target so stepping doesn't fire a
  // request per bar. Mirrors loadOlder: never touches pollRef/timer/status
  // (only its own error path).
  // The FORWARD_CHUNK lookahead is only valid up to real "now" — replay data
  // beyond the cursor already exists, but live data beyond "now" doesn't.
  // Clamping here keeps toRef.current from ever being set into a future that
  // hasn't happened yet: without it, live's tail-follow (Chart.tsx) would set
  // toRef.current to targetMs + 200 bars on its first call, then its own
  // `targetMs <= toRef.current` guard above would silently block every later
  // bridge until wall-clock time caught up to that fictitious point (hours,
  // for M1) — the historical window would freeze again after one bar.
  // toRef.current only ever advances up to what `missing` confirms is
  // actually covered, never blindly to the requested `to`. In live mode the
  // just-closed bar can still be `missing`/`pending` here — `journal live`
  // promotes a closed bar into `candles` on its own ~1-5s cycle, which races
  // the frontend's poll. Advancing past an unconfirmed span anyway would
  // drop that bar forever, since the next call's `from` starts exactly where
  // this one left off and nothing else ever re-requests bars behind toRef.
  // Leaving toRef short of `to` means the next rollover naturally retries
  // the same gap instead.
  const loadUpTo = useCallback(async (targetMs: number) => {
    if (loadingNewerRef.current) return;
    if (targetMs <= toRef.current) return;    // already loaded past the cursor
    loadingNewerRef.current = true;
    const gen = genRef.current;
    // Start at the BUCKET START of the cursor, not the raw instant. toRef starts
    // life as Date.now() (chart opened mid-bar) and later advances to whatever
    // the backend confirmed covered, which is also mid-bucket whenever the
    // window ran up to `now`. The backend selects `time_msc BETWEEN from AND to`,
    // so a mid-bucket `from` skips that bucket's bar for good — its time_msc is
    // the bucket START, which sits BEFORE `from`, and nothing ever re-requests
    // behind toRef. Reported as: open /chart, and the bar that was forming at
    // that moment is missing from the chart forever (one bar, once per open).
    // Re-fetching one already-held bar each rollover is free — mergeCandles
    // dedupes by time_msc.
    const from = bucketStart(toRef.current, tf);
    const to = Math.min(targetMs + timeframeMs(tf) * FORWARD_CHUNK, Date.now());
    try {
      const resp = await fetchCandles(symbol, tf, from, to);
      if (!alive.current || gen !== genRef.current) return;
      setCandles((prev) => capCandles(mergeCandles(prev, resp.candles), maxBars));
      setError(null);
      const missing = resp.missing as [number, number][];
      setMissing(missing);
      // A window fetched up to Date.now() always straddles the still-forming
      // bar, so `missing` here is never actually empty in live mode — it
      // can't be used as an "all caught up" signal. Forward progress
      // (coveredTo advancing past the previous toRef.current) is: real
      // closed bars got confirmed since the last call. Tail-follow runs
      // independently of load()'s poll cycle, which owns status/pollRef; if
      // that cycle already latched "gaveup" (initial window had a stale gap)
      // before journal live caught up, nothing else ever clears it — the
      // chart keeps rendering fresh live bars while the banner stays stuck.
      // Confirmed progress here is proof journal live is alive, so clear it.
      const coveredTo = missing.length ? Math.min(to, missing[0][0]) : to;
      if (coveredTo > toRef.current) {
        toRef.current = coveredTo;
        setStatus((s) => (s === "gaveup" || s === "error" ? "ready" : s));
      }
    } catch (e) {
      if (alive.current && gen === genRef.current) { setError(String(e)); setStatus("error"); }
    } finally {
      loadingNewerRef.current = false;
    }
  }, [symbol, tf, maxBars]);

  const lastBarMs = candles.length ? candles[candles.length - 1].time_msc : null;
  return { candles, status, error, lastBarMs, missing, retry, loadOlder, loadUpTo };
}
