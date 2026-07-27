// Persisted replay-config popup inputs. Mirrors lib/chartPrefs.ts: versioned (v1)
// form values, normalized/clamped on load, localStorage read/write here, DB
// persistence + reconcile driven by hooks/useReplayPrefs.ts. These are the RAW
// modal inputs — cursor/range are still derived at submit time in the modal.
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "./candles";

export interface ReplayFormPrefs {
  version: 1;
  symbol: Sym;
  timeframe: Timeframe;
  startDate: string;   // "yyyy-mm-dd" or "" (kept only if valid pattern)
  historyBars: number; // clamped [HISTORY_MIN, HISTORY_MAX]
  speed: number;       // clamped [SPEED_MIN, SPEED_MAX]
  competitiveMode: boolean;
  competitiveHideDate: boolean;
  competitiveRounds: number; // 0 = endless
}

export const HISTORY_MIN = 100, HISTORY_MAX = 1000;
export const SPEED_MIN = 1, SPEED_MAX = 10;

export const DEFAULT_REPLAY_PREFS: ReplayFormPrefs = {
  version: 1,
  symbol: "XAUUSDc",
  timeframe: "M15",
  startDate: "",
  historyBars: 300,
  speed: 4,
  competitiveMode: false,
  competitiveHideDate: true,
  competitiveRounds: 0,
};

const KEY = "mt5j.replay.config";
export const STORAGE_KEY = KEY;

function clampInt(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : fallback;
  return Math.min(hi, Math.max(lo, n));
}
function oneOf<T extends string>(v: unknown, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(v as string) ? (v as T) : fallback;
}
function isoDate(v: unknown, fallback: string): string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : fallback;
}
function asBool(v: unknown, fallback: boolean): boolean {
  return typeof v === "boolean" ? v : fallback;
}

// Coerce any stored/DB/corrupt object into a valid v1 ReplayFormPrefs.
export function normalizeReplayPrefs(raw: unknown): ReplayFormPrefs {
  if (raw === null || typeof raw !== "object") return { ...DEFAULT_REPLAY_PREFS };
  const p = raw as Record<string, unknown>;
  const D = DEFAULT_REPLAY_PREFS;
  return {
    version: 1,
    symbol: oneOf(p.symbol, SYMBOLS, D.symbol),
    timeframe: oneOf(p.timeframe, TIMEFRAMES, D.timeframe),
    startDate: isoDate(p.startDate, D.startDate),
    historyBars: clampInt(p.historyBars, HISTORY_MIN, HISTORY_MAX, D.historyBars),
    speed: clampInt(p.speed, SPEED_MIN, SPEED_MAX, D.speed),
    competitiveMode: asBool(p.competitiveMode, D.competitiveMode),
    competitiveHideDate: asBool(p.competitiveHideDate, D.competitiveHideDate),
    competitiveRounds: clampInt(p.competitiveRounds, 0, 100, D.competitiveRounds),
  };
}

export function loadReplayPrefs(store: Storage = localStorage): ReplayFormPrefs {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return { ...DEFAULT_REPLAY_PREFS };
    return normalizeReplayPrefs(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_REPLAY_PREFS };
  }
}

export function saveReplayPrefs(s: ReplayFormPrefs, store: Storage = localStorage): void {
  try {
    store.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota / private mode — safe to ignore */
  }
}

// DB is authoritative. Present -> DB wins (normalized). Absent -> keep local; if
// the browser actually had a stored row, seed the DB from it (shouldImport).
export function reconcileReplayPrefs(
  local: ReplayFormPrefs, dbParsed: unknown, localExists: boolean,
): { settings: ReplayFormPrefs; shouldImport: boolean } {
  if (dbParsed !== null && dbParsed !== undefined) {
    return { settings: normalizeReplayPrefs(dbParsed), shouldImport: false };
  }
  return { settings: local, shouldImport: localExists };
}
