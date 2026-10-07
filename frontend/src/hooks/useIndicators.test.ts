import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useIndicators } from "./useIndicators";
import type { Candle } from "../lib/types";
import type { IndicatorInstance } from "../lib/indicators";

const bar = (t: number, c = 100): Candle => ({ time_msc: t, o: c, h: c, l: c, c, v: 1 });
const items: IndicatorInstance[] = [{ id: "a", script: "lib:ema", inputs: {}, visible: true }];

let calls: { from_ms: number; to_ms: number; session_id: number | null; include_forming: boolean }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const b = JSON.parse(String(init.body));
    calls.push(b);
    // Pretend the server knows bars every 1000 ms up to to_ms — including
    // ones past what the client drew, to prove the client clips.
    const times: number[] = [];
    for (let t = b.from_ms; t <= b.to_ms + 2000; t += 1000) times.push(t);
    return new Response(JSON.stringify({
      times,
      plots: [{ title: "EMA", color: "cyan", pane: "price", style: "line", width: 2,
                values: times.map((t) => t / 1000) }],
      hlines: [], signals: times.map((t) => ({ time_msc: t, side: "long" })),
      lookback: 1, warm_from_msc: b.from_ms, forming_msc: null, pending: false,
    }), { status: 200 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

it("fetches the window once, then only the tail as the edge moves", async () => {
  const { result, rerender } = renderHook((p: { bars: Candle[] }) => useIndicators({
    symbol: "XAUUSDc", tf: "M1", items, bars: p.bars, sessionId: null, live: true, rev: 0,
  }), { initialProps: { bars: [bar(1000), bar(2000), bar(3000)] } });
  await waitFor(() => expect(result.current.renders.length).toBe(1));
  expect(calls[0]).toMatchObject({ from_ms: 1000, to_ms: 3000, include_forming: true });

  rerender({ bars: [bar(1000), bar(2000), bar(3000), bar(4000)] });
  await waitFor(() => expect(result.current.renders[0].result.times.slice(-1)[0]).toBe(4000));
  const tail = calls[calls.length - 1];
  expect(tail.from_ms).toBe(2000);                 // two bars back, not the whole window
  expect(result.current.renders[0].result.times).toEqual([1000, 2000, 3000, 4000]);
});

it("in replay passes the session and never renders past the last drawn bar", async () => {
  const { result } = renderHook(() => useIndicators({
    symbol: "XAUUSDc", tf: "M1", items, bars: [bar(1000), bar(2000)], sessionId: 7,
    live: false, rev: 0,
  }));
  await waitFor(() => expect(result.current.renders.length).toBe(1));
  expect(calls[0]).toMatchObject({ session_id: 7, include_forming: false });
  const r = result.current.renders[0].result;
  expect(r.times[r.times.length - 1]).toBe(2000);
  expect(r.signals.every((s) => s.time_msc <= 2000)).toBe(true);
});

it("hidden instances are not fetched", async () => {
  renderHook(() => useIndicators({
    symbol: "XAUUSDc", tf: "M1", items: [{ ...items[0], visible: false }],
    bars: [bar(1000)], sessionId: null, live: true, rev: 0,
  }));
  await new Promise((r) => setTimeout(r, 20));
  expect(calls).toEqual([]);
});

it("a server error is reported per instance", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: "line 1:1: boom" }), { status: 400 })));
  const { result } = renderHook(() => useIndicators({
    symbol: "XAUUSDc", tf: "M1", items, bars: [bar(1000)], sessionId: null, live: true, rev: 0,
  }));
  await waitFor(() => expect(result.current.errors.a).toBe("line 1:1: boom"));
});
