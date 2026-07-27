# Interactive Chart Focus & Markers Implementation Plan

## 1. Context & Goal
The Interactive Trades view (`TradeView.tsx`) currently displays charts using `lightweight-charts`, which naturally auto-scrolls to the rightmost (newest) data. For trades that occurred in the past (or are very long), this causes the user to see the end of the trade or blank space when first opening the page, rather than the point of entry. Additionally, it lacks clear visual markers for where the position was opened and closed.

**Goal**: 
1. Auto-focus the chart viewport onto the trade's entry point using a "Smart Fit" approach.
2. Add explicit arrow markers for Open and Close actions on the respective bars.

## 2. Architecture & Design

### 2.1 Smart Fit Auto-Focus
- The `CandleChart` component will accept a new optional prop: `fitToRange?: { startMs: number, endMs: number }`.
- When provided, `CandleChart` will calculate the logical indices of these timestamps in its `candles` data array.
- It will pad the start by 10 bars (to show context prior to the trade).
- It will pad the end by 5 bars.
- **Maximum Zoom-Out Limit**: To prevent candles from becoming unreadably thin on very long-duration trades, the maximum visible range will be capped at **100 bars**. If the trade is longer than 100 bars, the chart will display the first 100 bars starting from the entry point, and the user can manually scroll right to see the exit.
- This behavior will *only* be applied in `TradeView.tsx` so that standard `/chart` and replay charts retain their current "scroll to newest" behavior.

### 2.2 Trade Markers (Open/Close)
- The `CandleChart` component will accept a new optional prop: `markers?: import("lightweight-charts").SeriesMarker<any>[]`.
- When rendering or updating, it will pass these to the lightweight-charts series using `series.current.setMarkers(markers)`.
- `TradeView.tsx` will derive the markers from the trade data:
  - **BUY Trade**:
    - Entry Marker: Placed on `open_time_msc`, shape = `arrowUp`, position = `belowBar`, color = Green (theme upColor).
    - Exit Marker: Placed on `close_time_msc`, shape = `arrowDown`, position = `aboveBar`, color = Red (theme downColor).
  - **SELL Trade**:
    - Entry Marker: Placed on `open_time_msc`, shape = `arrowDown`, position = `aboveBar`, color = Red (theme downColor).
    - Exit Marker: Placed on `close_time_msc`, shape = `arrowUp`, position = `belowBar`, color = Green (theme upColor).
- Markers will be icon-only (no text labels) to maintain a clean aesthetic.

## 3. Component Interface Changes

**`frontend/src/components/CandleChart.tsx`**
```typescript
// New imports needed:
import type { SeriesMarker } from "lightweight-charts";

// New props:
fitToRange?: { startMs: number; endMs: number };
markers?: SeriesMarker<any>[];
```
- A new `useEffect` (or update existing) will apply `timeScale().setVisibleLogicalRange({ from, to })` when data is loaded and `fitToRange` is provided. The effect should only run when the chart is initialized or the trade ID changes, to avoid fighting user scrolls.
- Update `useEffect` dealing with settings to also apply `series.current?.setMarkers(props.markers ?? [])`.

## 4. Risks & Mitigations
- **Race conditions with data loading**: The chart might attempt to set logical ranges before the full backfill data is loaded. `setVisibleLogicalRange` should gracefully clamp, but we must ensure it triggers after `props.candles` is updated.
- **Missing close time**: Open trades don't have a `close_time_msc`. The exit marker will simply be omitted, and `fitToRange.endMs` will equal `startMs`.

## 5. Testing / Definition of Done
1. Open a completed Buy trade in the Interactive viewer. Verify:
   - Chart opens focused on the entry bar, with ~10 bars of context to the left.
   - Green arrow up below the entry bar.
   - Red arrow down above the exit bar.
2. Open a completed Sell trade. Verify inverse marker positioning/colors.
3. Open a very long trade (>100 bars). Verify chart bounds its zoom to 100 bars rather than squishing them.
4. Verify standard `/chart` still opens focused on the rightmost (live/newest) bar.
