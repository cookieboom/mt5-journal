import type { ComponentProps } from "react";
import { render, fireEvent } from "@testing-library/react";
import { it, expect, vi, beforeEach, describe } from "vitest";
import CandleChart from "./CandleChart";
import type { Candle, PlannedOrder } from "../lib/types";
import type { ChartSettings } from "../lib/chartPrefs";
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { PLANNED_ID, type DraggablePosition } from "../lib/sltpDrag";
import type { LiveData, LivePosition } from "../lib/types";

let capturedMarkers: SeriesMarker<Time>[] | null = null;
let capturedLogicalRange: { from: number; to: number } | null = null;
let capturedPriceLines: { price: number; color: string; title: string; axisLabelVisible?: boolean }[] = [];
let capturedSeriesOptions: Record<string, unknown> = {};

vi.mock("lightweight-charts", async () => {
  const actual: any = await vi.importActual("lightweight-charts");
  return {
    ...actual,
    createChart: (container: HTMLElement, options: any) => {
      const chart = actual.createChart(container, options);
      const origAddSeries = chart.addSeries;
      chart.addSeries = (...args: any[]) => {
        const s = origAddSeries.apply(chart, args as any);
        const origSetMarkers = s.setMarkers;
        s.setMarkers = (m: any) => {
          capturedMarkers = m;
          return origSetMarkers ? origSetMarkers.apply(s, [m]) : null;
        };
        const origCreatePriceLine = s.createPriceLine;
        s.createPriceLine = (opts: any) => {
          capturedPriceLines.push({
            price: opts.price, color: opts.color, title: opts.title,
            axisLabelVisible: opts.axisLabelVisible,
          });
          return origCreatePriceLine ? origCreatePriceLine.call(s, opts) : { applyOptions: () => {}, options: () => opts, remove: () => {} };
        };
        const origApplyOptions = s.applyOptions;
        s.applyOptions = (opts: any) => {
          Object.assign(capturedSeriesOptions, opts);
          return origApplyOptions ? origApplyOptions.call(s, opts) : undefined;
        };
        // Deterministic pixel<->price mapping for hit-test/drag math:
        // y=200 <-> price=100 (SL line), y=100 <-> price=110 (TP line),
        // y=150 <-> price=105 (entry line); 1px = 0.1 price unit elsewhere.
        s.priceToCoordinate = (price: number) => 200 - (price - 100) * 10;
        s.coordinateToPrice = (y: number) => 100 + (200 - y) / 10;
        return s;
      };
      const ts = chart.timeScale();
      const origSetVisibleLogicalRange = ts.setVisibleLogicalRange;
      ts.setVisibleLogicalRange = (range: any) => {
        capturedLogicalRange = range;
        return origSetVisibleLogicalRange ? origSetVisibleLogicalRange.apply(ts, [range]) : null;
      };
      // jsdom gives the chart pane zero layout width, so the real
      // coordinateToLogical always returns null — which makes toPoint()
      // (price-axis coord AND time-axis coord both required) null for any
      // pixel, even though the price-axis stubs above are enough on their
      // own for the pointer-drag tests. Stub it to something total (any
      // finite logical index) so toPoint() only fails on genuine price-axis
      // nulls, matching real-browser behavior where both axes resolve.
      ts.coordinateToLogical = (x: number) => x;
      ts.logicalToCoordinate = (i: number) => i;
      return chart;
    },
  };
});

const DEFAULT_SETTINGS: ChartSettings = {
  version: 1,
  chartType: "candle",
  theme: "dark",
  grid: true,
  crosshair: "normal",
  priceScale: "linear",
  autoScale: true,
  lastPriceLine: true,
  liveOverlay: true,
  defaultSymbol: "XAUUSDc",
  defaultTimeframe: "M5",
  initialBars: 300,
  maxBars: 3000,
  colors: { up: "#34d399", down: "#fb7185", wick: "#9a97c4" },
};

