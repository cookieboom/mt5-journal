import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRiskSizing } from "./useRiskSizing";

const size = {
  volume: 0.1, risk_usc: 50, risk_pct: 0.05, distance: 5, rr: 2,
  direction: "buy" as const, error: null,
};

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => ({
    ok: true, status: 200, json: async () => handler(url, init),
  })) as unknown as typeof fetch;
}

describe("useRiskSizing", () => {
  beforeEach(() => {
    // shouldAdvanceTime: testing-library's `waitFor` polls via a real
    // setInterval it can't tell is faked (it only recognizes Jest fake
    // timers), so without this its poll loop never ticks and every waitFor()
    // below hangs to the test timeout.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetch((url) => (url.includes("risk-prefs") ? { prefs: null } : size));
  });

  it("does not call /api/size until an SL exists", async () => {
    renderHook(() => useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: null, tp: null }));
    await act(async () => { vi.advanceTimersByTime(500); });
    const calls = (globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls;
    expect(calls.some((c) => String(c[0]).includes("/api/size"))).toBe(false);
  });

  it("debounces: three rapid SL changes produce one request", async () => {
    const { rerender } = renderHook(
      (p: { sl: number }) => useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: p.sl, tp: null }),
      { initialProps: { sl: 4030 } },
    );
    rerender({ sl: 4031 });
    rerender({ sl: 4032 });
    await act(async () => { vi.advanceTimersByTime(500); });
    const calls = (globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls
      .filter((c) => String(c[0]).includes("/api/size"));
    expect(calls.length).toBe(1);
  });

  it("exposes the server result verbatim, including a refusal", async () => {
    mockFetch((url) =>
      url.includes("risk-prefs")
        ? { prefs: null }
        : { ...size, volume: null, error: "Risiko 60.00 melebihi batas keras 5%" });
    const { result } = renderHook(() =>
      useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: 4030, tp: null }));
    await act(async () => { vi.advanceTimersByTime(500); });
    await waitFor(() => expect(result.current.result?.error).toContain("5%"));
    expect(result.current.result?.volume).toBeNull();
  });

  it("loads saved prefs and persists a change", async () => {
    const put = vi.fn();
    mockFetch((url, init) => {
      if (url.includes("risk-prefs")) {
        if (init?.method === "PUT") { put(JSON.parse(String(init.body))); return { ok: true }; }
        return { prefs: { mode: "usc", value: 2500 } };
      }
      return size;
    });
    const { result } = renderHook(() =>
      useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: 4030, tp: null }));
    await waitFor(() => expect(result.current.prefs.mode).toBe("usc"));
    expect(result.current.prefs.value).toBe(2500);

    act(() => { result.current.setPrefs({ mode: "pct", value: 1 }); });
    await act(async () => { vi.advanceTimersByTime(500); });
    await waitFor(() => expect(put).toHaveBeenCalledWith({ mode: "pct", value: 1 }));
  });
});
