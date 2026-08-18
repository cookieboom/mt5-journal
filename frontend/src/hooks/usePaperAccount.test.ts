import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { usePaperAccount } from "./usePaperAccount";

const view = {
  account: { id: 1, name: "Scalping XAU", balance: 1_000_000, leverage: 500 },
  header: { currency: "USC", balance: 1_000_000, equity: 1_000_000, margin: 0,
            free_margin: 1_000_000, margin_level: null, floating: 0 },
  open: [], pending: [], closed: [], summary: { n: 0 },
  max_drawdown: null, equity_curve: [],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => view })));
});
afterEach(() => vi.unstubAllGlobals());

describe("usePaperAccount", () => {
  it("fetches nothing at all while no account is selected", async () => {
    renderHook(() => usePaperAccount(null));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("loads the selected account", async () => {
    const { result } = renderHook(() => usePaperAccount(1));
    await waitFor(() => expect(result.current.view?.account.name).toBe("Scalping XAU"));
    expect(result.current.view?.header.currency).toBe("USC");
  });
});