beforeEach(() => {
  capturedMarkers = null;
  capturedLogicalRange = null;
  capturedPriceLines = [];
  capturedSeriesOptions = {};

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

// 20 candles spaced by 60s (1,000,000 ms to 2,140,000 ms)
const mockCandles: Candle[] = Array.from({ length: 20 }, (_, i) => ({
  time_msc: 1_000_000 + i * 60_000,
  o: 100 + i,
  h: 105 + i,
  l: 95 + i,
  c: 102 + i,
  v: 10,
}));

it("applies markers to series when markers prop is provided", () => {
  const dummyMarkers: SeriesMarker<Time>[] = [
    { time: 1000 as UTCTimestamp, position: "aboveBar", color: "green", shape: "arrowUp", text: "Buy" },
  ];

  render(
    <CandleChart
      symbol="XAUUSDc"
      tf="M1"
      settings={DEFAULT_SETTINGS}
      candles={mockCandles}
      onHover={() => {}}
      onNowVisibleChange={() => {}}
      onRequestOlder={() => {}}
      lastBarMs={2_140_000}
      live={null}
      nowVisible={true}
      markers={dummyMarkers}
    />
  );

  expect(capturedMarkers).toEqual(dummyMarkers);
});

it("applies fitToRange to visible logical range when fitToRange prop is provided", () => {
  const fitToRange = { startMs: 1_300_000, endMs: 1_600_000 };

  render(
    <CandleChart
      symbol="XAUUSDc"
      tf="M1"
      settings={DEFAULT_SETTINGS}
      candles={mockCandles}
      onHover={() => {}}
      onNowVisibleChange={() => {}}
      onRequestOlder={() => {}}
      lastBarMs={2_140_000}
      live={null}
      nowVisible={true}
      fitToRange={fitToRange}
    />
  );

  // index for startMs (1_300_000) is 5
  // index for endMs (1_600_000) is 10
  // paddedStart = max(0, 5 - 10) = 0
  // paddedEnd = min(19, 10 + 5) = 15
  expect(capturedLogicalRange).toEqual({ from: 0, to: 15 });
});

it("enforces max 100 bars limit when fitToRange spans > 100 bars", () => {
  const manyCandles: Candle[] = Array.from({ length: 200 }, (_, i) => ({
    time_msc: 1_000_000 + i * 60_000,
    o: 100, h: 105, l: 95, c: 102, v: 10,
  }));
  // startMs at index 10 (paddedStart = 0)
  // endMs at index 180 (paddedEnd would be min(199, 185) = 185)
  // paddedEnd - paddedStart = 185 > 100 -> paddedEnd becomes 0 + 100 = 100
  const fitToRange = { startMs: 1_000_000 + 10 * 60_000, endMs: 1_000_000 + 180 * 60_000 };

  render(
    <CandleChart
      symbol="XAUUSDc"
      tf="M1"
      settings={DEFAULT_SETTINGS}
      candles={manyCandles}
      onHover={() => {}}
      onNowVisibleChange={() => {}}
      onRequestOlder={() => {}}
      lastBarMs={manyCandles[199].time_msc}
      live={null}
      nowVisible={true}
      fitToRange={fitToRange}
    />
  );

  expect(capturedLogicalRange).toEqual({ from: 0, to: 100 });
});

it("handles startMs/endMs beyond candle range gracefully", () => {
  // startMs and endMs beyond the last candle timestamp -> index is candles.length - 1 (19)
  // paddedStart = max(0, 19 - 10) = 9
  // paddedEnd = min(19, 19 + 5) = 19
  const fitToRange = { startMs: 9_999_999, endMs: 9_999_999 };

  render(
    <CandleChart
      symbol="XAUUSDc"
      tf="M1"
      settings={DEFAULT_SETTINGS}
      candles={mockCandles}
      onHover={() => {}}
      onNowVisibleChange={() => {}}
      onRequestOlder={() => {}}
      lastBarMs={2_140_000}
      live={null}
      nowVisible={true}
      fitToRange={fitToRange}
    />
  );

  expect(capturedLogicalRange).toEqual({ from: 9, to: 19 });
});

it("handles malformed fitToRange (startMs > endMs) safely without setting logical range", () => {
  const fitToRange = { startMs: 2_000_000, endMs: 1_000_000 };

  render(
    <CandleChart
      symbol="XAUUSDc"
      tf="M1"
      settings={DEFAULT_SETTINGS}
      candles={mockCandles}
      onHover={() => {}}
      onNowVisibleChange={() => {}}
      onRequestOlder={() => {}}
      lastBarMs={2_140_000}
      live={null}
      nowVisible={true}
      fitToRange={fitToRange}
    />
  );

  expect(capturedLogicalRange).toBeNull();
});

const draggablePos: DraggablePosition = { id: 1, direction: "buy", entry_price: 105, sl: 100, tp: 110 };

it("renders draggable SL/TP/entry lines when draggablePositions is provided", () => {
  render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
    />
  );

  const prices = capturedPriceLines.map((l) => l.price).sort((a, b) => a - b);
  expect(prices).toEqual([100, 105, 110]);
});

it("dragging the SL line to a new pixel position calls onSlTpChange with the new price", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
      onSlTpChange={onSlTpChange}
    />
  );
  // The pointer listeners are attached to the inner chart-container div (the
  // one with the `el` ref), which is the second "div > div" match — the
  // first match is CandleChart's own outer positioning wrapper. Events fired
  // on a wrong (ancestor) node would never reach the inner listeners, since
  // DOM events bubble up, not down.
  const node = container.querySelectorAll("div > div")[1] as HTMLElement;

  // SL line is at y=200 (price 100, per the mock mapping). Press there,
  // move to y=180 (price 102), release.
  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 180 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 180 });

  expect(onSlTpChange).toHaveBeenCalledWith(1, { sl: 102 });
});

