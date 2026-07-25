import {
  forwardRef, useEffect, useImperativeHandle, useRef,
} from "react";
import {
  createChart, CandlestickSeries, ColorType, CrosshairMode,
  type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp,
} from "lightweight-charts";
import { toSeconds, liveLines, type Sym, type Timeframe } from "../lib/candles";
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
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
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
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: theme.border },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
        // Axis labels in WIB (server=UTC, +7h; display only).
        tickMarkFormatter: (t: number) => wib((t as number) * 1000, 0).replace(" WIB", ""),
      },
      localization: { timeFormatter: (t: number) => wib((t as number) * 1000, 0) },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: theme.up, downColor: theme.down,
      wickUpColor: theme.up, wickDownColor: theme.down, borderVisible: false,
    });
    chart.current = c;
    series.current = s;

    c.subscribeCrosshairMove((param) => {
      const bar = param.seriesData.get(s) as
        | { open: number; high: number; low: number; close: number } | undefined;
      if (!bar || param.time === undefined) { cbs.current.onHover(null); return; }
      cbs.current.onHover({
        time_msc: (param.time as number) * 1000,
        o: bar.open, h: bar.high, l: bar.low, c: bar.close, v: 0,
      });
    });

    c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || !series.current) return;
      const bars = series.current.barsInLogicalRange(range);
      if (bars && bars.barsBefore < 20) cbs.current.onRequestOlder();
      const vis = c.timeScale().getVisibleRange();
      const toMs = vis ? (vis.to as number) * 1000 : null;
      const last = cbs.current.lastBarMs;
      cbs.current.onNowVisibleChange(
        last !== null && toMs !== null && toMs >= last - 60_000,
      );
    });

    return () => { c.remove(); chart.current = null; series.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply theme/grid when settings change (no full re-create).
  useEffect(() => {
    if (!chart.current || !series.current) return;
    const theme = props.settings.theme === "light" ? LIGHT : DARK;
    chart.current.applyOptions({
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: props.settings.grid },
        horzLines: { color: theme.grid, visible: props.settings.grid },
      },
    });
    series.current.applyOptions({
      upColor: theme.up, downColor: theme.down, wickUpColor: theme.up, wickDownColor: theme.down,
    });
  }, [props.settings]);

  // Push candle data.
  useEffect(() => {
    if (!series.current) return;
    series.current.setData(
      props.candles.map((c) => ({
        time: toSeconds(c.time_msc) as UTCTimestamp,
        open: c.o, high: c.h, low: c.l, close: c.c,
      })),
    );
  }, [props.candles]);

  // Live SL/TP/entry overlay — only when the current symbol has open positions
  // AND "now" is in view. Horizontal lines have no time, so they'd otherwise
  // hang over history where those levels never existed.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];
    if (!props.nowVisible || !props.live || props.live.live.empty) return;
    const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
    for (const pos of mine) {
      for (const line of liveLines(pos)) {
        priceLines.current.push(
          s.createPriceLine({
            price: line.price,
            color: line.color,
            lineWidth: 1,
            lineStyle: 2,           // dashed
            axisLabelVisible: true,
            title: line.title,
          }),
        );
      }
    }
  }, [props.live, props.nowVisible, props.symbol]);

  useImperativeHandle(ref, () => ({
    jumpToNow: () => chart.current?.timeScale().scrollToRealTime(),
  }));

  return <div ref={el} className="w-full h-full" />;
});

export default CandleChart;
