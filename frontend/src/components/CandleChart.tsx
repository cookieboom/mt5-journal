import {
  forwardRef, useEffect, useImperativeHandle, useRef, useState, useCallback, useReducer,
} from "react";
import {
  createChart, CandlestickSeries, BarSeries, LineSeries, AreaSeries,
  ColorType, CrosshairMode, PriceScaleMode, LineStyle, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp, type SeriesType,
  type SeriesMarker, type Time,
} from "lightweight-charts";
import {
  toSeconds, isNowVisible, LINE_COLORS, liveLines, barCloseCountdown, timeframeMs,
  axisTickLabel, type Sym, type Timeframe,
} from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import type { Candle, HoverBar, LiveData, PlannedOrder } from "../lib/types";
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
  resolveDragTarget, ghostTitle, plannedTitle, positionTitle, HIT_THRESHOLD_PX, PLANNED_ID, type DraggablePosition, type LineKind,
} from "../lib/sltpDrag";
import DrawingOverlay from "./DrawingOverlay";
import DrawingPalette from "./DrawingPalette";
import TextDrawingInput from "./TextDrawingInput";
import { useDrawingGesture } from "../hooks/useDrawingGesture";
import {
  anchorToX, hitTest, moveDrawing, projectDrawing, timeAtLogical,
  type Anchor, type Drawing, type Projected, type Tool,
} from "../lib/drawings";
import { chartDark, chartLight } from "../lib/theme";

const DARK = chartDark;
const LIGHT = chartLight;

const CROSSHAIR = {
  normal: CrosshairMode.Normal, magnet: CrosshairMode.Magnet, hidden: CrosshairMode.Hidden,
} as const;