it("double-clicking an existing SL line calls onSlTpChange with sl: 0 (remove)", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelectorAll("div > div")[1] as HTMLElement;

  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 200 });
  fireEvent.pointerDown(node, { clientX: 51, clientY: 201 });   // within 350ms/5px -> double-click-hold

  expect(onSlTpChange).toHaveBeenCalledWith(1, { sl: 0 });
});

it("does not confuse a plain drag-a-line with the Spec-B measure gesture", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelectorAll("div > div")[1] as HTMLElement;

  // A single (non-double) press-and-drag on the SL line must go through the
  // drag-a-line path, not fall through into the idle "clear frozen measure" branch.
  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 190 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 190 });

  expect(onSlTpChange).toHaveBeenCalledTimes(1);
});

// Live-fallback path: draggablePositions is deliberately NOT passed here —
// this is the exact Task-10-dependency path (live position lines become
// draggable automatically whenever onSlTpChange is passed and
// draggablePositions is undefined). Uses the same entry=105/sl=100/tp=110
// values as draggablePos above so the mock's fixed pixel<->price mapping
// (y=200<->100, y=150<->105, y=100<->110) applies identically.
const liveBuyPos: LivePosition = {
  position_id: 9, symbol: "XAUUSDc", symbol_base: "XAUUSD",
  direction: "buy", volume: 0.1, open_price: 105, price_current: 105,
  sl: 100, tp: 110, profit: 0, observed_msc: 1,
};
const liveDataFor = (pos: LivePosition): LiveData => ({
  header: { login: 0, currency: "USC", offset_s: 0 },
  live: {
    positions: [pos], count: 1, total_floating: 0, total_volume: pos.volume,
    age_s: 0, stale: false, empty: false,
  },
});

it("dragging a LIVE position's SL line (draggablePositions undefined) calls onSlTpChange", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={liveDataFor(liveBuyPos)} nowVisible={true}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelectorAll("div > div")[1] as HTMLElement;

  // SL line at y=200 (price 100). Drag to y=180 (price 102).
  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 180 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 180 });

  expect(onSlTpChange).toHaveBeenCalledWith(9, { sl: 102 });
});

