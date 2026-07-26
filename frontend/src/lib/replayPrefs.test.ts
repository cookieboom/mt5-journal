import { describe, it, expect } from "vitest";
import {
  DEFAULT_REPLAY_PREFS, loadReplayPrefs, saveReplayPrefs,
  normalizeReplayPrefs, reconcileReplayPrefs, STORAGE_KEY,
} from "./replayPrefs";

function fakeStore(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() { return m.size; },
  } as Storage;
}

describe("replayPrefs", () => {
  it("loads defaults when nothing stored", () => {
    expect(loadReplayPrefs(fakeStore())).toEqual(DEFAULT_REPLAY_PREFS);
  });

  it("round-trips saved prefs", () => {
    const s = fakeStore();
    const p = { ...DEFAULT_REPLAY_PREFS, symbol: "BTCUSDc" as const, speed: 8 };
    saveReplayPrefs(p, s);
    expect(loadReplayPrefs(s)).toEqual(p);
  });

  it("falls back to defaults on corrupt json", () => {
    const s = fakeStore();
    s.setItem(STORAGE_KEY, "{not json");
    expect(loadReplayPrefs(s)).toEqual(DEFAULT_REPLAY_PREFS);
  });

  it("clamps out-of-range historyBars and speed", () => {
    const n = normalizeReplayPrefs({ historyBars: 99999, speed: 0 });
    expect(n.historyBars).toBe(1000);
    expect(n.speed).toBe(1);
  });

  it("rejects bad symbol / timeframe / startDate", () => {
    const n = normalizeReplayPrefs({ symbol: "NOPE", timeframe: "X9", startDate: "not-a-date" });
    expect(n.symbol).toBe(DEFAULT_REPLAY_PREFS.symbol);
    expect(n.timeframe).toBe(DEFAULT_REPLAY_PREFS.timeframe);
    expect(n.startDate).toBe("");
  });

  it("keeps a valid yyyy-mm-dd startDate", () => {
    expect(normalizeReplayPrefs({ startDate: "2026-01-02" }).startDate).toBe("2026-01-02");
  });

  it("reconcile: DB present wins; absent keeps local and imports when local existed", () => {
    const local = { ...DEFAULT_REPLAY_PREFS, speed: 9 };
    const fromDb = reconcileReplayPrefs(local, { version: 1, symbol: "EURUSDc" }, false);
    expect(fromDb.settings.symbol).toBe("EURUSDc");
    expect(fromDb.shouldImport).toBe(false);

    const noDb = reconcileReplayPrefs(local, null, true);
    expect(noDb.settings).toEqual(local);
    expect(noDb.shouldImport).toBe(true);
  });
});