// A pane that hasn't laid out yet (pre-paint, or a ResizeObserver-less test
// DOM) reports clientWidth 0 — never a genuine zero-width chart — so an
// hline's projected span falls back to this instead of collapsing its
// hit-test segment to a point.
const FALLBACK_PANE_WIDTH_PX = 2000;

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
  plannedOrder?: PlannedOrder | null;
  countdown?: boolean;   // live only: bar-close timer rides the price marker
  fitToRange?: { startMs: number; endMs: number };
  markers?: SeriesMarker<Time>[];
  missing?: [number, number][];
  shadeCoverage?: boolean;
  hideDate?: boolean;
  drawings?: {
    items: Drawing[];
    editable: boolean;
    onAdd: (d: Drawing) => void;
    onUpdate: (d: Drawing) => void;
    onDelete: (id: string) => void;
    onClearAll: () => void;
  };
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
  const sltpDragging = useRef<{
    positionId: number; kind: LineKind; startPrice: number;
    direction: "buy" | "sell"; entryPrice: number | null;
  } | null>(null);
  const [sltpGhost, setSltpGhost] = useState<{ price: number; kind: "sl" | "tp" } | null>(null);
  const [tool, setTool] = useState<Tool>("cursor");
  const toolRef = useRef<Tool>("cursor");
  toolRef.current = tool;
  const projectedRef = useRef<Projected[]>([]);
  // Open text editor: either a fresh label being placed, or an existing one
  // being re-edited (`id` set).
  const [textEdit, setTextEdit] = useState<
    { id: string | null; anchor: Anchor; px: { x: number; y: number }; initial: string } | null
  >(null);
  const linesMeta = useRef<{
    line: IPriceLine; positionId: number; kind: LineKind;
    direction: "buy" | "sell"; entryPrice: number | null;
  }[]>([]);
  const ghostLine = useRef<IPriceLine | null>(null);
  const [, bumpProjection] = useReducer((c: number) => c + 1, 0);
  // Pane size changes are invisible to React (autoSize:true), but a fit applied
  // to a pane that has not been laid out yet does not survive the resize that
  // follows. Counted separately from bumpProjection so the auto-fit re-runs on
  // a resize only, never on the visible-range changes a pan emits.
  const [sizeTick, bumpSize] = useReducer((c: number) => c + 1, 0);

  // Bar-close countdown (live only). It rides the planned-order entry line —
  // which sits exactly at the last close by construction (Chart.tsx derives it
  // from the shown bars) — as that line's title, so the chart keeps ONE price
  // marker instead of two: the line's own axis label is suppressed below and
  // the series' built-in last-price label carries the number.
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!props.countdown) return;
    const id = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [props.countdown]);
  const entryTitle = props.countdown ? barCloseCountdown(tick, props.tf) : "harga";

  // Pointer pixel (relative to the pane) → data coordinates, using the current
  // series/timeScale. candles give a gap-aware bar time from the logical index;
  // right of the last bar timeAtLogical keeps counting in whole timeframes, so
  // the empty space there is addressable instead of collapsing onto that bar.
  const toPoint = useCallback((px: number, py: number): Point | null => {
    const c = chart.current, s = series.current;
    if (!c || !s) return null;
    const price = s.coordinateToPrice(py);
    const logical = c.timeScale().coordinateToLogical(px);
    if (price === null || logical === null) return null;
    const barTimeMs = timeAtLogical(
      cbs.current.candles, logical as number, timeframeMs(cbs.current.tf),
    );
    return { price: price as number, logical: logical as number, barTimeMs };
  }, []);

  // Pixel y → nearest draggable line within HIT_THRESHOLD_PX, if any. Carries
  // the source position's direction/entryPrice captured at draw time (from
  // linesMeta) — NOT looked up from draggablePositions, which is undefined
  // by construction on the live-fallback path. This is what lets an
  // entry-line drag resolve to sl/tp correctly on both paths.
  const hitTestLine = useCallback((y: number): {
    positionId: number; kind: LineKind; price: number;
    direction: "buy" | "sell"; entryPrice: number | null;
  } | null => {
    const s = series.current;
    if (!s) return null;
    let best: {
      positionId: number; kind: LineKind; price: number;
      direction: "buy" | "sell"; entryPrice: number | null;
    } | null = null;
    let bestDist = Infinity;
    for (const meta of linesMeta.current) {
      const py = s.priceToCoordinate(meta.line.options().price);
      if (py === null) continue;
      const dist = Math.abs((py as number) - y);
      if (dist <= HIT_THRESHOLD_PX && dist < bestDist) {
        bestDist = dist;
        best = {
          positionId: meta.positionId, kind: meta.kind, price: meta.line.options().price,
          direction: meta.direction, entryPrice: meta.entryPrice,
        };
      }
    }
    return best;
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
        // Axis labels in WIB (server=UTC, +7h; display only). The date rides
        // only on the ticks that start a day — see `axisTickLabel`.
        tickMarkFormatter: (t: number, tickMarkType: number) =>
          axisTickLabel((t as number) * 1000, tickMarkType, props.hideDate),
      },
      localization: {
        // The crosshair label has room the axis does not, so it keeps the full
        // stamp — minus the date when competitive replay is hiding the period.
        timeFormatter: (t: number) => {
          const dt = wib((t as number) * 1000, 0);
          return props.hideDate ? `${dt.split(" ")[1]} WIB` : dt;
        },
      },
    });
    const s = addSeriesFor(c, props.settings);
    s.applyOptions({ priceLineVisible: props.settings.lastPriceLine });
    chart.current = c;
    series.current = s;
    bumpProjection();   // refs just set: re-render so drawing projections use them

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
      linesMeta.current = [];
      ghostLine.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // autoSize:true resize doesn't always trigger a React re-render, so a frozen
  // measurement overlay (projected from chart coordinates) can lag the pane.
  // Force a re-render on resize so `project()` re-runs against fresh coords.
  useEffect(() => {
    const node = el.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { bumpSize(); bumpProjection(); });
    ro.observe(node);
    bumpProjection();   // el.current is now set: re-render so gesture listeners attach
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

    // Entry-line drag resolves to sl/tp by direction — except a planned order
    // with no side chosen yet (PLANNED_ID, direction still null), where any
    // entry-line drag always means "sl": that's the gesture that decides the
    // side in the first place, so there's nothing to resolve by yet. Shared
    // by the ghost preview (onMove) and the committed change (onUp) so they
    // never disagree.
    const resolveEntryDragKind = (
      positionId: number, direction: "buy" | "sell", entryPrice: number | null, price: number,
    ): "sl" | "tp" =>
      positionId === PLANNED_ID && cbs.current.plannedOrder?.direction == null
        ? "sl"
        : resolveDragTarget({ id: positionId, direction, entry_price: entryPrice, sl: 0, tp: 0 }, price);

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
          sltpDragging.current = {
            positionId: hit.positionId, kind: hit.kind, startPrice: hit.price,
            direction: hit.direction, entryPrice: hit.entryPrice,
          };
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
        const kind = drag.kind === "entry"
          ? resolveEntryDragKind(drag.positionId, drag.direction, drag.entryPrice, pt.price)
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
        // Skip the no-op case: a plain click (press+release with no real
        // movement) must not fire a "change" to the same value it already
        // had — same float-tolerance convention as rule 5 elsewhere.
        if (pt && cbs.current.onSlTpChange && Math.abs(pt.price - drag.startPrice) > 1e-9) {
          const target = drag.kind === "entry"
            ? resolveEntryDragKind(drag.positionId, drag.direction, drag.entryPrice, pt.price)
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

    // A drag/wheel on the right price axis rescales priceToCoordinate without
    // touching the logical range, so it fires neither
    // subscribeVisibleLogicalRangeChange nor the ResizeObserver above — every
    // drawing's projected y goes stale until something else happens to bump
    // it. The axis renders inside `node`, so its own pointerup/wheel reach
    // this listener same as everything else here.
    node.addEventListener("pointerup", bumpProjection);
    node.addEventListener("wheel", bumpProjection, { passive: true });

    node.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointercancel", onCancel);
    return () => {
      node.removeEventListener("pointerup", bumpProjection);
      node.removeEventListener("wheel", bumpProjection);
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
    linesMeta.current = [];
    ghostLine.current = null;
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

  const lastFittedTrade = useRef<{ startMs: number; endMs: number } | null>(null);
  // Set by the first pan/zoom gesture on the pane. Until then the auto-fit owns
  // the viewport; after it, the user does and the fit never moves it again.
  const userMovedView = useRef(false);

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const grab = () => { userMovedView.current = true; };
    node.addEventListener("pointerdown", grab);
    node.addEventListener("wheel", grab, { passive: true });
    return () => {
      node.removeEventListener("pointerdown", grab);
      node.removeEventListener("wheel", grab);
    };
  }, []);

  // Smart fit auto-focus. Re-asserted on EVERY data change, not once: the
  // viewer anchors its first fetch at the entry and forward-loads past the exit
  // afterwards, and lightweight-charts keeps the pane pinned to the right edge
  // of the data — so those late bars scroll the trade off screen. Fitting once
  // and latching left the reader panning back to find their own trade.
  // A pan/zoom (userMovedView) hands the viewport over for good, so a pan's own
  // loadOlder can't yank it back; a different trade takes it back.
  useEffect(() => {
    if (!chart.current || !series.current || !props.fitToRange || props.candles.length === 0) return;
    const { startMs, endMs } = props.fitToRange;

    const prev = lastFittedTrade.current;
    const sameTrade = prev !== null && prev.startMs === startMs && prev.endMs === endMs;
    if (!sameTrade) userMovedView.current = false;
    else if (userMovedView.current) return;

    // Find the logical index (array index) of the start and end bars
    let startIndex = props.candles.findIndex(c => c.time_msc >= startMs);
    if (startIndex === -1) startIndex = props.candles.length - 1;

    let endIndex = props.candles.findIndex(c => c.time_msc >= endMs);
    if (endIndex === -1) endIndex = props.candles.length - 1;

    // Defensive guard for malformed range data (e.g. startMs > endMs)
    if (startIndex > endIndex) return;

    // Pad context symmetrically so the trade sits in the middle of the pane,
    // not hard against the right edge.
    const paddedStart = Math.max(0, startIndex - 10);
    let paddedEnd = Math.min(props.candles.length - 1, endIndex + 10);

    // Enforce 100 bars max zoom-out limit to prevent unreadable thin candles
    if (paddedEnd - paddedStart > 100) {
      paddedEnd = paddedStart + 100;
    }

    // Apply logical range
    chart.current.timeScale().setVisibleLogicalRange({
      from: paddedStart,
      to: paddedEnd,
    });

    lastFittedTrade.current = { startMs, endMs };
    // Keyed on the candle ARRAY, not its length: every setData snaps the pane
    // back to the right edge of the data, and the fill-poll cycle re-merges the
    // same bars into a fresh array several times after the window is complete.
    // Keyed on length, those identical-length re-merges scrolled the trade away
    // with no re-fit behind them.
  }, [props.fitToRange, props.candles, props.settings.chartType, sizeTick]);

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

    // A planned order's entry line sits on the last close and keeps its own
    // axis label (see below), so the series' built-in last-value badge would
    // be a duplicate at the same price. Hide it exactly while that line exists.
    const plannedEntry = props.plannedOrder?.entry ?? null;
    s.applyOptions({
      lastValueVisible: plannedEntry === null || Math.abs(plannedEntry) < 1e-9,
    });

    const addLine = (positionId: number, kind: LineKind, price: number | null,
                     color: string, title: string, direction: "buy" | "sell",
                     entryPrice: number | null) => {
      if (price === null || price === undefined || Math.abs(price) < 1e-9) return;
      const line = s.createPriceLine({
        price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title,
      });
      priceLines.current.push(line);
      linesMeta.current.push({ line, positionId, kind, direction, entryPrice });
    };

    // Position lines first — the three sources below are mutually exclusive and
    // each returns early, so this stays a function rather than growing a flag.
    const drawPositions = () => {
      if (props.draggablePositions !== undefined) {
        for (const pos of props.draggablePositions) {
          addLine(pos.id, "entry", pos.entry_price, LINE_COLORS.entry,
                  positionTitle("entry", pos.entry_price ?? 0, pos.entry_price), pos.direction, pos.entry_price);
          addLine(pos.id, "sl", pos.sl, LINE_COLORS.sl,
                  positionTitle("sl", pos.sl, pos.entry_price), pos.direction, pos.entry_price);
          addLine(pos.id, "tp", pos.tp, LINE_COLORS.tp,
                  positionTitle("tp", pos.tp, pos.entry_price), pos.direction, pos.entry_price);
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
        for (const line of liveLines(pos)) {
          addLine(pos.position_id, line.kind, line.price, line.color, line.title, pos.direction, pos.open_price);
        }
      }
    };
    drawPositions();
    // Whether the chart is now showing a position at all — any of the three
    // sources above, live or replay. Read off the lines themselves rather than
    // re-testing each source's conditions, so a fourth source can never drift
    // out of step with this.
    const hasPositionLines = priceLines.current.length > 0;

    // The planned order draws LAST, on top of whatever else the chart is
    // showing: it is not a position yet, so it belongs to none of the sources
    // above. Order matters because a plan and a position can share a price, and
    // lightweight-charts paints axis labels in creation order — drawn first, the
    // plan's label would be buried under the position's.
    // `direction` is null until the human's stop picks a side; an entry-line
    // drag then resolves to "sl" by default, which is exactly the gesture that
    // decides it.
    if (props.plannedOrder) {
      const p = props.plannedOrder;
      const dir = p.direction ?? "buy";
      // This line sits ON the last close, so its axis label would stack a
      // second price badge against the series' own last-price label. The badge
      // has to stay, though: lightweight-charts paints a price line's title
      // from its price-axis view, which returns early when axisLabelVisible is
      // false — killing the label kills the title (countdown in live, "harga"
      // otherwise) with it. So keep this line's label and drop the series' own:
      // same one badge on the scale, and the title paints.
      addLine(PLANNED_ID, "entry", p.entry, LINE_COLORS.entry, entryTitle, dir, p.entry);
      // The planned stops stop drawing the moment a position is on the chart:
      // the plan has been acted on, and the levels that now govern real money
      // are the position's own. The entry line above is exempt — it is not a
      // plan but where price is right now, and it carries the countdown.
      // p.entry IS that price (Chart.tsx derives it from the last shown close),
      // so it doubles as the reference for the distance in the titles.
      if (!hasPositionLines) {
        if (p.sl !== null) addLine(PLANNED_ID, "sl", p.sl, LINE_COLORS.sl, plannedTitle("sl", p.sl, p.entry), dir, p.entry);
        if (p.tp !== null) addLine(PLANNED_ID, "tp", p.tp, LINE_COLORS.tp, plannedTitle("tp", p.tp, p.entry), dir, p.entry);
      }
    }
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay,
      props.settings.chartType, props.overlayLines, props.draggablePositions, props.plannedOrder]);

  // Tick the countdown in place. Deliberately NOT a dep of the effect above:
  // that one tears down and rebuilds every price line, which at 1 Hz would
  // churn linesMeta under an in-flight drag. applyOptions just repaints the
  // title. Re-runs on plannedOrder too, since that rebuild drops the old line.
  useEffect(() => {
    const meta = linesMeta.current.find(
      (m) => m.positionId === PLANNED_ID && m.kind === "entry",
    );
    meta?.line.applyOptions({ title: entryTitle });
  }, [entryTitle, props.plannedOrder]);

  // Ghost line preview while a SL/TP drag is in progress — shows the
  // to-be-committed value at the cursor's projected price, styled distinctly
  // from the underlying (still-uncommitted) line via a translucent color.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (ghostLine.current) { s.removePriceLine(ghostLine.current); ghostLine.current = null; }
    if (sltpGhost) {
      const drag = sltpDragging.current;
      const entryFallback = drag?.entryPrice ?? null;
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
    // Reads chart.current at CALL time (not closure-capture time), so this
    // stays correct across pan/zoom/resize without needing its own deps.
    timeToX: (timeMsc: number) => {
      const c = chart.current;
      if (!c) return null;
      const x = c.timeScale().timeToCoordinate((timeMsc / 1000) as UTCTimestamp);
      return x === null ? null : (x as number);
    },
  }));

  const theme = props.settings.theme === "light" ? LIGHT : DARK;

  // Drawing projection context (anchorToX inside projectDrawing snaps to the
  // containing bar, so a level drawn on M15 still renders on H1).
  const drawings = props.drawings;
  const drawCtx = {
    width: el.current?.clientWidth || FALLBACK_PANE_WIDTH_PX,
    candles: props.candles,
    tfMs: timeframeMs(props.tf),
    logicalToX: (i: number) => {
      const x = chart.current?.timeScale().logicalToCoordinate(i as never);
      return x === null || x === undefined ? null : (x as number);
    },
    priceToY: (p: number) => {
      const y = series.current?.priceToCoordinate(p);
      return y === null || y === undefined ? null : (y as number);
    },
  };
  // Measure anchors go through the SAME projection as drawings: timeToCoordinate
  // resolves only exact bar times, so a measurement whose anchor sits right of
  // the last bar (where toPoint now extrapolates) would project to null and the
  // overlay would silently vanish.
  const project = (p: Point): ProjectedPoint | null => {
    const x = anchorToX(p.barTimeMs, drawCtx.candles, drawCtx.logicalToX, drawCtx.tfMs);
    const y = drawCtx.priceToY(p.price);
    return x === null || y === null ? null : { x, y };
  };

  const projectedDrawings: Projected[] = drawings
    ? drawings.items.map((d) => projectDrawing(d, drawCtx))
    : [];
  projectedRef.current = projectedDrawings;

  const toAnchor = useCallback((x: number, y: number): Anchor | null => {
    const p = toPoint(x, y);
    return p === null ? null : { timeMs: p.barTimeMs, price: p.price };
  }, [toPoint]);

  const gesture = useDrawingGesture({
    node: el.current,
    // The text tool never enters the draw reducer's drag path — a single
    // press just opens the inline editor below, handled by its own effect —
    // so the hook is disabled outright while the text tool is armed. Masking
    // `tool` to "cursor" instead (so the hook still ran) let its hit-test
    // branch silently grab/select whatever drawing sat under the press, which
    // then became the Delete key's target instead of the label just typed.
    // `tool` snaps back to "cursor" the instant the press lands (see the text
    // effect below), so this disables the hook only for the brief moment
    // between arming the tool and the next press.
    enabled: !!drawings?.editable && tool !== "text",
    tool,
    items: drawings?.items ?? [],
    projected: projectedDrawings,
    toAnchor,
    // An SL/TP line and the measure double-click-hold both outrank a drawing.
    reserved: (_x, y, e) =>
      hitTestLine(y) !== null
      || (lastUp.current !== null
          && isDoubleClickHold(lastUp.current.ms, lastUp.current.x, lastUp.current.y,
                               e.timeStamp, _x, y)),
    onAdd: (d) => drawings?.onAdd(d),
    onUpdate: (d) => drawings?.onUpdate(d),
    onDelete: (id) => drawings?.onDelete(id),
    onToolDone: () => setTool("cursor"),
    suppressPan: (off) => chart.current?.applyOptions({ handleScroll: !off, handleScale: !off }),
    // See useDrawingGesture's onUp: a completed draw/drag must not leave its
    // release point sitting in lastUp, or the next nearby press reads as the
    // second half of a measure double-click instead of a re-grab.
    clearMeasureSeed: () => { lastUp.current = null; },
  });

  // Text tool: a single press places the anchor and opens the inline editor —
  // there is nothing to drag, so this never enters the draw reducer. A
  // double-click on an existing label reopens it. Both paths are capture-phase
  // so they land before the measure gesture, which needs a HOLD and therefore
  // never competes for a plain click.
  useEffect(() => {
    const node = el.current;
    if (!node || !props.drawings?.editable) return;

    const rel = (e: { clientX: number; clientY: number }) => {
      const r = node.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      if (toolRef.current !== "text") return;
      const { x, y } = rel(e);
      const at = toAnchor(x, y);
      if (!at) return;
      setTextEdit({ id: null, anchor: at, px: { x, y }, initial: "" });
      setTool("cursor");
      e.preventDefault();
      e.stopPropagation();
    };

    const onDouble = (e: MouseEvent) => {
      const { x, y } = rel(e);
      const hit = hitTest(projectedRef.current, { x, y });
      if (!hit) return;
      const target = cbs.current.drawings?.items.find((d) => d.id === hit.id);
      if (!target || target.kind !== "text") return;
      setTextEdit({ id: target.id, anchor: target.a, px: { x, y }, initial: target.text });
      e.preventDefault();
      e.stopPropagation();
    };

    node.addEventListener("pointerdown", onDown, true);
    node.addEventListener("dblclick", onDouble, true);
    return () => {
      node.removeEventListener("pointerdown", onDown, true);
      node.removeEventListener("dblclick", onDouble, true);
    };
  }, [props.drawings?.editable, toAnchor]);

  // Live preview: in-progress draft, or the object under an active drag —
  // neither is committed until pointerup.
  let previewProjected = projectedDrawings;
  if (gesture.draft) {
    previewProjected = [...projectedDrawings, projectDrawing(gesture.draft, drawCtx)];
  } else {
    const gs = gesture.state;
    if (gs.phase === "dragging" && gs.at && drawings) {
      const target = drawings.items.find((d) => d.id === gs.id);
      if (target) {
        const moved = moveDrawing(target, gs.handle, gs.from, gs.at);
        previewProjected = projectedDrawings.map((p) =>
          p.d.id === gs.id ? projectDrawing(moved, drawCtx) : p);
      }
    }
  }

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
      {drawings && (
        <DrawingOverlay projected={previewProjected} selectedId={gesture.selectedId} />
      )}
      {drawings?.editable && (
        <DrawingPalette
          tool={tool}
          onTool={setTool}
          onClearAll={drawings.onClearAll}
          count={drawings.items.length}
        />
      )}
      {drawings?.editable && textEdit && (
        <TextDrawingInput
          x={textEdit.px.x}
          y={textEdit.px.y}
          initial={textEdit.initial}
          onCommit={(text) => {
            const edit = textEdit;
            setTextEdit(null);
            if (text.length === 0) return;      // blank label = discarded
            if (edit.id === null) {
              drawings.onAdd({ id: crypto.randomUUID(), kind: "text", a: edit.anchor, text });
            } else {
              const target = drawings.items.find((d) => d.id === edit.id);
              if (target && target.kind === "text") drawings.onUpdate({ ...target, text });
            }
          }}
          onCancel={() => setTextEdit(null)}
        />
      )}
    </div>
  );
});

export default CandleChart;
