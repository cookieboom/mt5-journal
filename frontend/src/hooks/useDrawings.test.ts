import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useDrawings } from "./useDrawings";
import type { Drawing } from "../lib/drawings";

const hline: Drawing = { id: "d1", kind: "hline", price: 105 };

function mockFetch(getBody: unknown) {
  return vi.fn((_url: string, init?: RequestInit) => {
    if (!init || init.method !== "PUT") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(getBody) } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) } as Response);
  });
}

beforeEach(() => {
  // shouldAdvanceTime: testing-library's `waitFor` polls via a real
  // setInterval it can't tell is faked (it only recognizes Jest fake
  // timers), so without this every waitFor() below hangs to the test
  // timeout. Same fix as useRiskSizing.test.ts.
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("useDrawings", () => {
  it("loads and parses the stored blob on mount", async () => {
    const f = mockFetch({ drawings: { v: 1, items: [hline] } });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([hline]));
    expect(f.mock.calls[0][0]).toBe("/api/drawings?symbol=XAUUSDc");
  });

  it("passes session_id in the query when replaying", async () => {
    const f = mockFetch({ drawings: null });
    vi.stubGlobal("fetch", f);
    renderHook(() => useDrawings("XAUUSDc", 42, true));
    await waitFor(() => expect(f.mock.calls[0][0]).toBe("/api/drawings?symbol=XAUUSDc&session_id=42"));
  });

  it("does not fetch at all when disabled", () => {
    const f = mockFetch({ drawings: null });
    vi.stubGlobal("fetch", f);
    renderHook(() => useDrawings("XAUUSDc", null, false));
    expect(f).not.toHaveBeenCalled();
  });

  it("drops a corrupt blob instead of throwing", async () => {
    const f = mockFetch({ drawings: { v: 9, items: [hline] } });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([]));
  });

  it("add/update/remove/clear mutate items and schedule one debounced PUT", async () => {
    const f = mockFetch({ drawings: { v: 1, items: [] } });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([]));

    act(() => { result.current.add(hline); });
    expect(result.current.items).toEqual([hline]);

    act(() => { result.current.update({ ...hline, price: 111 }); });
    expect(result.current.items).toEqual([{ ...hline, price: 111 }]);

    // Both mutations coalesce into ONE PUT after the debounce window.
    const putsBefore = f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT").length;
    expect(putsBefore).toBe(0);
    await act(async () => { vi.advanceTimersByTime(500); });
    const puts = f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(JSON.parse(puts[0][1]!.body as string)).toEqual({ v: 1, items: [{ ...hline, price: 111 }] });

    act(() => { result.current.remove("d1"); });
    expect(result.current.items).toEqual([]);
    act(() => { result.current.add(hline); result.current.clear(); });
    expect(result.current.items).toEqual([]);
  });

  it("keeps working when the GET fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    const { result } = renderHook(() => useDrawings("XAUUSDc", null, true));
    await waitFor(() => expect(result.current.items).toEqual([]));
    act(() => { result.current.add(hline); });
    expect(result.current.items).toEqual([hline]);
  });
});
