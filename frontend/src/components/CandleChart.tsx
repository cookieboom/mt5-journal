import {
  forwardRef, useEffect, useImperativeHandle, useRef,
} from "react";
import {
  createChart, CandlestickSeries, BarSeries, LineSeries, AreaSeries,
  ColorType, CrosshairMode, PriceScaleMode, LineStyle,
  type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp, type SeriesType,
} from "lightweight-charts";
import { toSeconds, liveLines, isNowVisible, type Sym, type Timeframe } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import type { Candle, HoverBar, LiveData } from "../lib/types";
import { wib } from "../lib/format";
import type { ChartHandle } from "../pages/Chart";

const DARK = {
  bg: "transparent", text: "#9a97c4", grid: "rgba(255,255,255,0.06)",
  border: "rgba(255,255,255,0.09)", up: "#34d399", down: "#fb7185",
};
const LIGHT = {
  bg: "#ffffff", text: "#334155", grid: "rgba(0,0,0,0.06)",
  border: "rgba(0,0,0,0.12)", up: "#059669", down: "#e11d48",
};

const CROSSHAIR = {
  normal: CrosshairMode.Normal, magnet: CrosshairMode.Magnet, hidden: CrosshairMode.Hidden,
} as const;

// Candle/bar carry OHLC; line/area carry a single value (close).
function isOHLC(t: ChartSettings["chartType"]): boolean {
  return t === "candle" || t === "bar";
}
function seriesData(candles: Candle[], t: ChartSettings["chartType"]) {
  return candles.map((c) =>
    isOHLC(t)
      ? { time: toSeconds(c.time_msc) as UTCTimestamp, open: c.o, high: c.h, low: c.l, close: c.c }
      : { time: toSeconds(c.time_msc) as UTCTimestamp, value: c.c },
  );
}
function addSeriesFor(chart: IChartApi, s: ChartSettings): ISeriesApi<SeriesType> {
  const { up, down, wick } = s.colors;
  switch (s.chartType) {
    case "bar":
      return chart.addSeries(BarSeries, { upColor: up, downColor: down });
    case "line":
      return chart.addSeries(LineSeries, { color: up, lineWidth: 2 });
    case "area":
      return chart.addSeries(AreaSeries, { lineColor: up, topColor: up, bottomColor: "transparent" });
    case "candle":
    default:
      return chart.addSeries(CandlestickSeries, {
        upColor: up, downColor: down, wickUpColor: wick, wickDownColor: wick,
        borderVisible: false,
      });
  }
}

