import { useCallback, useEffect, useRef, useState } from "react";
import type { Sym, Timeframe } from "../lib/candles";
import { msPerStep, type StepEvent, type TrainingPosition, type TrainingSession } from "../lib/replay";
import * as replayApi from "../lib/replayApi";

export interface ReplayConfig {
  symbol: Sym;
  timeframe: Timeframe;
  range_start_msc: number;
  range_end_msc: number;
  cursor_start_msc: number;
  speed: number;                  // 1..10 bars/sec
}

export type ReplayStatus = "idle" | "starting" | "ready" | "ended" | "error";

export function useReplaySession() {
  const [session, setSession] = useState<TrainingSession | null>(null);
  const [positions, setPositions] = useState<TrainingPosition[]>([]);
  const [events, setEvents] = useState<StepEvent[]>([]);
  const [status, setStatus] = useState<ReplayStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);

  const cfgRef = useRef<ReplayConfig | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busy = useRef(false);      // one in-flight step at a time
  const clear = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };

  const _create = useCallback(async (cfg: ReplayConfig) => {
    setStatus("starting"); setError(null); setEvents([]); setPositions([]);
    const r = await replayApi.createSession(cfg);
    if (!r.ok || !r.data) { setError(r.error ?? "gagal membuat sesi"); setStatus("error"); return; }
    setSession(r.data.session);
    setStatus("ready");
  }, []);

  const start = useCallback((cfg: ReplayConfig) => { cfgRef.current = cfg; return _create(cfg); }, [_create]);

  const _sid = () => session?.id ?? null;

  const step = useCallback(async (n = 1): Promise<StepEvent[]> => {
    const id = _sid();
    if (id === null || busy.current) return [];
    busy.current = true;
    try {
      const r = await replayApi.step(id, n);
      if (!r.ok || !r.data) { setError(r.error ?? "gagal step"); return []; }
      setPositions(r.data.positions);
      setEvents(r.data.events);
      setSession((s) => (s ? { ...s, cursor_msc: r.data!.cursor_msc } : s));
      return r.data.events;
    } finally {
      busy.current = false;
    }
  }, [session]);

  // Auto-step loop: stop at range end, or when an exit happened (review the fill).
  useEffect(() => {
    if (!playing || !session) return;
    if (session.cursor_msc >= session.range_end_msc) { setPlaying(false); return; }
    const delay = msPerStep(cfgRef.current?.speed ?? 4);
    clear();
    timer.current = setTimeout(async () => {
      const evs = await step(1);
      if (evs.some((e) => e.kind === "exit")) setPlaying(false);
    }, delay);
    return clear;
  }, [playing, session, step]);

  const play = useCallback(() => setPlaying(true), []);
  const pause = useCallback(() => { setPlaying(false); clear(); }, []);
  const jump = useCallback((n: number) => step(n), [step]);

  const reset = useCallback(() => {
    pause();
    if (cfgRef.current) return _create(cfgRef.current);
  }, [pause, _create]);

  const refresh = useCallback(async () => {
    const id = _sid();
    if (id === null) return;
    const v = await replayApi.getSession(id);
    if (v) { setSession(v.session); setPositions(v.positions); }
  }, [session]);

  const open = useCallback(async (order: { direction: "buy" | "sell"; volume: number; sl: number; tp: number }) => {
    const id = _sid();
    if (id === null) return;
    const r = await replayApi.openPosition(id, order);
    if (!r.ok) { setError(r.error ?? "gagal buka posisi"); return; }
    await refresh();
  }, [session, refresh]);

  const close = useCallback(async (pid: number) => {
    const id = _sid();
    if (id === null) return;
    await replayApi.closePosition(id, pid);
    await refresh();
  }, [session, refresh]);

  const end = useCallback(async () => {
    const id = _sid();
    if (id === null) return;
    pause();
    const r = await replayApi.endSession(id);
    if (r.ok && r.data) { setSession(r.data.session); setPositions(r.data.positions); }
    setStatus("ended");
  }, [session, pause]);

  const discard = useCallback(async () => {
    const id = _sid();
    pause();
    if (id !== null) await replayApi.deleteSession(id);
    setSession(null); setPositions([]); setEvents([]); setStatus("idle");
  }, [session, pause]);

  useEffect(() => clear, []);

  return {
    session, positions, events, status, error, playing,
    cursorMsc: session?.cursor_msc ?? null,
    start, step, play, pause, jump, reset, open, close, end, discard,
  };
}
