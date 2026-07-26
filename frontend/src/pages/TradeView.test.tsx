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

// Trade opens right at the last of 3 candles, so startMs (10 M1-bars before
// entry) lands strictly between the 1st and 2nd candle — clicking "Putar
// ulang" reveals exactly 1 of the 3 bars, deterministically, no fake timers.
function stubPlaybackFetch() {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.startsWith("/api/trades/2?") || url === "/api/trades/2")
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: { offset_s: 0, currency: "USC" },
        trade: { position_id: 2, symbol: "XAUUSDc", symbol_base: "XAUUSD", direction: "buy",
          status: "closed", open_time_msc: 10_120_000, close_time_msc: 10_130_000, duration_s: 10,
          volume: 0.1, open_price: 4000, close_price: 4010, sl_initial: null, tp_initial: null,
          net_profit: 100, r_multiple: 1, mae_r: null, mfe_r: null, magic: null },
        annotation: null, tags: [], session: "London", is_ea: false, chartable: true,
      }) });
    if (url.startsWith("/api/candles"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        symbol: "XAUUSDc", timeframe: "M1",
        candles: [
          { time_msc: 10_000, o: 4000, h: 4012, l: 3999, c: 4010, v: 5 },
          { time_msc: 10_060_000, o: 4001, h: 4013, l: 3998, c: 4011, v: 5 },
          { time_msc: 10_120_000, o: 4002, h: 4014, l: 3997, c: 4012, v: 5 },
        ],
        missing: [], pending: false }) });
    if (url.startsWith("/api/trades"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        header: {}, trades: [{ position_id: 2 }], tags: {}, symbols: [], max_abs_net: 1, filters: {} }) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ prefs: null }) });
  }));
}

it("default (no playback) shows the full window", async () => {
  stubPlaybackFetch();
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes><Route path="/trades/:id/view" element={<TradeView />} /></Routes>
  </MemoryRouter>);
  await screen.findByText("XAUUSD");
  expect(await screen.findByTestId("bar-count")).toHaveTextContent("3");
});

it("play reveals bars up to a moving cursor", async () => {
  stubPlaybackFetch();
  render(<MemoryRouter initialEntries={["/trades/2/view"]}>
    <Routes><Route path="/trades/:id/view" element={<TradeView />} /></Routes>
  </MemoryRouter>);
  await screen.findByText("XAUUSD");
  expect(await screen.findByTestId("bar-count")).toHaveTextContent("3"); // full window default

  fireEvent.click(screen.getByRole("button", { name: "Putar ulang" }));
  // cursor jumps to startMs (open_time_msc - 10*tfMs) — only bars <= cursor show.
  expect(await screen.findByTestId("bar-count")).toHaveTextContent("1");

  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(await screen.findByTestId("bar-count")).toHaveTextContent("3"); // back to full window
});
