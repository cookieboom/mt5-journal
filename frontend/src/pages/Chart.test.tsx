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
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import Chart from "./Chart";
import * as api from "../lib/api";
import type { SizeResult } from "../lib/types";

const mockCandleChart = vi.fn();
vi.mock("../components/CandleChart", () => ({
  default: React.forwardRef((props: any, _ref: any) => {
    mockCandleChart(props);
    return <div data-testid="candle-chart" />;
  }),
}));

// The panel's own sizing math lives entirely on the server (useRiskSizing just
// relays POST /api/size). Mocking the hook lets a test hand the panel a ready
// SizeResult on the very first render instead of re-driving a real SL drag +
// debounce through it — the drag path itself is CandleChart's concern, already
// covered by CandleChart.test.tsx and the SL/TP seam test above.
const riskSizing = vi.hoisted(() => ({ result: null as SizeResult | null }));
vi.mock("../hooks/useRiskSizing", () => ({
  useRiskSizing: () => ({
    prefs: { mode: "pct" as const, value: 1 },
    setPrefs: vi.fn(),
    result: riskSizing.result,
    loading: false,
  }),
}));

// A real session comes from POST /api/training/sessions, an async round trip —
// racy to depend on from a test that clicks "Buy" right after mount. Mocking
// the session hook lets a test hand the panel an already-active session
// synchronously; `openMock` stands in for the module's real
// replayApi.openPosition(session_id, order) call so a submit can be asserted
// without a network round trip. Session lifecycle itself (create/step/close)
// is ReplayControls/useReplaySession's own concern, untouched here.
const replaySession = vi.hoisted(() => ({
  session: null as any,
  positions: [] as any[],
  sessionSummary: null as any,
  cursorMsc: null as number | null,
  anchorMsc: null as number | null,
  open: vi.fn(),
}));
vi.mock("../hooks/useReplaySession", () => ({
  useReplaySession: () => ({
    session: replaySession.session,
    positions: replaySession.positions,
    events: [],
    sessionSummary: replaySession.sessionSummary,
    status: replaySession.session ? "ready" : "idle",
    error: null,
    playing: false,
    cursorMsc: replaySession.cursorMsc,
    anchorMsc: replaySession.anchorMsc,
    start: vi.fn(),
    step: vi.fn().mockResolvedValue([]),
    play: vi.fn(),
    pause: vi.fn(),
    jump: vi.fn(),
    reset: vi.fn(),
    open: replaySession.open,
    close: vi.fn(),
    modifySltp: vi.fn(),
    end: vi.fn(),
    discard: vi.fn().mockResolvedValue(undefined),
  }),
}));

