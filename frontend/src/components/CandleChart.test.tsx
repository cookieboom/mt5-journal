import { render } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import CandleChart from "./CandleChart";
import type { Candle } from "../lib/types";
import type { ChartSettings } from "../lib/chartPrefs";
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

let capturedMarkers: any = null;
let capturedLogicalRange: any = null;

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
        return s;
      };
      const ts = chart.timeScale();
      const origSetVisibleLogicalRange = ts.setVisibleLogicalRange;
      ts.setVisibleLogicalRange = (range: any) => {
        capturedLogicalRange = range;
        return origSetVisibleLogicalRange ? origSetVisibleLogicalRange.apply(ts, [range]) : null;
      };
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
