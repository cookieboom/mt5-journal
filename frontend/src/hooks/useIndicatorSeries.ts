// Draws indicator results onto an existing lightweight-charts instance: one
// series per plot, overlays on pane 0, every other pane name in its own pane
// below. Lives beside CandleChart, not inside its effects: indicator series are
// independent of the main series, so a chart-type switch (which recreates the
// main series) never touches them.
import { useEffect, useRef, type MutableRefObject } from "react";
import {
  HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from "lightweight-charts";
import {
  paneAssignments, plotColor, toLineData, type IndicatorResult,
} from "../lib/indicators";
import { palette, tint } from "../lib/theme";

export interface IndicatorRender { id: string; result: IndicatorResult }

// Price pane keeps most of the height however many indicator panes stack up.
const PRICE_STRETCH = 3;

type AnySeries = ISeriesApi<"Line"> | ISeriesApi<"Histogram">;

function shapeOf(items: IndicatorRender[]): string {
  return JSON.stringify(items.map((i) => [
    i.id,
    i.result.plots.map((p) => [p.pane, p.style, p.color, p.width, p.title]),
    i.result.hlines.map((h) => [h.value, h.pane, h.color, h.title]),
  ]));
}

export function useIndicatorSeries(
  chart: MutableRefObject<IChartApi | null>, items: IndicatorRender[] | undefined,
): void {
  const series = useRef<Map<string, AnySeries>>(new Map());
  const shape = useRef("");

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const list = items ?? [];
    const next = shapeOf(list);
    if (next !== shape.current) {
      for (const s of series.current.values()) c.removeSeries(s);
      series.current.clear();
      while (c.panes().length > 1) c.removePane(c.panes().length - 1);

      const panes = paneAssignments(list.map((i) => ({ id: i.id, plots: i.result.plots.map((p) => p.pane) })));
      for (const it of list) {
        const firstInPane = new Map<string, AnySeries>();
        it.result.plots.forEach((p, k) => {
          const pane = panes[`${it.id}:${p.pane}`];
          const color = plotColor(p.color);
          const common = { title: p.title, priceLineVisible: false, lastValueVisible: true };
          const s: AnySeries = p.style === "histogram"
            ? c.addSeries(HistogramSeries, { ...common, color }, pane)
            : c.addSeries(LineSeries, {
              ...common, color, lineWidth: p.width as 1 | 2 | 3 | 4,
              lineVisible: p.style !== "dots", pointMarkersVisible: p.style === "dots",
              crosshairMarkerVisible: false,
            }, pane);
          series.current.set(`${it.id}:${k}`, s);
          if (!firstInPane.has(p.pane)) firstInPane.set(p.pane, s);
        });
        for (const h of it.result.hlines) {
          firstInPane.get(h.pane)?.createPriceLine({
            price: h.value, color: plotColor(h.color), lineWidth: 1,
            lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: h.title,
          });
        }
      }
      const all = c.panes();
      all.forEach((p, i) => p.setStretchFactor(i === 0 ? PRICE_STRETCH : 1));
      shape.current = next;
    }

    for (const it of list) {
      it.result.plots.forEach((p, k) => {
        const s = series.current.get(`${it.id}:${k}`);
        if (!s) return;
        if (p.style === "histogram") {
          s.setData(it.result.times.map((t, i) => {
            const time = Math.floor(t / 1000) as UTCTimestamp;
            const v = p.values[i];
            return v === null ? { time } : { time, value: v, color: tint(v >= 0 ? palette.pos : palette.neg, 0.6) };
          }));
        } else {
          s.setData(toLineData(it.result.times, p.values));
        }
      });
    }
  }, [chart, items]);

  // The chart itself is torn down by CandleChart; only forget our handles.
  useEffect(() => () => { series.current.clear(); shape.current = ""; }, []);
}