// `extra` lets a test answer additional routes (training sessions, live open)
// without duplicating every background-hook stub below it — checked first, so
// it may also override a route already handled further down.
function stubFetch(extra?: (url: string, method: string) => { ok: boolean; json: () => Promise<any> } | undefined) {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    const method = opts?.method ?? "GET";
    if (extra) {
      const r = extra(url, method);
      if (r) return Promise.resolve(r);
    }

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

// Reproduces the reported bug: the live bar (useLiveForming, polled every 5s
// forever) keeps advancing, but data.candles (useChartData) only refetches
// while its initial window has gaps — once that settles to "ready" nothing
// ever asks the backend for bars that close afterward. mergeForming can only
// ever bridge a SINGLE bar against a stale data.candles, so after the forming
// bar rolls over more than once since page load, the bar(s) in between vanish
// and a permanent gap opens between the frozen historical tail and the live
// bar. The fix must make live mode pull newly closed bars forward, the same
// way replay already does via data.loadUpTo(cursor).
it("live mode keeps the historical window advancing so it never gaps behind the live bar", async () => {
  vi.useFakeTimers();
  const T0 = 1_700_000_100_000;   // on an M5 bucket boundary: forward fetches start there
  vi.setSystemTime(T0);
  const M5 = 5 * 60_000;

  let formingMs = T0; // mutated mid-test to simulate the live bar rolling over
  const candlesCalls: { from: string | null; to: string | null }[] = [];

  const fetchMock = vi.fn((url: string) => {
    if (url.startsWith("/api/candles/live")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          forming: { time_msc: formingMs, o: 1900, h: 1901, l: 1899, c: 1900.5, v: 1 },
          beat_msc: formingMs, live: true,
        }),
      });
    }
    if (url.startsWith("/api/candles")) {
      const q = new URL(url, "http://localhost").searchParams;
      candlesCalls.push({ from: q.get("from"), to: q.get("to") });
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          symbol: "XAUUSDc", timeframe: "M5",
          candles: [{ time_msc: T0 - M5, o: 1900, h: 1905, l: 1895, c: 1900, v: 5 }],
          missing: [], pending: false,
        }),
      });
    }
    if (url.startsWith("/api/live-status")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ live: true, beat_msc: T0, age_ms: 100 }) });
    }
    if (url.startsWith("/api/live")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          header: { login: 0, currency: "USC", offset_s: 0 },
          live: { positions: [], count: 0, total_floating: 0, total_volume: 0, age_s: 1, stale: false, empty: true },
        }),
      });
    }
    if (url.startsWith("/api/watch")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ prefs: null }) });
  });
  vi.stubGlobal("fetch", fetchMock);

  try {
    render(
      <MemoryRouter initialEntries={["/chart"]}>
        <Routes><Route path="/chart" element={<Chart />} /></Routes>
      </MemoryRouter>,
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(candlesCalls.length).toBe(1); // initial historical fetch only

    // First bar close after page load: the live feed moves one bar forward,
    // and real time actually elapses by the same amount (realistic — a
    // synthetic jump with the clock left behind would hide the overshoot bug
    // below, since it only manifests once wall-clock time has caught up).
    formingMs = T0 + M5;
    await act(async () => { await vi.advanceTimersByTimeAsync(M5); });
    expect(candlesCalls.length).toBe(2);
    const first = candlesCalls[1];
    expect(Number(first.from)).toBe(T0); // toRef.current from the initial window
    // The bridging fetch must not overshoot past real "now" — a forward-chunk
    // lookahead (fine for replay, where the range already exists) would jump
    // toRef.current e.g. 200 bars into a future that hasn't happened yet for
    // live data, permanently blocking every later bridge until wall-clock
    // time caught up to that fictitious point.
    expect(Number(first.to)).toBe(T0 + M5);

    // Second bar close, sometime later. This is exactly where the overshoot
    // bug froze the historical window: toRef.current stuck far in the
    // future, so this bridge would silently never fire.
    formingMs = T0 + 2 * M5;
    await act(async () => { await vi.advanceTimersByTimeAsync(M5); });
    expect(candlesCalls.length).toBe(3);
    const second = candlesCalls[2];
    expect(Number(second.from)).toBe(Number(first.to));
    expect(Number(second.to)).toBe(T0 + 2 * M5);
  } finally {
    vi.useRealTimers();
  }
});