it("dragging a LIVE position's ENTRY line resolves to sl/tp by direction, never { entry }", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={liveDataFor(liveBuyPos)} nowVisible={true}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelectorAll("div > div")[1] as HTMLElement;

  // Entry line at y=150 (price 105). Drag up to y=130 (price 107) — above
  // entry for a "buy" position, so this must resolve to tp, not { entry }.
  fireEvent.pointerDown(node, { clientX: 50, clientY: 150 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 130 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 130 });

  expect(onSlTpChange).toHaveBeenCalledWith(9, { tp: 107 });
});

// Shared render helper: fills in the required props with the same defaults
// used throughout this file, forwards any overrides (e.g. plannedOrder,
// draggablePositions, onSlTpChange), and exposes the captured price lines
// plus a drag helper built on the mock's fixed pixel<->price mapping
// (price 100 <-> y=200, 1px = 0.1 price unit — see the lightweight-charts
// mock above).
function renderChart(overrides: Partial<ComponentProps<typeof CandleChart>> = {}) {
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      {...overrides}
    />
  );
  const node = container.querySelectorAll("div > div")[1] as HTMLElement;
  const yFor = (price: number) => 200 - (price - 100) * 10;
  const dragLineTo = (fromPrice: number, toPrice: number) => {
    fireEvent.pointerDown(node, { clientX: 50, clientY: yFor(fromPrice) });
    fireEvent.pointerMove(window, { clientX: 50, clientY: yFor(toPrice) });
    fireEvent.pointerUp(window, { clientX: 50, clientY: yFor(toPrice) });
  };
  return { container, node, priceLines: capturedPriceLines, dragLineTo };
}

describe("planned-order lines", () => {
  it("draws entry, SL and TP lines for a planned order", () => {
    const plannedOrder: PlannedOrder = { entry: 4035, sl: 4030, tp: 4045, direction: "buy" };
    const { priceLines } = renderChart({ plannedOrder });
    const prices = priceLines.map((l) => l.price).sort((a, b) => a - b);
    expect(prices).toEqual([4030, 4035, 4045]);
  });

  it("omits an unset SL and TP rather than drawing them at 0", () => {
    const plannedOrder: PlannedOrder = { entry: 4035, sl: null, tp: null, direction: null };
    const { priceLines } = renderChart({ plannedOrder });
    expect(priceLines.map((l) => l.price)).toEqual([4035]);
  });

  it("reports a planned-line drag under the PLANNED_ID sentinel", () => {
    const onSlTpChange = vi.fn();
    const plannedOrder: PlannedOrder = { entry: 4035, sl: 4030, tp: null, direction: "buy" };
    const { dragLineTo } = renderChart({ plannedOrder, onSlTpChange });
    dragLineTo(4030, 4028);
    expect(onSlTpChange).toHaveBeenCalledWith(PLANNED_ID, { sl: 4028 });
  });

  it("a drag from the planned ENTRY line becomes the SL while no side is known", () => {
    const onSlTpChange = vi.fn();
    const plannedOrder: PlannedOrder = { entry: 4035, sl: null, tp: null, direction: null };
    const { dragLineTo } = renderChart({ plannedOrder, onSlTpChange });
    dragLineTo(4035, 4030);
    expect(onSlTpChange).toHaveBeenCalledWith(PLANNED_ID, { sl: 4030 });
  });

  // lightweight-charts paints a price line's title from its price-AXIS view,
  // which bails out entirely when axisLabelVisible is false — a titled line
  // with no axis label therefore shows nothing at all. The bar-close countdown
  // rides this line's title, so: axis label ON, and the series' own last-value
  // badge OFF, keeping exactly one price marker on the scale.
  it("keeps the axis label on the planned entry line so its countdown title paints", () => {
    const plannedOrder: PlannedOrder = { entry: 4035, sl: null, tp: null, direction: null };
    const { priceLines } = renderChart({ plannedOrder, countdown: true, tf: "M5" });
    const entry = priceLines.find((l) => l.price === 4035)!;
    expect(entry.axisLabelVisible).toBe(true);
    expect(entry.title).toMatch(/^\d{2}:\d{2}$/);
    expect(capturedSeriesOptions.lastValueVisible).toBe(false);
  });

  it("leaves the series last-value badge alone when there is no planned entry line", () => {
    renderChart({ countdown: true });
    expect(capturedSeriesOptions.lastValueVisible).toBe(true);
  });

  it("planned lines coexist with real position lines without colliding", () => {
    const plannedOrder: PlannedOrder = { entry: 4035, sl: 4030, tp: null, direction: "buy" };
    const draggablePositions: DraggablePosition[] = [
      { id: 1, direction: "buy", entry_price: 4000, sl: 3990, tp: 0 },
    ];
    const { priceLines } = renderChart({ plannedOrder, draggablePositions });
    expect(priceLines.map((l) => l.price).sort((a, b) => a - b))
      .toEqual([3990, 4000, 4030, 4035]);
  });
});

