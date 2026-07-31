import {
  forwardRef, useEffect, useImperativeHandle, useRef, useState, useCallback, useReducer,
} from "react";
import {
  createChart, CandlestickSeries, BarSeries, LineSeries, AreaSeries,
  ColorType, CrosshairMode, PriceScaleMode, LineStyle, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp, type SeriesType,
  type SeriesMarker, type Time,
} from "lightweight-charts";
import { toSeconds, isNowVisible, LINE_COLORS, type Sym, type Timeframe } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import type { Candle, HoverBar, LiveData } from "../lib/types";
import { wib } from "../lib/format";
import type { ChartHandle } from "../pages/Chart";
import MeasureOverlay, { type ProjectedPoint } from "./MeasureOverlay";
import {
  measureReducer, computeMetrics, isDoubleClickHold, IDLE,
  type MeasureState, type Point,
} from "../lib/measure";
import CoverageShadeOverlay from "./CoverageShadeOverlay";
import { classifyGaps } from "../lib/coverage";
import {
  resolveDragTarget, ghostTitle, HIT_THRESHOLD_PX, type DraggablePosition, type LineKind,
} from "../lib/sltpDrag";

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
  draggablePositions?: DraggablePosition[];
  onSlTpChange?: (positionId: number, change: { sl?: number; tp?: number }) => void;
  fitToRange?: { startMs: number; endMs: number };
  markers?: SeriesMarker<Time>[];
  missing?: [number, number][];
  shadeCoverage?: boolean;
  hideDate?: boolean;
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
  const sltpDragging = useRef<{ positionId: number; kind: LineKind; startPrice: number } | null>(null);
  const [sltpGhost, setSltpGhost] = useState<{ price: number; kind: "sl" | "tp" } | null>(null);
  const linesMeta = useRef<{ line: IPriceLine; positionId: number; kind: LineKind }[]>([]);
  const ghostLine = useRef<IPriceLine | null>(null);
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

  // Pixel y → nearest draggable line within HIT_THRESHOLD_PX, if any.
  const hitTestLine = useCallback((y: number): { positionId: number; kind: LineKind; price: number } | null => {
    const s = series.current;
    if (!s) return null;
    for (const meta of linesMeta.current) {
      const py = s.priceToCoordinate(meta.line.options().price);
      if (py !== null && Math.abs((py as number) - y) <= HIT_THRESHOLD_PX) {
        return { positionId: meta.positionId, kind: meta.kind, price: meta.line.options().price };
      }
    }
    return null;
  }, []);

  // Always restore pan/zoom and reset drag state, regardless of which path
  // ended the drag (pointerup, Escape, pointercancel, or auto-clear on data
  // identity change). Covers both the measure-gesture drag and an in-progress
  // SL/TP line drag — both suppress pan/zoom the same way in onDown.
  const endDrag = useCallback(() => {
    if (dragging.current || sltpDragging.current) {
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
        tickMarkFormatter: (t: number) => {
          const dt = wib((t as number) * 1000, 0).replace(" WIB", "");
          return props.hideDate ? dt.split(" ")[1] || dt : dt;
        },
      },
      localization: { 
        timeFormatter: (t: number) => {
          const dt = wib((t as number) * 1000, 0);
          return props.hideDate ? dt.split(" ")[1] + " WIB" || dt : dt;
        } 
      },
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
        const hit = hitTestLine(y);
        if (hit && hit.kind !== "entry" && cbs.current.onSlTpChange) {
          cbs.current.onSlTpChange(hit.positionId, hit.kind === "sl" ? { sl: 0 } : { tp: 0 });
          e.preventDefault();
          return;
        }
        const anchor = toPoint(x, y);
        if (!anchor) return;
        dragging.current = true;
        c.applyOptions({ handleScroll: false, handleScale: false });
        setMeasure((s) => measureReducer(s, { t: "start", anchor }));
        e.preventDefault();
      } else {
        const hit = hitTestLine(y);
        if (hit && cbs.current.onSlTpChange) {
          sltpDragging.current = { positionId: hit.positionId, kind: hit.kind, startPrice: hit.price };
          c.applyOptions({ handleScroll: false, handleScale: false });
          const pt = toPoint(x, y);
          if (pt) setSltpGhost({ price: pt.price, kind: hit.kind === "tp" ? "tp" : "sl" });
          e.preventDefault();
          return;
        }
        // A plain press clears any frozen measurement.
        setMeasure((s) => (s.phase === "frozen" ? measureReducer(s, { t: "clear" }) : s));
      }
    };

    const onMove = (e: PointerEvent) => {
      if (sltpDragging.current) {
        const { x, y } = rel(e);
        const pt = toPoint(x, y);
        if (!pt) return;
        const drag = sltpDragging.current;
        const pos = cbs.current.draggablePositions?.find((p) => p.id === drag.positionId);
        const kind = drag.kind === "entry" && pos ? resolveDragTarget(pos, pt.price)
          : (drag.kind as "sl" | "tp");
        setSltpGhost({ price: pt.price, kind });
        return;
      }
      if (!dragging.current) return;
      const { x, y } = rel(e);
      const cur = toPoint(x, y);
      if (cur) setMeasure((s) => measureReducer(s, { t: "move", cursor: cur }));
    };

    const onUp = (e: PointerEvent) => {
      const { x, y } = rel(e);
      lastUp.current = { ms: e.timeStamp, x, y };
      if (sltpDragging.current) {
        const drag = sltpDragging.current;
        // Restore pan/zoom before clearing sltpDragging — endDrag's guard
        // checks it (mirrors the measure-gesture's dragging.current guard).
        endDrag();
        sltpDragging.current = null;
        setSltpGhost(null);
        const pt = toPoint(x, y);
        const pos = cbs.current.draggablePositions?.find((p) => p.id === drag.positionId);
        // Skip the no-op case: a plain click (press+release with no real
        // movement) must not fire a "change" to the same value it already
        // had — same float-tolerance convention as rule 5 elsewhere.
        if (pt && cbs.current.onSlTpChange && Math.abs(pt.price - drag.startPrice) > 1e-9) {
          const target = drag.kind === "entry" && pos ? resolveDragTarget(pos, pt.price)
            : (drag.kind as "sl" | "tp");
          cbs.current.onSlTpChange(drag.positionId, { [target]: pt.price } as { sl?: number; tp?: number });
        }
        return;
      }
      if (dragging.current) {
        endDrag();
        setMeasure((s) => measureReducer(s, { t: "release" }));
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        endDrag();
        sltpDragging.current = null;
        setSltpGhost(null);
        setMeasure((s) => measureReducer(s, { t: "clear" }));
      }
    };

    const onCancel = () => {
      endDrag();
      sltpDragging.current = null;
      setSltpGhost(null);
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
  }, [toPoint, endDrag, hitTestLine]);

  // Data identity changed → the stored data coordinates may no longer line up.
  useEffect(() => {
    endDrag();
    sltpDragging.current = null;
    setSltpGhost(null);
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

  const lastFittedTrade = useRef<{ startMs: number; endMs: number; fullyFitted: boolean } | null>(null);

  // Smart fit auto-focus
  useEffect(() => {
    if (!chart.current || !series.current || !props.fitToRange || props.candles.length === 0) return;
    const { startMs, endMs } = props.fitToRange;

    const prev = lastFittedTrade.current;
    if (prev && prev.startMs === startMs && prev.endMs === endMs && prev.fullyFitted) {
      return;
    }

    // Find the logical index (array index) of the start and end bars
    let startIndex = props.candles.findIndex(c => c.time_msc >= startMs);
    if (startIndex === -1) startIndex = props.candles.length - 1;

    let endIndex = props.candles.findIndex(c => c.time_msc >= endMs);
    const fullyFitted = endIndex !== -1;
    if (endIndex === -1) endIndex = props.candles.length - 1;

    // Pad context: 10 bars before entry, 5 bars after exit
    const paddedStart = Math.max(0, startIndex - 10);
    let paddedEnd = Math.min(props.candles.length - 1, endIndex + 5);

    // Enforce 100 bars max zoom-out limit to prevent unreadable thin candles
    if (paddedEnd - paddedStart > 100) {
      paddedEnd = paddedStart + 100;
    }

    // Defensive guard for malformed range data (e.g. startMs > endMs)
    if (paddedStart > paddedEnd) return;

    // Apply logical range
    chart.current.timeScale().setVisibleLogicalRange({
      from: paddedStart,
      to: paddedEnd,
    });

    lastFittedTrade.current = { startMs, endMs, fullyFitted };
  }, [props.fitToRange, props.candles.length, props.settings.chartType]);

  // SL/TP/entry overlay lines. Three mutually-exclusive sources, in priority
  // order: (1) draggablePositions (replay, or any caller building its own
  // position list) — draws draggable entry/SL/TP lines and records them in
  // linesMeta for hit-testing; (2) overlayLines — explicit, non-draggable
  // lines (older replay callers, static views); (3) live positions for the
  // current symbol, only when "now" is in view (horizontal lines have no
  // time, so they'd otherwise hang over history where those levels never
  // existed) — these are ALSO recorded in linesMeta, so live positions become
  // draggable automatically whenever onSlTpChange is passed, with no need for
  // the caller to separately build a draggablePositions array for the live case.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];
    linesMeta.current = [];

    const addLine = (positionId: number, kind: LineKind, price: number | null,
                     color: string, title: string) => {
      if (price === null || price === undefined || Math.abs(price) < 1e-9) return;
      const line = s.createPriceLine({
        price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title,
      });
      priceLines.current.push(line);
      linesMeta.current.push({ line, positionId, kind });
    };

    if (props.draggablePositions !== undefined) {
      for (const pos of props.draggablePositions) {
        addLine(pos.id, "entry", pos.entry_price, LINE_COLORS.entry, `entry #${pos.id}`);
        addLine(pos.id, "sl", pos.sl, LINE_COLORS.sl, `SL #${pos.id}`);
        addLine(pos.id, "tp", pos.tp, LINE_COLORS.tp, `TP #${pos.id}`);
      }
      return;
    }

    // Replay (or any caller) supplies explicit lines → draw exactly those.
    const explicit = props.overlayLines;
    if (explicit !== undefined) {
      for (const line of explicit) {
        priceLines.current.push(s.createPriceLine({
          price: line.price, color: line.color, lineWidth: 1,
          lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: line.title,
        }));
      }
      return;
    }

    // Live SL/TP/entry overlay — only when the current symbol has open positions
    // AND "now" is in view (horizontal lines have no time).
    if (!props.settings.liveOverlay || !props.nowVisible || !props.live || props.live.live.empty) return;
    const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
    for (const pos of mine) {
      addLine(pos.position_id, "entry", pos.open_price, LINE_COLORS.entry, `entry #${pos.position_id}`);
      addLine(pos.position_id, "sl", pos.sl, LINE_COLORS.sl, `SL #${pos.position_id}`);
      addLine(pos.position_id, "tp", pos.tp, LINE_COLORS.tp, `TP #${pos.position_id}`);
    }
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay,
      props.settings.chartType, props.overlayLines, props.draggablePositions]);

  // Ghost line preview while a SL/TP drag is in progress — shows the
  // to-be-committed value at the cursor's projected price, styled distinctly
  // from the underlying (still-uncommitted) line via a translucent color.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (ghostLine.current) { s.removePriceLine(ghostLine.current); ghostLine.current = null; }
    if (sltpGhost) {
      const drag = sltpDragging.current;
      const pos = drag && cbs.current.draggablePositions?.find((p) => p.id === drag.positionId);
      const entryFallback = pos?.entry_price ?? null;
      const title = ghostTitle(sltpGhost.kind, entryFallback, sltpGhost.price);
      const color = (sltpGhost.kind === "tp" ? LINE_COLORS.tp : LINE_COLORS.sl) + "80";
      ghostLine.current = s.createPriceLine({
        price: sltpGhost.price, color, lineWidth: 2, lineStyle: LineStyle.Solid,
        axisLabelVisible: true, title,
      });
    }
  }, [sltpGhost]);

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

  // Coverage shading (unfetched/closed bands), normal-mode only (gated by the
  // caller via shadeCoverage — replay passes neither prop). Time→x projection
  // mirrors `project()` above; re-runs on every re-render, including the
  // bumpProjection-forced ones from visible-range change and resize.
  let shadeOverlay: JSX.Element | null = null;
  if (props.shadeCoverage && props.candles.length > 0) {
    const projectMs = (ms: number): number | null => {
      const c = chart.current;
      if (!c) return null;
      const x = c.timeScale().timeToCoordinate((ms / 1000) as UTCTimestamp);
      return x === null ? null : (x as number);
    };
    const winRange: [number, number] = [
      props.candles[0].time_msc, props.candles[props.candles.length - 1].time_msc,
    ];
    const segments = classifyGaps(props.candles, props.missing ?? [], winRange, props.tf);
    const chartHeight = el.current?.clientHeight ?? 0;
    if (chartHeight > 0) {
      shadeOverlay = <CoverageShadeOverlay segments={segments} project={projectMs} height={chartHeight} />;
    }
  }

  return (
    <div className="relative w-full h-full">
      <div ref={el} className="w-full h-full" />
      {overlay}
      {shadeOverlay}
    </div>
  );
});

export default CandleChart;
