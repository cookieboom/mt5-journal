import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { it, expect, vi, beforeEach } from "vitest";
import TradeView from "./TradeView";

// jsdom has no matchMedia/canvas text metrics — lightweight-charts (used by
// CandleChart) needs both to size its price axis. Stub minimally so the real
// chart can mount without throwing; we don't assert anything about its pixels.
beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    measureText: () => ({ width: 10 }),
    fillRect: () => {}, clearRect: () => {}, getImageData: () => ({ data: [] }),
    putImageData: () => {}, createImageData: () => [], setTransform: () => {},
    drawImage: () => {}, save: () => {}, fillText: () => {}, restore: () => {},
    beginPath: () => {}, moveTo: () => {}, lineTo: () => {}, closePath: () => {},
    stroke: () => {}, translate: () => {}, scale: () => {}, rotate: () => {},
    arc: () => {}, fill: () => {}, transform: () => {}, rect: () => {}, clip: () => {},
  } as unknown as CanvasRenderingContext2D);
});

// Minimal fetch stub: trade detail + candles + prefs + trades list.
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.startsWith("/api/trades/2?") || url === "/api/trades/2")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: { offset_s: 0, currency: "USC" },
        trade: { position_id: 2, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
          status: "closed", open_time_msc: 10_000, close_time_msc: 20_000, duration_s: 10,
          volume: 0.1, open_price: 4000, close_price: 4010, sl_initial: null, tp_initial: null,
          net_profit: 100, r_multiple: 1, mae_r: null, mfe_r: null, magic: null },
        annotation: null, tags: [], session: "London", is_ea: false, chartable: true,
      }) });
    if (url.startsWith("/api/candles"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        symbol: "XAUUSDc", timeframe: "M1",
        candles: [{ time_msc: 10_000, o: 4000, h: 4012, l: 3999, c: 4010, v: 5 }],
        missing: [], pending: false }) });
    if (url.startsWith("/api/trades"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: {}, trades: [{ position_id: 2 }], tags: {}, symbols: [], max_abs_net: 1, filters: {} }) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ prefs: null }) });
  }));
});

it("shows the trade's R and net in the stats panel", async () => {
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes><Route path="/trades/:id/view" element={<TradeView />} /></Routes>
  </MemoryRouter>);
  expect(await screen.findByText(/R-multiple/i)).toBeInTheDocument();
  expect(await screen.findByText("XAUUSD")).toBeInTheDocument();
});

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="probe">{loc.pathname}</div>;
}

function stubNeighborFetch() {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.startsWith("/api/trades/2?") || url === "/api/trades/2")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: { offset_s: 0, currency: "USC" },
        trade: { position_id: 2, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
          status: "closed", open_time_msc: 10_000, close_time_msc: 20_000, duration_s: 10,
          volume: 0.1, open_price: 4000, close_price: 4010, sl_initial: null, tp_initial: null,
          net_profit: 100, r_multiple: 1, mae_r: null, mfe_r: null, magic: null },
        annotation: null, tags: [], session: "London", is_ea: false, chartable: true,
      }) });
    if (url.startsWith("/api/candles"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        symbol: "XAUUSDc", timeframe: "M1",
        candles: [{ time_msc: 10_000, o: 4000, h: 4012, l: 3999, c: 4010, v: 5 }],
        missing: [], pending: false }) });
    if (url.startsWith("/api/trades"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: {}, trades: [{ position_id: 3 }, { position_id: 2 }, { position_id: 1 }],
        tags: {}, symbols: [], max_abs_net: 1, filters: {} }) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ prefs: null }) });
  }));
}

it("ArrowLeft navigates to the older neighbor (prevId)", async () => {
  stubNeighborFetch();
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes>
      <Route path="/trades/:id/view" element={<><TradeView /><LocationProbe /></>} />
    </Routes>
  </MemoryRouter>);

  await screen.findByText("XAUUSD");
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  // list [3,2,1], id=2 -> prevId (older, later index) = 1.
  expect(await screen.findByTestId("probe")).toHaveTextContent("/trades/1/view");
});

it("ArrowRight navigates to the newer neighbor (nextId)", async () => {
  stubNeighborFetch();
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes>
      <Route path="/trades/:id/view" element={<><TradeView /><LocationProbe /></>} />
    </Routes>
  </MemoryRouter>);

  await screen.findByText("XAUUSD");
  fireEvent.keyDown(window, { key: "ArrowRight" });
  // list [3,2,1], id=2 -> nextId (newer, earlier index) = 3.
  expect(await screen.findByTestId("probe")).toHaveTextContent("/trades/3/view");
});