describe("drawing gesture", () => {
  // Only items/editable are ever overridden below — narrowed to those two
  // (rather than the full Partial<drawings>) so the spread doesn't widen
  // onAdd/onUpdate/onDelete/onClearAll's type away from vi.fn()'s Mock, which
  // would otherwise lose `.mock.calls` under tsc (vitest itself doesn't
  // typecheck, so this only surfaces via `tsc -b`).
  const drawingProps = (
    over: Partial<Pick<NonNullable<ComponentProps<typeof CandleChart>["drawings"]>, "items" | "editable">> = {},
  ) => ({
    items: [],
    editable: true,
    onAdd: vi.fn(),
    onUpdate: vi.fn(),
    onDelete: vi.fn(),
    onClearAll: vi.fn(),
    ...over,
  });

  const base = {
    symbol: "XAUUSDc" as const,
    tf: "M1" as const,
    settings: DEFAULT_SETTINGS,
    candles: mockCandles,
    onHover: () => {},
    onNowVisibleChange: () => {},
    onRequestOlder: () => {},
    lastBarMs: null,
    live: null,
    nowVisible: false,
  };

  it("renders neither palette nor overlay when the drawings prop is absent", () => {
    const { container, queryByTestId } = render(<CandleChart {...base} />);
    expect(queryByTestId("drawing-overlay")).toBeNull();
    expect(container.querySelector('[aria-label="trendline"]')).toBeNull();
  });

  it("renders the palette when editable and hides it when read-only", () => {
    const { container, rerender } = render(<CandleChart {...base} drawings={drawingProps()} />);
    expect(container.querySelector('[aria-label="trendline"]')).toBeTruthy();
    rerender(<CandleChart {...base} drawings={drawingProps({ editable: false })} />);
    expect(container.querySelector('[aria-label="trendline"]')).toBeNull();
  });

  it("draws a trendline from press to release and returns the tool to cursor", () => {
    const props = drawingProps();
    const { container } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="trendline"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;

    fireEvent.pointerDown(pane, { clientX: 10, clientY: 200 });
    fireEvent.pointerMove(window, { clientX: 60, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 60, clientY: 100 });

    expect(props.onAdd).toHaveBeenCalledTimes(1);
    const added = props.onAdd.mock.calls[0][0];
    expect(added.kind).toBe("trend");
    expect(added.a.price).toBeCloseTo(100, 6);
    expect(added.b.price).toBeCloseTo(110, 6);
    // one object per tool click: the palette falls back to cursor afterwards
    expect(container.querySelector('[aria-label="kursor"]')!.getAttribute("aria-pressed")).toBe("true");
  });

  it("discards a degenerate object drawn with no movement", () => {
    const props = drawingProps();
    const { container } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="kotak"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 10, clientY: 200 });
    fireEvent.pointerUp(window, { clientX: 10, clientY: 200 });
    expect(props.onAdd).not.toHaveBeenCalled();
  });

  it("escape cancels an in-progress draw", () => {
    const props = drawingProps();
    const { container } = render(<CandleChart {...base} drawings={props} />);
    fireEvent.click(container.querySelector('[aria-label="trendline"]')!);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 10, clientY: 200 });
    fireEvent.pointerMove(window, { clientX: 60, clientY: 100 });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.pointerUp(window, { clientX: 60, clientY: 100 });
    expect(props.onAdd).not.toHaveBeenCalled();
  });

  it("selects an existing drawing and deletes it with the Delete key", () => {
    const hline = { id: "h1", kind: "hline" as const, price: 105 };
    const props = drawingProps({ items: [hline] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    // price 105 sits at y=150 under the harness mapping
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 150 });
    fireEvent.keyDown(window, { key: "Delete" });
    expect(props.onDelete).toHaveBeenCalledWith("h1");
  });

  it("drags a selected hline to a new price", () => {
    const hline = { id: "h1", kind: "hline" as const, price: 105 };
    const props = drawingProps({ items: [hline] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 100 });
    expect(props.onUpdate).toHaveBeenCalledTimes(1);
    expect(props.onUpdate.mock.calls[0][0].price).toBeCloseTo(110, 6);
  });

  it("lets an SL/TP line win over a drawing at the same pixel", () => {
    const onSlTpChange = vi.fn();
    const positions: DraggablePosition[] = [
      { id: 5, direction: "buy", entry_price: 105, sl: 100, tp: 110 },
    ];
    // A drawing sits exactly on the SL line (price 100 → y=200).
    const props = drawingProps({ items: [{ id: "h1", kind: "hline", price: 100 }] });
    const { container } = render(
      <CandleChart {...base} drawings={props} draggablePositions={positions} onSlTpChange={onSlTpChange} />,
    );
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 200 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 190 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 190 });
    expect(onSlTpChange).toHaveBeenCalledTimes(1);
    expect(props.onUpdate).not.toHaveBeenCalled();
  });

  it("lets a double-click-hold measure gesture win over a drawing at the same pixel", () => {
    // A drawing sits exactly where the double-click-hold lands (price 105 → y=150).
    const props = drawingProps({ items: [{ id: "h1", kind: "hline", price: 105 }] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;

    // First press+release, then a second down within DBLCLICK_MS/DBLCLICK_PX
    // — the same trigger the pre-existing SL/TP double-click test uses.
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 150 });
    fireEvent.pointerDown(pane, { clientX: 41, clientY: 151 });
    fireEvent.pointerMove(window, { clientX: 41, clientY: 130 });
    // The trailing release matters: without the reserved() guard, this second
    // press would have grabbed the hline instead of deferring to measure, and
    // this pointerup is what would commit that grab as an onUpdate call.
    fireEvent.pointerUp(window, { clientX: 41, clientY: 130 });

    expect(props.onUpdate).not.toHaveBeenCalled();
    // …the measure gesture, not a drawing re-grab, is what actually ran
    expect(container.querySelector('[data-testid="measure-overlay"]')).toBeTruthy();
  });

  it("attaches no drawing listeners when read-only", () => {
    const props = drawingProps({ editable: false, items: [{ id: "h1", kind: "hline", price: 105 }] });
    const { container } = render(<CandleChart {...base} drawings={props} />);
    const pane = container.querySelector(".w-full.h-full > div")! as HTMLElement;
    fireEvent.pointerDown(pane, { clientX: 40, clientY: 150 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 40, clientY: 100 });
    expect(props.onUpdate).not.toHaveBeenCalled();
    // …but the object is still drawn
    expect(container.querySelector('[data-testid="drawing-h1"]')).toBeTruthy();
  });
});