describe("risk sizing panel", () => {
  const FAKE_SESSION = {
    id: 1, symbol: "XAUUSDc", symbol_base: "XAUUSD", timeframe: "M5" as const,
    range_start_msc: 0, range_end_msc: Date.now() + 365 * 24 * 3600 * 1000,
    cursor_msc: Date.now(), status: "active" as const, created_at_msc: Date.now(),
  };

  // A current M5 bucket, so the shown bar is inside the freshness window the
  // open button now gates on. stubFetch's own defaults deliberately serve a
  // 1970 bar and `live: false` — i.e. a dead feed — which is what the
  // stale-feed test below leans on.
  const M5_MS = 5 * 60_000;
  const FRESH_BUCKET = Math.floor(Date.now() / M5_MS) * M5_MS;

  // Extends the file's stubFetch with the live-open preview/enqueue seam, and
  // (when `fresh`) a live feed current enough to open against. Everything else
  // (live positions, ...) falls through to stubFetch's own defaults; replay
  // session creation never touches the network at all — see the
  // useReplaySession mock above.
  function extraRoutes(url: string, method: string, fresh: boolean) {
    if (fresh && url.startsWith("/api/candles/live")) {
      return {
        ok: true,
        json: () => Promise.resolve({
          forming: { time_msc: FRESH_BUCKET, o: 1900, h: 1901, l: 1899, c: 1900.5, v: 1 },
          beat_msc: FRESH_BUCKET, live: true,
        }),
      };
    }
    if (fresh && url.startsWith("/api/candles")) {
      return {
        ok: true,
        json: () => Promise.resolve({
          symbol: "XAUUSDc", timeframe: "M5",
          candles: [{ time_msc: FRESH_BUCKET, o: 1900, h: 1905, l: 1895, c: 1900, v: 5 }],
          missing: [], pending: false,
        }),
      };
    }
    if (url === "/api/live/open/preview" && method === "POST") {
      return {
        ok: true,
        json: () => Promise.resolve({
          intent: "Buka buy 0.13 lot XAUUSDc", position_id: null, kind: "open",
          symbol: "XAUUSDc", fields: { sl: null, tp: null, volume: 0.13 },
        }),
      };
    }
    if (url === "/api/live/open" && method === "POST") {
      return { ok: true, json: () => Promise.resolve({ ok: true, command_id: 7 }) };
    }
    return undefined;
  }

  beforeEach(() => {
    riskSizing.result = null;
    replaySession.session = null;
    replaySession.cursorMsc = null;
    replaySession.anchorMsc = null;
    replaySession.open = vi.fn();
  });

  function renderChartPage(
    opts: { replayOpen: boolean; sizeResult?: SizeResult; staleFeed?: boolean },
  ) {
    riskSizing.result = opts.sizeResult ?? null;
    const openPosition = vi.fn();
    replaySession.open = vi.fn(async (order: unknown) => {
      openPosition(FAKE_SESSION.id, order);
    });
    if (opts.replayOpen) {
      replaySession.session = FAKE_SESSION;
      replaySession.cursorMsc = FAKE_SESSION.cursor_msc;
      replaySession.anchorMsc = FAKE_SESSION.cursor_msc;
    }

    const fetchMock = stubFetch((url, method) =>
      extraRoutes(url, method, !opts.staleFeed));
    const postJsonSpy = vi.spyOn(api, "postJson"); // call-through: still hits the stubbed fetch above

    render(
      <MemoryRouter initialEntries={["/chart"]}>
        <Routes><Route path="/chart" element={<Chart />} /></Routes>
      </MemoryRouter>,
    );

    // Entering replay is Chart.tsx's own local state — the only door in is the
    // real toolbar → config-modal → "Mulai" flow. Both clicks are synchronous
    // state transitions (no network involved, since useReplaySession is
    // mocked above), so this needs no `act`/`await` beyond fireEvent's own.
    if (opts.replayOpen) {
      fireEvent.click(screen.getByRole("button", { name: /replay/i }));
      fireEvent.click(screen.getByRole("button", { name: /^mulai$/i }));
    }

    return { fetchMock, postJson: postJsonSpy, openPosition };
  }

  it("is mounted in replay mode", async () => {
    renderChartPage({ replayOpen: true });
    expect(await screen.findByText(/Ukuran otomatis/i)).toBeTruthy();
  });

  it("is mounted in live (non-replay) mode", async () => {
    renderChartPage({ replayOpen: false });
    expect(await screen.findByText(/Ukuran otomatis/i)).toBeTruthy();
  });

  it("a replay submit sends the server-derived volume to the replay open API", async () => {
    const { openPosition } = renderChartPage({ replayOpen: true, sizeResult: {
      volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: null,
      direction: "buy", error: null,
    } });
    fireEvent.click(await screen.findByRole("button", { name: /buy/i }));
    await waitFor(() => expect(openPosition).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ direction: "buy", volume: 0.13 }),
    ));
  });

  it("a live submit goes through the preview/confirm flow, not straight to the order", async () => {
    const { postJson } = renderChartPage({ replayOpen: false, sizeResult: {
      volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: null,
      direction: "buy", error: null,
    } });
    fireEvent.click(await screen.findByRole("button", { name: /buy/i }));
    await waitFor(() => expect(postJson).toHaveBeenCalledWith(
      "/api/live/open/preview", expect.anything()));
    expect(postJson).not.toHaveBeenCalledWith("/api/live/open", expect.anything());
  });

  // The volume is frozen at enqueue, so a reference price the market has left
  // ships a lot that no longer matches the budget — and the executor's
  // re-validation only catches a stop on the wrong SIDE, never a wrong size.
  // docs/HANDOFF.md, OPEN QUESTION (2026-08-04 review).
  it("refuses to open at all when the feed is stale, however valid the size", async () => {
    const { postJson } = renderChartPage({ replayOpen: false, staleFeed: true, sizeResult: {
      volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: null,
      direction: "buy", error: null,
    } });
    const btn = await screen.findByRole("button", { name: /buy/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(postJson).not.toHaveBeenCalledWith("/api/live/open/preview", expect.anything());
    expect(screen.getByTestId("stale-block").textContent).toBeTruthy();
  });

  // Replay's price IS the cursor bar's close — there is no feed to be stale.
  it("never applies the freshness gate in replay", async () => {
    const { openPosition } = renderChartPage({ replayOpen: true, staleFeed: true, sizeResult: {
      volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: null,
      direction: "buy", error: null,
    } });
    fireEvent.click(await screen.findByRole("button", { name: /buy/i }));
    await waitFor(() => expect(openPosition).toHaveBeenCalled());
  });

  it("hands the chart editable drawings keyed to the live symbol outside replay", async () => {
    const { fetchMock } = renderChartPage({ replayOpen: false });
    await screen.findByTestId("candle-chart");

    const calls = mockCandleChart.mock.calls;
    expect(calls[calls.length - 1][0].drawings.editable).toBe(true);

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]: [string, RequestInit?]) => u);
      expect(urls).toContain("/api/drawings?symbol=XAUUSDc");
    });
    const urls = fetchMock.mock.calls.map(([u]: [string, RequestInit?]) => u);
    expect(urls.some((u) => u.startsWith("/api/drawings?") && u.includes("session_id="))).toBe(false);
  });

  it("scopes drawings to the replay session while replaying", async () => {
    // Live annotations were made knowing what happened next; training must not
    // see them, and the session-scoped key is what enforces that.
    const { fetchMock } = renderChartPage({ replayOpen: true });
    await screen.findByTestId("candle-chart");
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]: [string, RequestInit?]) => u);
      expect(urls.some((u) => u.startsWith("/api/drawings?") && u.includes("session_id=1"))).toBe(true);
    });
  });

  // IMPORTANT 1: replay.start(cfg) is an async POST. setReplayOpen(true) lands
  // synchronously, well before the response assigns replay.session — so there
  // is a real window where replayOpen is true but replay.session is still
  // null. A naive `replay.session?.id ?? null` falls back to the LIVE
  // per-symbol key during that whole window, rendering it editable on the
  // replay chart; an edit landing there would persist to the live key. This
  // test drives that exact window: the mocked useReplaySession.start() never
  // assigns replaySession.session (standing in for "POST still in flight").
  it("does not read the live drawings key or render it editable while a replay session is starting", async () => {
    const { fetchMock } = renderChartPage({ replayOpen: false });
    await screen.findByTestId("candle-chart");
    // The initial (non-replay) mount already read the live key once — clear
    // so only what happens AFTER entering replay is under test.
    fetchMock.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /replay/i }));
    fireEvent.click(screen.getByRole("button", { name: /^mulai$/i }));

    // Confirms the window this test targets is real: start() was invoked but
    // (per the mock) never resolved a session.
    expect(replaySession.session).toBeNull();

    const urls = fetchMock.mock.calls.map(([u]: [string, RequestInit?]) => u);
    expect(urls).not.toContain("/api/drawings?symbol=XAUUSDc");

    const calls = mockCandleChart.mock.calls;
    const props = calls[calls.length - 1][0];
    expect(props.drawings.editable).toBe(false);
    expect(props.drawings.items).toEqual([]);
  });

  // Below lg the side column becomes a sheet. Rendering `sidePanel` in the lg
  // column AND the sheet at the same time would mount RiskSizePanel twice —
  // two "Buka" buttons aimed at the same live account, one of them invisible.
  // jsdom has no viewport, so both containers exist here and the count is the
  // assertion that catches it.
  it("moves the side panel into a sheet instead of duplicating it", async () => {
    renderChartPage({ replayOpen: false });
    await screen.findByTestId("candle-chart");

    expect(screen.getAllByText("Ukuran otomatis")).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Panel" }));

    expect(screen.getByRole("dialog", { name: "Panel chart" })).toBeInTheDocument();
    expect(screen.getAllByText("Ukuran otomatis")).toHaveLength(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