const CandleChart = forwardRef<ChartHandle, {
  symbol: Sym;
  tf: Timeframe;
  settings: ChartSettings;
  candles: Candle[];
  onHover: (b: HoverBar | null) => void;
  onNowVisibleChange: (v: boolean) => void;
  onRequestOlder: () => void;
  lastBarMs: number | null;
  live: LiveData | null;
  nowVisible: boolean;
}>(function CandleChart(props, ref) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<SeriesType> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const cbs = useRef(props);
  cbs.current = props;

  // Create the chart once.
  useEffect(() => {
    if (!el.current) return;
    const theme = props.settings.theme === "light" ? LIGHT : DARK;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: props.settings.grid },
        horzLines: { color: theme.grid, visible: props.settings.grid },
      },
      crosshair: { mode: CROSSHAIR[props.settings.crosshair] },
      rightPriceScale: {
        borderColor: theme.border,
        mode: props.settings.priceScale === "log" ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
        autoScale: props.settings.autoScale,
      },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
        // Axis labels in WIB (server=UTC, +7h; display only).
        tickMarkFormatter: (t: number) => wib((t as number) * 1000, 0).replace(" WIB", ""),
      },
      localization: { timeFormatter: (t: number) => wib((t as number) * 1000, 0) },
    });
    const s = addSeriesFor(c, props.settings);
    s.applyOptions({ priceLineVisible: props.settings.lastPriceLine });
    chart.current = c;
    series.current = s;

    c.subscribeCrosshairMove((param) => {
      const d = param.seriesData.get(s) as
        | { open: number; high: number; low: number; close: number }
        | { value: number } | undefined;
      if (!d || param.time === undefined) { cbs.current.onHover(null); return; }
      const single = "value" in d;
      const close = single ? d.value : d.close;
      cbs.current.onHover({
        time_msc: (param.time as number) * 1000,
        o: single ? close : d.open,
        h: single ? close : d.high,
        l: single ? close : d.low,
        c: close, v: 0,
      });
    });

    c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || !series.current) return;
      const bars = series.current.barsInLogicalRange(range);
      if (bars && bars.barsBefore < 20) cbs.current.onRequestOlder();
      const vis = c.timeScale().getVisibleRange();
      const toMs = vis ? (vis.to as number) * 1000 : null;
      const last = cbs.current.lastBarMs;
      cbs.current.onNowVisibleChange(isNowVisible(last, toMs, cbs.current.tf));
    });

    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      priceLines.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply live-appliable settings when they change (no full re-create; chart
  // type is handled by its own recreate effect below).
  useEffect(() => {
    if (!chart.current || !series.current) return;
    const s = props.settings;
    const theme = s.theme === "light" ? LIGHT : DARK;
    chart.current.applyOptions({
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: s.grid },
        horzLines: { color: theme.grid, visible: s.grid },
      },
      crosshair: { mode: CROSSHAIR[s.crosshair] },
      rightPriceScale: {
        borderColor: theme.border,
        mode: s.priceScale === "log" ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
        autoScale: s.autoScale,
      },
    });
    // Colour options depend on series type; candle/bar use up/down, line/area a single colour.
    if (s.chartType === "candle") {
      series.current.applyOptions({
        upColor: s.colors.up, downColor: s.colors.down,
        wickUpColor: s.colors.wick, wickDownColor: s.colors.wick,
      });
    } else if (s.chartType === "bar") {
      series.current.applyOptions({ upColor: s.colors.up, downColor: s.colors.down });
    } else if (s.chartType === "line") {
      series.current.applyOptions({ color: s.colors.up });
    } else {
      series.current.applyOptions({ lineColor: s.colors.up, topColor: s.colors.up });
    }
    series.current.applyOptions({ priceLineVisible: s.lastPriceLine });
  }, [props.settings]);

  // Chart type change: recreate the SERIES only (not the whole chart, so pan/
  // zoom and theme survive), re-set data, and drop price lines (the overlay
  // effect below redraws them — it depends on chartType).
  useEffect(() => {
    const c = chart.current;
    if (!c || !series.current) return;
    for (const pl of priceLines.current) series.current.removePriceLine(pl);
    priceLines.current = [];
    c.removeSeries(series.current);
    const s = addSeriesFor(c, props.settings);
    s.applyOptions({ priceLineVisible: props.settings.lastPriceLine });
    s.setData(seriesData(cbs.current.candles, props.settings.chartType));
    series.current = s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.settings.chartType]);

  // Push candle data.
  useEffect(() => {
    if (!series.current) return;
    series.current.setData(seriesData(props.candles, props.settings.chartType));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.candles]);

  // Live SL/TP/entry overlay — only when the current symbol has open positions
  // AND "now" is in view. Horizontal lines have no time, so they'd otherwise
  // hang over history where those levels never existed.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];
    if (!props.settings.liveOverlay || !props.nowVisible || !props.live || props.live.live.empty) return;
    const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
    for (const pos of mine) {
      for (const line of liveLines(pos)) {
        priceLines.current.push(
          s.createPriceLine({
            price: line.price,
            color: line.color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: line.title,
          }),
        );
      }
    }
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay]);

  useImperativeHandle(ref, () => ({
    jumpToNow: () => chart.current?.timeScale().scrollToRealTime(),
  }));

  return <div ref={el} className="w-full h-full" />;
});

export default CandleChart;
