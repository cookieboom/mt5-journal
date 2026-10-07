import { render } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { IChartApi } from "lightweight-charts";
import CandleChart from "./CandleChart";
import type { ChartSettings } from "../lib/chartPrefs";
import type { Candle } from "../lib/types";
import type { IndicatorResult } from "../lib/indicators";
import type { IndicatorRender } from "../hooks/useIndicatorSeries";

let chart: IChartApi | null = null;
let priceLineTitles: string[] = [];

vi.mock("lightweight-charts", async () => {
  const actual: any = await vi.importActual("lightweight-charts");
  return {
    ...actual,
    createChart: (container: HTMLElement, options: any) => {
      const c = actual.createChart(container, options);
      const add = c.addSeries;
      c.addSeries = (...args: any[]) => {
        const s = add.apply(c, args);
        const cpl = s.createPriceLine;
        s.createPriceLine = (o: any) => { priceLineTitles.push(o.title); return cpl.call(s, o); };
        return s;
      };
      chart = c;
      return c;
    },
  };
});

const SETTINGS: ChartSettings = {
  version: 1, chartType: "candle", theme: "dark", grid: true, crosshair: "normal",
  priceScale: "linear", autoScale: true, lastPriceLine: true, liveOverlay: true,
  defaultSymbol: "XAUUSDc", defaultTimeframe: "M5", initialBars: 300, maxBars: 3000,
  colors: { up: "#34d399", down: "#fb7185", wick: "#9a97c4" },
};

const candles: Candle[] = Array.from({ length: 5 }, (_, i) => ({
  time_msc: 1_000_000 + i * 60_000, o: 100, h: 101, l: 99, c: 100, v: 1,
}));
const times = candles.map((c) => c.time_msc);

function res(pane: string, hline = false): IndicatorResult {
  return {
    times,
    plots: [{ title: pane, color: "cyan", pane, style: "line", width: 2, values: [null, 1, 2, 3, 4] }],
    hlines: hline ? [{ value: 70, title: "OB", pane, color: "muted" }] : [],
    signals: [], lookback: 1, warm_from_msc: times[1], forming_msc: null, pending: false,
  };
}

beforeEach(() => {
  chart = null;
  priceLineTitles = [];
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    measureText: () => ({ width: 10 }), fillRect: () => {}, clearRect: () => {},
    getImageData: () => ({ data: [] }), putImageData: () => {}, createImageData: () => [],
    setTransform: () => {}, drawImage: () => {}, save: () => {}, fillText: () => {},
    restore: () => {}, beginPath: () => {}, moveTo: () => {}, lineTo: () => {},
    closePath: () => {}, stroke: () => {}, translate: () => {}, scale: () => {},
    rotate: () => {}, arc: () => {}, fill: () => {}, transform: () => {}, rect: () => {},
    clip: () => {},
  } as unknown as CanvasRenderingContext2D);
});

function view(indicators: IndicatorRender[], settings = SETTINGS) {
  return (
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={settings} candles={candles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={times[4]} live={null} nowVisible indicators={indicators}
    />
  );
}

const seriesPerPane = () => chart!.panes().map((p) => p.getSeries().length);

it("overlays share the price pane and oscillators get their own pane", () => {
  render(view([{ id: "a", result: res("price") }, { id: "b", result: res("rsi", true) }]));
  expect(seriesPerPane()).toEqual([2, 1]);
  expect(priceLineTitles).toContain("OB");
});

it("removing an indicator removes its pane", () => {
  const r = render(view([{ id: "a", result: res("price") }, { id: "b", result: res("rsi") }]));
  r.rerender(view([{ id: "a", result: res("price") }]));
  expect(seriesPerPane()).toEqual([2]);
  r.rerender(view([]));
  expect(seriesPerPane()).toEqual([1]);
});

it("two instances of the same oscillator get two panes", () => {
  render(view([{ id: "a", result: res("rsi") }, { id: "b", result: res("rsi") }]));
  expect(seriesPerPane()).toEqual([1, 1, 1]);
});

it("a chart-type switch keeps the indicator series", () => {
  const items = [{ id: "a", result: res("price") }, { id: "b", result: res("rsi") }];
  const r = render(view(items));
  r.rerender(view(items, { ...SETTINGS, chartType: "line" }));
  expect(seriesPerPane()).toEqual([2, 1]);
});

it("a data-only update reuses the series", () => {
  const r = render(view([{ id: "a", result: res("rsi") }]));
  const before = chart!.panes()[1].getSeries()[0];
  const next = { ...res("rsi"), plots: [{ ...res("rsi").plots[0], values: [1, 1, 1, 1, 9] }] };
  r.rerender(view([{ id: "a", result: next }]));
  const after = chart!.panes()[1].getSeries()[0];
  expect(after).toBe(before);
  expect((after.data().at(-1) as { value: number }).value).toBe(9);
});

it("a chart-type switch with only an oscillator keeps the price pane first", () => {
  const items = [{ id: "a", result: res("rsi") }];
  const r = render(view(items));
  r.rerender(view(items, { ...SETTINGS, chartType: "line" }));
  expect(seriesPerPane()).toEqual([1, 1]);
  expect(chart!.panes()[1].getSeries()[0].seriesType()).toBe("Line");
  expect(chart!.panes()[0].getSeries()[0].options()).not.toHaveProperty("title", "rsi");
});
