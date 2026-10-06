import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCompetitiveReplay } from "./useCompetitiveReplay";
import { DEFAULT_REPLAY_PREFS } from "../lib/replayPrefs";
import type { TrainingPosition } from "../lib/replay";

const PREFS = { ...DEFAULT_REPLAY_PREFS, competitiveMode: true, competitiveRounds: 0 };

function pos(id: number, status: string): TrainingPosition {
  return { id, status, direction: "buy", entry_price: 1, sl: 0, tp: 0 } as unknown as TrainingPosition;
}

function fakeReplay(positions: TrainingPosition[]) {
  return {
    positions,
    sessionSummary: { total_r: 1.5 },
    start: vi.fn(),
  } as unknown as Parameters<typeof useCompetitiveReplay>[0];
}

describe("useCompetitiveReplay", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("pauses on the scenario result, then starts the next scenario", () => {
    const open = fakeReplay([pos(1, "open")]);
    const { result, rerender } = renderHook(
      ({ r }) => useCompetitiveReplay(r, PREFS, true, () => {}), { initialProps: { r: open } });
    const closed = fakeReplay([pos(1, "closed")]);
    rerender({ r: closed });

    expect(result.current.evalPause).toEqual({ pnl: 1.5, isSkip: false });
    act(() => { vi.advanceTimersByTime(3000); });
    expect(closed.start).toHaveBeenCalledTimes(1);
    expect(result.current.round).toBe(2);
  });

  it("leaving during the result pause cancels the next scenario", () => {
    const open = fakeReplay([pos(1, "open")]);
    const { result, rerender } = renderHook(
      ({ r }) => useCompetitiveReplay(r, PREFS, true, () => {}), { initialProps: { r: open } });
    const closed = fakeReplay([pos(1, "closed")]);
    rerender({ r: closed });
    expect(result.current.evalPause).not.toBeNull();

    act(() => { result.current.reset(); });       // the human exits replay
    act(() => { vi.advanceTimersByTime(5000); });

    expect(closed.start).not.toHaveBeenCalled();
  });

  it("unmounting during a skip pause cancels the next scenario", () => {
    const r = fakeReplay([pos(1, "open")]);
    const { result, unmount } = renderHook(() => useCompetitiveReplay(r, PREFS, true, () => {}));
    act(() => { result.current.next(true); });
    unmount();
    act(() => { vi.advanceTimersByTime(5000); });
    expect(r.start).not.toHaveBeenCalled();
  });
});
