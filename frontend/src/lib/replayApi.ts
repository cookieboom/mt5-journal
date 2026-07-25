// Typed fetch wrappers for /api/training/*. Impure; the pure display helpers are
// in lib/replay.ts. The backend is authoritative for all fills/scores.
import { postJson } from "./api";
import type { Sym, Timeframe } from "./candles";
import type { StepEvent, TrainingPosition, TrainingSession, TrainingSummary } from "./replay";

export interface SessionView { session: TrainingSession; positions: TrainingPosition[] }
export interface CreateResult { session: TrainingSession; pending: boolean }
export interface StepResult { cursor_msc: number; events: StepEvent[]; positions: TrainingPosition[] }

export function createSession(body: {
  symbol: Sym; timeframe: Timeframe;
  range_start_msc: number; range_end_msc: number; cursor_start_msc?: number;
}) {
  return postJson<CreateResult>("/api/training/sessions", body);
}

export async function getSession(id: number): Promise<SessionView | null> {
  const r = await fetch(`/api/training/sessions/${id}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error((await r.json()).error ?? `HTTP ${r.status}`);
  return (await r.json()) as SessionView;
}

export async function listSessions(status?: "active" | "ended"): Promise<TrainingSession[]> {
  const q = status ? `?status=${status}` : "";
  const r = await fetch(`/api/training/sessions${q}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as TrainingSession[];
}

export async function deleteSession(id: number): Promise<boolean> {
  const r = await fetch(`/api/training/sessions/${id}`, { method: "DELETE" });
  return r.ok;
}

export function step(id: number, n = 1) {
  return postJson<StepResult>(`/api/training/sessions/${id}/step`, { n });
}

export function openPosition(id: number, body: {
  direction: "buy" | "sell"; volume: number; sl: number; tp: number;
}) {
  return postJson<TrainingPosition>(`/api/training/sessions/${id}/positions`, body);
}

export function closePosition(id: number, pid: number) {
  return postJson<TrainingPosition>(`/api/training/sessions/${id}/positions/${pid}/close`, {});
}

export function endSession(id: number) {
  return postJson<SessionView>(`/api/training/sessions/${id}/end`, {});
}

export async function getSummary(): Promise<TrainingSummary> {
  const r = await fetch("/api/training/summary");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as TrainingSummary;
}
