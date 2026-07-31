import { renderHook, act } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import { useLiveCommand } from "./useLiveCommand";
import * as api from "../lib/api";

beforeEach(() => { vi.restoreAllMocks(); });

it("request() fetches a preview and stores it without sending the real command", async () => {
  const postSpy = vi.spyOn(api, "postJson").mockResolvedValue({
    ok: true, data: { intent: "Set SL to 1900", position_id: 5, kind: "modify_sltp",
      symbol: "XAUUSDc", fields: { sl: 1900, tp: null, volume: null } },
  });
  const { result } = renderHook(() => useLiveCommand());

  await act(async () => { await result.current.request(5, "sltp", { sl: 1900 }); });

  expect(postSpy).toHaveBeenCalledWith("/api/live/5/sltp/preview", { sl: 1900 });
  expect(result.current.preview?.intent).toBe("Set SL to 1900");
});

it("confirm() enqueues the pending command and clears the preview", async () => {
  vi.spyOn(api, "postJson").mockResolvedValueOnce({
    ok: true, data: { intent: "Set SL to 1900", position_id: 5, kind: "modify_sltp",
      symbol: "XAUUSDc", fields: { sl: 1900, tp: null, volume: null } },
  });
  const { result } = renderHook(() => useLiveCommand());
  await act(async () => { await result.current.request(5, "sltp", { sl: 1900 }); });

  const enqueueSpy = vi.spyOn(api, "postJson").mockResolvedValue({
    ok: true, data: { ok: true, command_id: 42 },
  });
  await act(async () => { await result.current.confirm(); });

  expect(enqueueSpy).toHaveBeenCalledWith("/api/live/5/sltp", { sl: 1900 });
  expect(result.current.preview).toBeNull();
});

it("cancel() clears preview and pending state without enqueueing", async () => {
  vi.spyOn(api, "postJson").mockResolvedValue({
    ok: true, data: { intent: "Set SL to 1900", position_id: 5, kind: "modify_sltp",
      symbol: "XAUUSDc", fields: { sl: 1900, tp: null, volume: null } },
  });
  const { result } = renderHook(() => useLiveCommand());
  await act(async () => { await result.current.request(5, "sltp", { sl: 1900 }); });

  act(() => { result.current.cancel(); });

  expect(result.current.preview).toBeNull();
});
