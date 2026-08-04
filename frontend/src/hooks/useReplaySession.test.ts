import { renderHook, act } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import { useReplaySession } from "./useReplaySession";
import * as replayApi from "../lib/replayApi";

beforeEach(() => {
  vi.restoreAllMocks();
});

it("start seeds an empty summary so the card renders before the first step", async () => {
  const { result } = renderHook(() => useReplaySession());
  expect(result.current.sessionSummary).toBeNull();

  vi.spyOn(replayApi, "createSession").mockResolvedValue({
    ok: true,
    data: { session: { id: 1, symbol: "XAUUSDc", symbol_base: "XAUUSD", timeframe: "M5",
      range_start_msc: 0, range_end_msc: 1000, cursor_msc: 0, status: "active", created_at_msc: 0 },
      pending: false },
  });
  await act(async () => { await result.current.start({
    symbol: "XAUUSDc", timeframe: "M5", range_start_msc: 0, range_end_msc: 1000,
  } as any); });

  expect(result.current.sessionSummary).toEqual({
    n: 0, win_rate: null, avg_r: null, total_r: 0, avg_mae_r: null, avg_mfe_r: null,
  });
});

it("modifySltp calls replayApi.modifySltp then refreshes the session", async () => {
  const { result } = renderHook(() => useReplaySession());

  // Seed an active session id the way `start`/`open` would.
  vi.spyOn(replayApi, "createSession").mockResolvedValue({
    ok: true,
    data: { session: { id: 42, symbol: "XAUUSDc", symbol_base: "XAUUSD", timeframe: "M5",
      range_start_msc: 0, range_end_msc: 1000, cursor_msc: 0, status: "active", created_at_msc: 0 },
      pending: false },
  });
  await act(async () => { await result.current.start({
    symbol: "XAUUSDc", timeframe: "M5", range_start_msc: 0, range_end_msc: 1000,
  } as any); });

  const modifySpy = vi.spyOn(replayApi, "modifySltp").mockResolvedValue({
    ok: true, data: { position: {} as any },
  });
  const getSessionSpy = vi.spyOn(replayApi, "getSession").mockResolvedValue({
    session: { id: 42 } as any, positions: [], summary: {} as any,
  });

  await act(async () => { await result.current.modifySltp(7, { sl: 1900 }); });

  expect(modifySpy).toHaveBeenCalledWith(7, { sl: 1900 });
  expect(getSessionSpy).toHaveBeenCalled();
});
