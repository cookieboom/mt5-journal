// Pins the live SL/TP safety seam: a chart-line drag must go through
// SltpConfirmDialog (precision-edit preview) and then ConfirmModal (the only
// write) before anything reaches the bridge. A future refactor that wires
// SltpConfirmDialog's onConfirm straight to liveCmd.confirm — skipping the
// preview step — must fail this test even though every other suite (drag
// hit-testing in CandleChart.test.tsx, dialog behavior in
// SltpConfirmDialog.test.tsx, request/confirm plumbing in
// useLiveCommand.test.ts) stays green in isolation.
//
// CandleChart itself (lightweight-charts + canvas) is mocked out, same as
// TradeView.test.tsx does — we only need to capture the onSlTpChange prop
// Chart.tsx wires to it and invoke it directly, exactly as a real drag would.
// Unlike TradeView (which never passes a ref), Chart.tsx passes `ref={chartRef}`
// to CandleChart, and CandleChart really is a forwardRef component — the mock
// must be one too, or React silently calls it with undefined props instead of
// the real ones (confirmed by hand: a plain function-component mock breaks).
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { it, expect, vi, beforeEach } from "vitest";
import React from "react";
import Chart from "./Chart";

const mockCandleChart = vi.fn();
vi.mock("../components/CandleChart", () => ({
  default: React.forwardRef((props: any, _ref: any) => {
    mockCandleChart(props);
    return <div data-testid="candle-chart" />;
  }),
}));

function stubFetch() {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    const method = opts?.method ?? "GET";

    // ---- the two seam endpoints under test (checked before the generic
    // /api/live branch below, since both URLs start with "/api/live") ----
    if (url === "/api/live/5/sltp/preview" && method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          intent: "Set SL to 1900.5", position_id: 5, kind: "modify_sltp",
          symbol: "XAUUSDc", fields: { sl: 1900.5, tp: null, volume: null },
        }),
      });
    }
    if (url === "/api/live/5/sltp" && method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ok: true, command_id: 42 }),
      });
    }

    // ---- background hooks Chart.tsx mounts unconditionally ----
    if (url.startsWith("/api/candles/live")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ forming: null, beat_msc: null, live: false }),
      });
    }
    if (url.startsWith("/api/candles")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          symbol: "XAUUSDc", timeframe: "M5",
          candles: [{ time_msc: 10_000, o: 1900, h: 1905, l: 1895, c: 1900, v: 5 }],
          missing: [], pending: false,
        }),
      });
    }
    if (url.startsWith("/api/live-status")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ live: true, beat_msc: 10_000, age_ms: 100 }),
      });
    }
    if (url.startsWith("/api/live")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          header: { login: 0, currency: "USC", offset_s: 0 },
          live: {
            positions: [{
              position_id: 5, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
              volume: 0.1, open_price: 1900, price_current: 1901, sl: 1890, tp: 1920,
              profit: 100, observed_msc: Date.now(),
            }],
            count: 1, total_floating: 100, total_volume: 0.1, age_s: 1, stale: false, empty: false,
          },
        }),
      });
    }
    if (url.startsWith("/api/training/summary")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          n: 0, win_rate: null, avg_r: null, total_r: 0, avg_mae_r: null, avg_mfe_r: null,
        }),
      });
    }
    if (url.startsWith("/api/watch")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    // /api/chart/prefs, /api/replay/prefs, and anything else — harmless default.
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ prefs: null }) });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function postsTo(fetchMock: ReturnType<typeof stubFetch>, url: string): number {
  return fetchMock.mock.calls.filter(
    ([u, opts]: [string, RequestInit?]) => u === url && (opts?.method ?? "GET") === "POST",
  ).length;
}

beforeEach(() => { mockCandleChart.mockClear(); });

it("a live SL/TP drag previews before it enqueues — never skips straight to the bridge", async () => {
  const fetchMock = stubFetch();
  render(
    <MemoryRouter initialEntries={["/chart"]}>
      <Routes><Route path="/chart" element={<Chart />} /></Routes>
    </MemoryRouter>,
  );

  await screen.findByTestId("candle-chart");
  const calls = mockCandleChart.mock.calls;
  const lastCandleChartProps = calls[calls.length - 1][0];
  expect(lastCandleChartProps.onSlTpChange).toBeTypeOf("function");

  // Simulate the drag reaching CandleChart's onSlTpChange callback, exactly as
  // a real pointerup on the SL line would (see CandleChart.test.tsx for the
  // pixel-level drag simulation that produces this same call shape).
  act(() => { lastCandleChartProps.onSlTpChange(5, { sl: 1900.5 }); });

  // Step 1: the precision-edit dialog opens. Confirming it must only fetch a
  // PREVIEW — nothing has been sent to the bridge yet.
  const dialogConfirm = await screen.findByRole("button", { name: /^konfirmasi$/i });
  act(() => { dialogConfirm.click(); });

  await screen.findByText(/konfirmasi perintah/i); // ConfirmModal heading
  expect(postsTo(fetchMock, "/api/live/5/sltp/preview")).toBe(1);
  expect(postsTo(fetchMock, "/api/live/5/sltp")).toBe(0);

  // Step 2: only ConfirmModal's own confirm button is allowed to enqueue.
  const modalConfirm = await screen.findByRole("button", { name: /konfirmasi & kirim/i });
  act(() => { modalConfirm.click(); });

  await screen.findByText(/masuk antrean/i); // toast confirms the enqueue landed
  expect(postsTo(fetchMock, "/api/live/5/sltp/preview")).toBe(1);
  expect(postsTo(fetchMock, "/api/live/5/sltp")).toBe(1);
});
