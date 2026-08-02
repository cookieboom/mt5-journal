import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useChartData } from "./useChartData";

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
}

// Reproduces the reported live-chart bug: Chart.tsx calls loadUpTo(formingMs)
// on every bar rollover to bridge the historical window forward. The backend
// can legitimately report the just-closed bar's span as still
// missing/pending — `journal live` promotes a closed bar into `candles` on
// its own ~1-5s cycle (candle_queue / serve_watches), a real race against the
// frontend's poll. loadUpTo must never advance its cursor past a span the
// backend didn't actually confirm as covered: doing so drops that bar
// forever, since the next call's `from` starts exactly where the previous
// one left off — nothing else ever re-requests bars behind the cursor.
it("loadUpTo does not permanently drop a bar the backend hasn't promoted yet", async () => {
  vi.useFakeTimers();
  const T0 = 1_700_000_000_000;
  vi.setSystemTime(T0);
  const M5 = 5 * 60_000;

  const bridgeCalls: { from: number; to: number }[] = [];
  let backendCaughtUp = false;

  const fetchMock = vi.fn((url: string) => {
    const q = new URL(url, "http://localhost").searchParams;
    const from = Number(q.get("from"));
    const to = Number(q.get("to"));
    if (from === T0 - 3 * M5) {
      // Initial window: fully covered.
      return jsonOk({
        symbol: "XAUUSDc", timeframe: "M5",
        candles: [
          { time_msc: T0 - 2 * M5, o: 1, h: 1, l: 1, c: 1, v: 1 },
          { time_msc: T0 - 1 * M5, o: 1, h: 1, l: 1, c: 1, v: 1 },
        ],
        missing: [], pending: false,
      });
    }
    bridgeCalls.push({ from, to });
    if (!backendCaughtUp) {
      // journal live hasn't promoted this span into `candles` yet.
      return jsonOk({ symbol: "XAUUSDc", timeframe: "M5", candles: [], missing: [[from, to]], pending: true });
    }
    return jsonOk({
      symbol: "XAUUSDc", timeframe: "M5",
      candles: [{ time_msc: from, o: 2, h: 2, l: 2, c: 2, v: 1 }],
      missing: [], pending: false,
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  try {
    const { result } = renderHook(() => useChartData("XAUUSDc", "M5", 3, 50));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.status).toBe("ready");
    expect(result.current.lastBarMs).toBe(T0 - M5);

    // Bar closes; the bridge fetch races the backend's promotion and finds
    // the tail unpromoted (candles: [], missing: [[from,to]], pending: true).
    await act(async () => { await vi.advanceTimersByTimeAsync(M5); });
    await act(async () => { await result.current.loadUpTo(T0 + M5); });
    expect(bridgeCalls.length).toBe(1);
    expect(bridgeCalls[0].from).toBe(T0);
    expect(result.current.missing).toEqual([[T0, T0 + M5]]);

    // A LATER rollover must retry from the SAME point, not from wherever the
    // unfulfilled request's `to` landed — that would silently skip the gap.
    await act(async () => { await vi.advanceTimersByTimeAsync(M5); });
    await act(async () => { await result.current.loadUpTo(T0 + 2 * M5); });
    expect(bridgeCalls.length).toBe(2);
    expect(bridgeCalls[1].from).toBe(T0); // must retry the exact same gap, not skip past it

    // Once the backend catches up, the very next rollover must actually pull
    // the bar in — it must not have been silently lost already.
    backendCaughtUp = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(M5); });
    await act(async () => { await result.current.loadUpTo(T0 + 3 * M5); });
    expect(bridgeCalls[2].from).toBe(T0);
    expect(result.current.missing).toEqual([]);
  } finally {
    vi.useRealTimers();
  }
});
