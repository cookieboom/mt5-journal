import {
  forwardRef, useEffect, useImperativeHandle, useRef, useState, useCallback, useReducer,
} from "react";
import {
  createChart, CandlestickSeries, BarSeries, LineSeries, AreaSeries,
  ColorType, CrosshairMode, PriceScaleMode, LineStyle, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp, type SeriesType,
  type SeriesMarker, type Time,
} from "lightweight-charts";
import { toSeconds, liveLines, isNowVisible, type Sym, type Timeframe } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import type { Candle, HoverBar, LiveData } from "../lib/types";
import { wib } from "../lib/format";
import type { ChartHandle } from "../pages/Chart";
import MeasureOverlay, { type ProjectedPoint } from "./MeasureOverlay";
import {
  measureReducer, computeMetrics, isDoubleClickHold, IDLE,
  type MeasureState, type Point,
} from "../lib/measure";

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
  overlayLines?: import("../lib/types").PriceLineSpec[];
  fitToRange?: { startMs: number; endMs: number };
  markers?: SeriesMarker<Time>[];
}>(function CandleChart(props, ref) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<SeriesType> | null>(null);
  const markersPrimitive = useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const chartTypeFirstRun = useRef(true);
  const cbs = useRef(props);
  cbs.current = props;

  const [measure, setMeasure] = useState<MeasureState>(IDLE);
  const lastUp = useRef<{ ms: number; x: number; y: number } | null>(null);
  const dragging = useRef(false);
  const [, bumpProjection] = useReducer((c: number) => c + 1, 0);

  // Pointer pixel (relative to the pane) → data coordinates, using the current
  // series/timeScale. candles give a gap-aware bar time from the logical index.
  const toPoint = useCallback((px: number, py: number): Point | null => {
    const c = chart.current, s = series.current;
    if (!c || !s) return null;
    const price = s.coordinateToPrice(py);
    const logical = c.timeScale().coordinateToLogical(px);
    if (price === null || logical === null) return null;
    const cand = cbs.current.candles;
    const idx = Math.max(0, Math.min(cand.length - 1, Math.round(logical as number)));
    const barTimeMs = cand.length ? cand[idx].time_msc : 0;
    return { price: price as number, logical: logical as number, barTimeMs };
  }, []);

  // Always restore pan/zoom and reset drag state, regardless of which path
  // ended the drag (pointerup, Escape, pointercancel, or auto-clear on data
  // identity change).
  const endDrag = useCallback(() => {
    if (dragging.current) {
      dragging.current = false;
      chart.current?.applyOptions({ handleScroll: true, handleScale: true });
    }
  }, []);

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
      const cur = series.current;
      if (!cur) { cbs.current.onHover(null); return; }
      const d = param.seriesData.get(cur) as
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
      bumpProjection();
    });

    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      priceLines.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // autoSize:true resize doesn't always trigger a React re-render, so a frozen
  // measurement overlay (projected from chart coordinates) can lag the pane.
  // Force a re-render on resize so `project()` re-runs against fresh coords.
  useEffect(() => {
    const node = el.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => bumpProjection());
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  // Double-click-then-hold measurement gesture. Pure logic lives in measure.ts;
  // here we only translate DOM events ↔ chart coordinates and suppress panning
  // while dragging so the drag measures instead of scrolling the chart.
  useEffect(() => {
    const node = el.current;
    const c = chart.current;
    if (!node || !c) return;

    const rel = (e: PointerEvent) => {
      const r = node.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      const { x, y } = rel(e);
      const prev = lastUp.current;
      if (prev && isDoubleClickHold(prev.ms, prev.x, prev.y, e.timeStamp, x, y)) {
        const anchor = toPoint(x, y);
        if (!anchor) return;
        dragging.current = true;
        c.applyOptions({ handleScroll: false, handleScale: false });
        setMeasure((s) => measureReducer(s, { t: "start", anchor }));
        e.preventDefault();
      } else {
        // A plain press clears any frozen measurement.
        setMeasure((s) => (s.phase === "frozen" ? measureReducer(s, { t: "clear" }) : s));
      }
    };

    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      const { x, y } = rel(e);
      const cur = toPoint(x, y);
      if (cur) setMeasure((s) => measureReducer(s, { t: "move", cursor: cur }));
    };

    const onUp = (e: PointerEvent) => {
      const { x, y } = rel(e);
      lastUp.current = { ms: e.timeStamp, x, y };
      if (dragging.current) {
        endDrag();
        setMeasure((s) => measureReducer(s, { t: "release" }));
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        endDrag();
        setMeasure((s) => measureReducer(s, { t: "clear" }));
      }
    };

    const onCancel = () => {
      endDrag();
      setMeasure((s) => measureReducer(s, { t: "clear" }));
    };

    node.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointercancel", onCancel);
    return () => {
      node.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointercancel", onCancel);
    };
  }, [toPoint, endDrag]);

  // Data identity changed → the stored data coordinates may no longer line up.
  useEffect(() => {
    endDrag();
    setMeasure((s) => (s.phase === "idle" ? s : IDLE));
  }, [props.symbol, props.tf, props.settings.chartType, endDrag]);

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
    if (chartTypeFirstRun.current) { chartTypeFirstRun.current = false; return; }
    const c = chart.current;
    if (!c || !series.current) return;
    for (const pl of priceLines.current) series.current.removePriceLine(pl);
    priceLines.current = [];
    c.removeSeries(series.current);
    markersPrimitive.current = null;
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

  // Set markers when they change
  useEffect(() => {
    if (!series.current) return;
    const m = props.markers ?? [];
    if (typeof (series.current as any).setMarkers === "function") {
      (series.current as any).setMarkers(m);
    } else {
      if (!markersPrimitive.current) {
        markersPrimitive.current = createSeriesMarkers(series.current, m);
      } else {
        markersPrimitive.current.setMarkers(m);
      }
    }
  }, [props.markers, props.settings.chartType]);

  // Smart fit auto-focus
  useEffect(() => {
    if (!chart.current || !series.current || !props.fitToRange || props.candles.length === 0) return;
    const { startMs, endMs } = props.fitToRange;

    // Find the logical index (array index) of the start and end bars
    let startIndex = props.candles.findIndex(c => c.time_msc >= startMs);
    if (startIndex === -1) startIndex = props.candles.length - 1;

    let endIndex = props.candles.findIndex(c => c.time_msc >= endMs);
    if (endIndex === -1) endIndex = props.candles.length - 1;

    // Pad context: 10 bars before entry, 5 bars after exit
    const paddedStart = Math.max(0, startIndex - 10);
    let paddedEnd = Math.min(props.candles.length - 1, endIndex + 5);

    // Enforce 100 bars max zoom-out limit to prevent unreadable thin candles
    if (paddedEnd - paddedStart > 100) {
      paddedEnd = paddedStart + 100;
    }

    // Apply logical range
    chart.current.timeScale().setVisibleLogicalRange({
      from: paddedStart,
      to: paddedEnd,
    });
  }, [props.fitToRange, props.candles.length, props.settings.chartType]);

  // Live SL/TP/entry overlay — only when the current symbol has open positions
  // AND "now" is in view. Horizontal lines have no time, so they'd otherwise
  // hang over history where those levels never existed.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];

    // Replay (or any caller) supplies explicit lines → draw exactly those.
    const explicit = props.overlayLines;
    let specs: { price: number; color: string; title: string }[] = [];
    if (explicit !== undefined) {
      specs = explicit;
    } else {
      // Live SL/TP/entry overlay — only when the current symbol has open positions
      // AND "now" is in view (horizontal lines have no time).
      if (!props.settings.liveOverlay || !props.nowVisible || !props.live || props.live.live.empty) return;
      const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
      for (const pos of mine) specs.push(...liveLines(pos));
    }

    for (const line of specs) {
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
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay, props.settings.chartType, props.overlayLines]);

  useImperativeHandle(ref, () => ({
    jumpToNow: () => chart.current?.timeScale().scrollToRealTime(),
  }));

  const theme = props.settings.theme === "light" ? LIGHT : DARK;
  const project = (p: Point): ProjectedPoint | null => {
    const c = chart.current, s = series.current;
    if (!c || !s) return null;
    const x = c.timeScale().timeToCoordinate((p.barTimeMs / 1000) as UTCTimestamp);
    const y = s.priceToCoordinate(p.price);
    if (x === null || y === null) return null;
    return { x: x as number, y: y as number };
  };
  let overlay: JSX.Element | null = null;
  if (measure.phase !== "idle") {
    const a = project(measure.anchor);
    const cur = project(measure.cursor);
    const m = computeMetrics(measure.anchor, measure.cursor);
    const degenerate = m.bars === 0 && Math.abs(m.dPrice) < 1e-9;
    if (!degenerate && a && cur) {
      overlay = (
        <MeasureOverlay
          anchor={a} cursor={cur}
          metrics={m}
          upColor={theme.up} downColor={theme.down}
        />
      );
    }
  }

  return (
    <div className="relative w-full h-full">
      <div ref={el} className="w-full h-full" />
      {overlay}
    </div>
  );
});

export default CandleChart;
