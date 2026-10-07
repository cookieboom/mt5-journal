// Fetches indicator values for the bars on screen and keeps them current.
//
// Two triggers: a FULL fetch when what is being computed changes (symbol, tf,
// session, the attached scripts or their inputs, or older bars loaded on the
// left), and a TAIL fetch when only the right edge moves (live forming bar
// ticks, a new bar, a replay step) — the tail re-asks for the last two bars
// and merges, so a live chart is not recomputing 3,000 bars every second.
//
// Replay: `sessionId` makes the server clip to the cursor, and every render is
// clipped to the last bar on screen as well (clipResult), so a racing or stale
// response can never draw a value the cursor has not revealed.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Candle } from "../lib/types";
import {
  clipResult, indicatorsApi, mergeTail,
  type IndicatorInstance, type IndicatorResult,
} from "../lib/indicators";
import type { IndicatorRender } from "./useIndicatorSeries";

const PENDING_RETRY_MS = 3000;

export function useIndicators(a: {
  symbol: string;
  tf: string;
  items: IndicatorInstance[];
  bars: Candle[];
  sessionId: number | null;
  live: boolean;          // include the forming bar (never in replay)
  rev: number;            // bumped when a script's source changes
}): {
  renders: IndicatorRender[];
  results: Record<string, IndicatorResult>;
  errors: Record<string, string>;
} {
  const [results, setResults] = useState<Record<string, IndicatorResult>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [retry, setRetry] = useState(0);
  const visible = useMemo(() => a.items.filter((i) => i.visible), [a.items]);
  const from = a.bars[0]?.time_msc ?? null;
  const last = a.bars[a.bars.length - 1];
  const to = last?.time_msc ?? null;

  const ctxKey = JSON.stringify([a.symbol, a.tf, a.sessionId]);
  const fullKey = JSON.stringify([ctxKey, from, a.rev, a.live,
    visible.map((i) => [i.id, i.script, i.inputs])]);
  const tailKey = `${to}|${last?.c}|${last?.h}|${last?.l}`;

  const gen = useRef(0);
  const latest = useRef({ ...a, visible, from, to });
  latest.current = { ...a, visible, from, to };
  const resultsRef = useRef(results);
  resultsRef.current = results;

  // Another symbol's values must never sit on this chart, even for a frame.
  useEffect(() => { setResults({}); setErrors({}); }, [ctxKey]);

  useEffect(() => {
    const g = ++gen.current;
    const cur = latest.current;
    if (cur.from === null || cur.to === null || cur.visible.length === 0) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    void Promise.all(cur.visible.map(async (inst) => [inst.id, await indicatorsApi.compute({
      script: inst.script, inputs: inst.inputs, symbol: cur.symbol, timeframe: cur.tf,
      from_ms: cur.from!, to_ms: cur.to!, session_id: cur.sessionId, include_forming: cur.live,
    })] as const)).then((list) => {
      if (g !== gen.current) return;
      const next: Record<string, IndicatorResult> = {};
      const errs: Record<string, string> = {};
      for (const [id, r] of list) {
        if (r.ok && r.data) next[id] = clipResult(r.data, cur.to!);
        else errs[id] = r.error ?? "gagal menghitung";
      }
      setResults(next);
      setErrors(errs);
      if (Object.values(next).some((r) => r.pending)) {
        timer = setTimeout(() => setRetry((x) => x + 1), PENDING_RETRY_MS);
      }
    });
    return () => { if (timer) clearTimeout(timer); };
  }, [fullKey, retry]);

  const inflight = useRef(false);
  const again = useRef(false);
  useEffect(() => {
    const run = async (): Promise<void> => {
      if (inflight.current) { again.current = true; return; }
      inflight.current = true;
      try {
        const g = gen.current;
        const cur = latest.current;
        if (cur.to === null) return;
        await Promise.all(cur.visible.map(async (inst) => {
          const prev = resultsRef.current[inst.id];
          if (!prev || prev.times.length === 0) return;
          if (cur.to! < prev.times[prev.times.length - 1]) {
            setRetry((x) => x + 1);          // the edge moved back: start over
            return;
          }
          const tailFrom = prev.times[Math.max(0, prev.times.length - 2)];
          const r = await indicatorsApi.compute({
            script: inst.script, inputs: inst.inputs, symbol: cur.symbol, timeframe: cur.tf,
            from_ms: tailFrom, to_ms: cur.to!, session_id: cur.sessionId, include_forming: cur.live,
          });
          if (g !== gen.current || !r.ok || !r.data) return;
          const tail = clipResult(r.data, cur.to!);
          setResults((p) => (p[inst.id] ? { ...p, [inst.id]: mergeTail(p[inst.id], tail) } : p));
        }));
      } finally {
        inflight.current = false;
        if (again.current) { again.current = false; void run(); }
      }
    };
    void run();
  }, [tailKey]);

  const renders = useMemo(() => (to === null ? [] : visible
    .filter((i) => results[i.id])
    .map((i) => ({ id: i.id, result: clipResult(results[i.id], to) }))), [visible, results, to]);

  return { renders, results, errors };
}
