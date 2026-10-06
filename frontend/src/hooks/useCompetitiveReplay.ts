import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { outcomeCounts, summarize, type TrainingPosition } from "../lib/replay";
import { timeframeMs } from "../lib/candles";
import type { ReplayConfig, useReplaySession } from "./useReplaySession";
import type { ReplayFormPrefs } from "../lib/replayPrefs";

type Replay = ReturnType<typeof useReplaySession>;

export interface EvalPause { pnl: number; isSkip: boolean }

/**
 * Competitive replay: a run of random scenarios, scored as one. When every
 * position in a scenario has closed, the result shows for a moment and the
 * next scenario starts; after `competitiveRounds` scenarios the run ends.
 *
 * Each scenario is its own backend session, so the closed positions of the
 * finished ones are carried here — otherwise the stats would reset every round.
 */
export function useCompetitiveReplay(
  replay: Replay,
  prefs: ReplayFormPrefs,
  active: boolean,
  onFinished: () => void,
) {
  const [round, setRound] = useState(1);
  const [closed, setClosed] = useState<TrainingPosition[]>([]);
  const [evalPause, setEvalPause] = useState<EvalPause | null>(null);
  const prevPosCount = useRef(0);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  // The result/skip pause is a timer that starts the NEXT scenario. It must die
  // with the run: leaving replay mid-pause used to start a new session anyway.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const later = useCallback((ms: number, fn: () => void) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { timer.current = null; fn(); }, ms);
  }, []);
  const cancel = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => cancel, [cancel]);

  /** A fresh run: round 1, no carried results, no pause pending. */
  const reset = useCallback(() => {
    cancel();
    setRound(1);
    setClosed([]);
    setEvalPause(null);
    prevPosCount.current = 0;
  }, [cancel]);

  const next = useCallback((isSkip = false) => {
    if (!isSkip && prefs.competitiveRounds > 0 && round >= prefs.competitiveRounds) {
      finished.current();
      return;
    }

    // Carry this scenario's result into the run total before the session is
    // replaced (position ids are globally unique, so no dedupe needed).
    const done = replay.positions.filter((p) => p.status === "closed");
    if (done.length) setClosed((prev) => [...prev, ...done]);

    // A random cursor between two years and two weeks ago.
    const endMs = Date.now() - 14 * 24 * 3600 * 1000;
    const startMs = Date.now() - 2 * 365 * 24 * 3600 * 1000;
    const cursor = Math.floor(startMs + Math.random() * (endMs - startMs));
    const cfg: ReplayConfig = {
      symbol: prefs.symbol,
      timeframe: prefs.timeframe,
      range_start_msc: cursor - timeframeMs(prefs.timeframe) * prefs.historyBars,
      range_end_msc: Date.now(),
      cursor_start_msc: cursor,
      speed: prefs.speed,
    };

    if (!isSkip) setRound((r) => r + 1);

    if (isSkip) {
      setEvalPause({ pnl: 0, isSkip: true });
      later(1000, () => {
        setEvalPause(null);
        prevPosCount.current = 0;
        replay.start(cfg);
      });
    } else {
      setEvalPause(null);
      prevPosCount.current = 0;
      replay.start(cfg);
    }
  }, [prefs, round, replay, later]);

  useEffect(() => {
    if (!active || !prefs.competitiveMode) return;
    const count = replay.positions.filter((p) => p.status !== "closed").length;
    if (prevPosCount.current > 0 && count === 0 && !evalPause) {
      // Every position closed: show the scenario's result, then move on.
      setEvalPause({ pnl: replay.sessionSummary?.total_r || 0, isSkip: false });
      later(3000, () => next());
    }
    prevPosCount.current = count;
  }, [replay.positions, active, prefs.competitiveMode, evalPause, next, replay.sessionSummary, later]);

  // Stats span the whole run: finished scenarios plus the current one.
  const positions = useMemo(
    () => [...closed, ...replay.positions],
    [closed, replay.positions],
  );
  const counts = useMemo(() => outcomeCounts(positions), [positions]);
  const summary = useMemo(() => summarize(positions), [positions]);

  return { round, evalPause, next, reset, counts, summary };
}
