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
  const T0 = 1_700_000_100_000;   // on an M5 bucket boundary: forward fetches start there
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

// Reproduces the reported bug: opening /chart mid-bar loses exactly ONE bar —
// the one that was still forming at open — and only that once, after which the
// chart behaves. `toRef` starts at Date.now(), which is mid-bucket, and every
// forward fetch starts exactly there; the backend selects
// `time_msc BETWEEN from AND to`, so that bucket's bar (time_msc = bucket
// start, BEFORE from) can never come back. The next rollover confirms coverage
// from the following bar onward and the hole is permanent — nothing ever
// re-requests behind toRef. Forward fetches must therefore start at the BUCKET
// START of the cursor, not at the raw instant.
it("loadUpTo re-requests the bucket that was still forming when the chart opened", async () => {
  vi.useFakeTimers();
  const M5 = 5 * 60_000;
  const B = 1_700_000_100_000;   // a bucket boundary (epoch-aligned for M5)
  const OPEN = B + 150_000;      // chart opened halfway through the B bar
  vi.setSystemTime(OPEN);

  const froms: number[] = [];
  const fetchMock = vi.fn((url: string) => {
    const q = new URL(url, "http://localhost").searchParams;
    const from = Number(q.get("from"));
    if (from === OPEN - 3 * M5) {
      // Initial window: everything up to the last CLOSED bar; B is still forming.
      return jsonOk({
        symbol: "XAUUSDc", timeframe: "M5",
        candles: [{ time_msc: B - M5, o: 1, h: 1, l: 1, c: 1, v: 1 }],
        missing: [], pending: false,
      });
    }
    froms.push(from);
    // B has closed and journal live promoted it. The backend filters
    // time_msc >= from, exactly as `candles_store.load` does.
    return jsonOk({
      symbol: "XAUUSDc", timeframe: "M5",
      candles: [{ time_msc: B, o: 2, h: 2, l: 2, c: 2, v: 1 }].filter((c) => c.time_msc >= from),
      missing: [[B + M5, B + M5 + 30_000]], pending: true,   // the new forming bar
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  try {
    const { result } = renderHook(() => useChartData("XAUUSDc", "M5", 3, 50));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.lastBarMs).toBe(B - M5);

    // Rollover: the forming bar advances to B + M5, so Chart.tsx tail-follows.
    vi.setSystemTime(B + M5 + 300);
    await act(async () => { await result.current.loadUpTo(B + M5); });

    expect(froms[0]).toBeLessThanOrEqual(B);   // must reach back over the B bucket
    expect(result.current.candles.map((c) => c.time_msc)).toContain(B);
  } finally {
    vi.useRealTimers();
  }
});

// Reproduces the reported bug: /chart shows fresh live bars (tail-follow via
// loadUpTo keeps working) but the "data belum lengkap" banner never clears.
// load()'s poll cycle owns status and gives up after MAX_POLLS if the
// initial window has a gap (e.g. chart opened before `journal live` caught
// up) — but loadUpTo runs independently and never touched status, so a
// stale "gaveup" outlived the gap it described.
it("loadUpTo clears a stale gaveup status once the window is actually covered", async () => {
  vi.useFakeTimers();
  const T0 = 1_700_000_000_000;
  vi.setSystemTime(T0);
  const M5 = 5 * 60_000;

  let pollCalls = 0;
  const fetchMock = vi.fn((url: string) => {
    const q = new URL(url, "http://localhost").searchParams;
    const from = Number(q.get("from"));
    // First 6 calls are load()'s initial fetch + its 5 bounded retries, all
    // against the same window: some history present, but the tail is
    // permanently missing (simulates journal live not running yet) so the
    // poll cycle exhausts MAX_POLLS and gives up.
    if (pollCalls < 6) {
      pollCalls += 1;
      return jsonOk({
        symbol: "XAUUSDc", timeframe: "M5",
        candles: [{ time_msc: T0 - 2 * M5, o: 1, h: 1, l: 1, c: 1, v: 1 }],
        missing: [[T0 - M5, T0]], pending: true,
      });
    }
    // journal live has since caught up: tail-follow (loadUpTo) finds full coverage.
    return jsonOk({
      symbol: "XAUUSDc", timeframe: "M5",
      candles: [{ time_msc: from, o: 2, h: 2, l: 2, c: 2, v: 1 }],
      missing: [], pending: false,
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  try {
    const { result } = renderHook(() => useChartData("XAUUSDc", "M5", 3, 50));
    // Drain all 5 bounded polls (POLL_MS apart) to reach "gaveup".
    await act(async () => { await vi.advanceTimersByTimeAsync(2000 * 6); });
    expect(result.current.status).toBe("gaveup");

    await act(async () => { await result.current.loadUpTo(T0 + M5); });
    expect(result.current.status).toBe("ready");
  } finally {
    vi.useRealTimers();
  }
});

// A window fetched up to Date.now() always straddles the currently-forming
// bar, so the backend's `missing` for a live tail-follow call is realistically
// NEVER empty — it always has a trailing sliver for the bar that hasn't
// closed yet. The gaveup-clearing logic must not require missing.length===0
// (that would never fire in practice); it must clear on confirmed forward
// progress instead.
it("loadUpTo clears a stale gaveup status on forward progress even with a trailing forming-bar sliver", async () => {
  vi.useFakeTimers();
  const T0 = 1_700_000_000_000;
  vi.setSystemTime(T0);
  const M5 = 5 * 60_000;

  let pollCalls = 0;
  const fetchMock = vi.fn(() => {
    if (pollCalls < 6) {
      pollCalls += 1;
      return jsonOk({
        symbol: "XAUUSDc", timeframe: "M5",
        candles: [{ time_msc: T0 - 2 * M5, o: 1, h: 1, l: 1, c: 1, v: 1 }],
        missing: [[T0 - M5, T0]], pending: true,
      });
    }
    // journal live caught up: the just-closed bar is confirmed, but the
    // still-forming bar's tail is (correctly, always) reported missing.
    return jsonOk({
      symbol: "XAUUSDc", timeframe: "M5",
      candles: [{ time_msc: T0, o: 2, h: 2, l: 2, c: 2, v: 1 }],
      missing: [[T0 + M5, T0 + M5 + 15_000]], pending: true,
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  try {
    const { result } = renderHook(() => useChartData("XAUUSDc", "M5", 3, 50));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000 * 6); });
    expect(result.current.status).toBe("gaveup");

    await act(async () => { await result.current.loadUpTo(T0 + M5); });
    expect(result.current.missing).toEqual([[T0 + M5, T0 + M5 + 15_000]]);
    expect(result.current.status).toBe("ready");
  } finally {
    vi.useRealTimers();
  }
});
