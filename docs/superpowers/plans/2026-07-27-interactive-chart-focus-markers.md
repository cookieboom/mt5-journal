# Interactive Chart Focus & Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-focus the Interactive Trades chart on the entry point with a "Smart Fit" constraint and display open/close arrow markers.

**Architecture:** We will extend the `CandleChart` component to accept `fitToRange` and `markers` props. `CandleChart` will use `lightweight-charts` API `setVisibleLogicalRange` and `setMarkers`. `TradeView` will calculate the markers (based on direction and entry/exit times) and pass them down alongside the `fitToRange`.

**Tech Stack:** React, lightweight-charts, TypeScript

## Global Constraints

- This logic applies only to the `TradeView` component. Other chart consumers (`Dashboard`, `Live`, `Report`) should remain untouched and function as before.
- Follow existing codebase rules (e.g. no any types unless strictly necessary).
- `lightweight-charts` time format requires seconds (cast to `UTCTimestamp`).

---

### Task 1: Update `CandleChart` to Support Focus Range and Markers

**Files:**
- Modify: `frontend/src/components/CandleChart.tsx`

**Interfaces:**
- Consumes: `fitToRange?: { startMs: number; endMs: number }`, `markers?: import("lightweight-charts").SeriesMarker<import("lightweight-charts").Time>[]`
- Produces: Visual changes to the lightweight-charts instance.

- [ ] **Step 1: Add new props to `CandleChart`**

Modify the props interface for `CandleChart` in `frontend/src/components/CandleChart.tsx`:
```tsx
import type { SeriesMarker, Time } from "lightweight-charts";

// ... existing code ...

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
// ...
```

- [ ] **Step 2: Add useEffect for markers**

Add an effect that updates markers when `props.markers` or `series.current` changes. Also add `setMarkers` to the existing chartType change effect to ensure they survive a series recreation.

```tsx
  // Set markers when they change
  useEffect(() => {
    if (!series.current) return;
    series.current.setMarkers(props.markers ?? []);
  }, [props.markers, props.settings.chartType]);
```

- [ ] **Step 3: Add useEffect for Smart Fit focus range**

Add an effect that calculates the logical range indices based on the `fitToRange` start/end timestamps and applies `setVisibleLogicalRange`.

```tsx
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
```

*(Note: We use `props.candles.length` in the dependency array so it fires once the data loads/backfills, but we don't depend on `props.candles` array identity to avoid scrolling on every tick).*

- [ ] **Step 4: Commit changes**

```bash
git add frontend/src/components/CandleChart.tsx
git commit -m "feat: add fitToRange and markers support to CandleChart"
```

---

### Task 2: Calculate and Pass Props in `TradeView`

**Files:**
- Modify: `frontend/src/pages/TradeView.tsx`

**Interfaces:**
- Consumes: The `CandleChart` component with new props.
- Produces: An updated Trade detail view that focuses on the trade and renders markers.

- [ ] **Step 1: Compute trade markers**

In `frontend/src/pages/TradeView.tsx`, import `SeriesMarker` and `Time` from `lightweight-charts` and compute the markers using `useMemo`. 
Add this just before `return (...)`:

```tsx
  import type { SeriesMarker, Time } from "lightweight-charts";
  import { toSeconds } from "../lib/candles";

  // ... existing code in TradeView ...

  const markers = useMemo(() => {
    if (!t) return undefined;
    const m: SeriesMarker<Time>[] = [];
    const isBuy = t.direction.toLowerCase() === "buy";
    const upColor = settings.colors.up;
    const downColor = settings.colors.down;

    // Entry Marker
    m.push({
      time: toSeconds(t.open_time_msc) as Time,
      position: isBuy ? "belowBar" : "aboveBar",
      color: isBuy ? upColor : downColor,
      shape: isBuy ? "arrowUp" : "arrowDown",
    });

    // Exit Marker
    if (t.close_time_msc != null) {
      m.push({
        time: toSeconds(t.close_time_msc) as Time,
        position: isBuy ? "aboveBar" : "belowBar",
        color: isBuy ? downColor : upColor,
        shape: isBuy ? "arrowDown" : "arrowUp",
      });
    }

    return m;
  }, [t, settings.colors]);

  const fitToRange = useMemo(() => {
    if (!t) return undefined;
    return { startMs: t.open_time_msc, endMs: t.close_time_msc ?? t.open_time_msc };
  }, [t]);
```

- [ ] **Step 2: Pass props to `CandleChart`**

Update the `<CandleChart>` invocation inside `TradeView.tsx` to include `fitToRange` and `markers`:

```tsx
            <CandleChart symbol={t.symbol as Sym} tf={tf} settings={settings}
              candles={shown} overlayLines={overlay} lastBarMs={chart.lastBarMs}
              onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={chart.loadOlder}
              live={null} nowVisible={false}
              fitToRange={fitToRange}
              markers={markers} />
```

- [ ] **Step 3: Run the web app to verify**

```bash
cd frontend && npm run dev &
```
Open a browser to `http://localhost:5173/trades/<some-id>` (or appropriate URL) and verify that:
- The chart focuses on the entry bar automatically.
- The arrow markers appear correctly based on the trade's direction.

- [ ] **Step 4: Commit changes**

```bash
git add frontend/src/pages/TradeView.tsx
git commit -m "feat: pass focus range and markers to trade view chart"
```
