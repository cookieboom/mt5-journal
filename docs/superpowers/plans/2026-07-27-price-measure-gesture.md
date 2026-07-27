# Price-Measurement Gesture (Spec B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TradingView-style "double-click then hold" price-measurement gesture to every chart, showing live Δprice, %, and time/bar distance, persisting until Esc/click.

**Architecture:** All decision logic (metrics + gesture state machine + double-click detection) lives as pure functions in `frontend/src/lib/measure.ts`, unit-tested without a chart. A presentational SVG component `MeasureOverlay.tsx` draws the box/line/markers/label from already-projected pixel coordinates. `CandleChart.tsx` is the only place that touches lightweight-charts: it translates DOM pointer events → data coordinates, calls the reducer, and projects data coordinates → pixels for the overlay. Front-end only; no backend, no new dependencies.

**Tech Stack:** React 18 + TypeScript, lightweight-charts 5.2.0, vitest + @testing-library/react (jsdom).

## Global Constraints

- **Front-end only.** No changes under `src/journal/` (Python). No new npm dependencies. (Roadmap: Spec B is "Kecil / FE-only".)
- **Timestamps are epoch milliseconds, integer, UTC (broker server time).** Chart lib wants UNIX seconds — divide by 1000 only at the lib boundary. Never store naive/local time. (CLAUDE.md rule 3.)
- **Money/prices are floats; compare with tolerance `Math.abs(a-b) < 1e-9`, never `==`.** (CLAUDE.md rule 5.)
- **No ticks/pips** — `tick_size` is not in the FE payload; showing it would need a backend change. Excluded by design.
- Reuse existing helpers; do **not** modify `format.ts:dur` (it mirrors Python `format.py` and caps at hours). Measurement spans can cross days, so this plan adds a separate `fmtSpan`.
- Theme up/down colors already exist in `CandleChart.tsx` (`DARK`/`LIGHT`: `up`/`down`). The gesture color follows Δ sign.
- Definition of done (CLAUDE.md): `vitest` green, `tsc`/build 0 errors, `uv run pytest` still green, `journal rebuild` still OK, then `graphify update .`. In-browser visual pass is a human step, noted at the end.

---

## File Structure

- **Create** `frontend/src/lib/measure.ts` — pure: `Point`, `MeasureMetrics`, `MeasureState`, `MeasureEvent`, `computeMetrics`, `fmtSpan`, `measureReducer`, `isDoubleClickHold`, constants `DBLCLICK_MS`, `DBLCLICK_PX`.
- **Create** `frontend/src/lib/measure.test.ts` — vitest for all of the above.
- **Create** `frontend/src/components/MeasureOverlay.tsx` — presentational SVG.
- **Create** `frontend/src/components/MeasureOverlay.test.tsx` — render test.
- **Modify** `frontend/src/components/CandleChart.tsx` — pointer/keyboard wiring, projection, render `<MeasureOverlay>`, auto-clear.

Task order: 1 → 2 build the pure core (independently testable); 3 builds the view (independently testable); 4 wires them into the chart (verified by build + existing suite + manual visual pass).

---

### Task 1: Pure metrics + duration formatter (`measure.ts`)

**Files:**
- Create: `frontend/src/lib/measure.ts`
- Test: `frontend/src/lib/measure.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `interface Point { price: number; barTimeMs: number; logical: number }`
  - `interface MeasureMetrics { dPrice: number; pct: number | null; bars: number; dTimeMs: number; up: boolean }`
  - `function computeMetrics(anchor: Point, cursor: Point): MeasureMetrics`
  - `function fmtSpan(ms: number): string`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/measure.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { computeMetrics, fmtSpan, type Point } from "./measure";

const P = (price: number, barTimeMs: number, logical: number): Point => ({ price, barTimeMs, logical });

describe("computeMetrics", () => {
  it("positive move: dPrice, pct, bars, dTimeMs, up=true", () => {
    const anchor = P(2000, 1_000_000, 10);
    const cursor = P(2012.34, 1_000_000 + 8100_000, 19); // +8100s = 2h15m, 9 bars
    const m = computeMetrics(anchor, cursor);
    expect(m.dPrice).toBeCloseTo(12.34, 9);
    expect(m.pct).toBeCloseTo(0.617, 3); // 12.34/2000*100
    expect(m.bars).toBe(9);
    expect(m.dTimeMs).toBe(8100_000);
    expect(m.up).toBe(true);
  });

  it("negative move (cursor below/before anchor): dPrice<0, up=false, bars & dTimeMs are absolute", () => {
    const anchor = P(2000, 2_000_000, 20);
    const cursor = P(1990, 1_000_000, 12);
    const m = computeMetrics(anchor, cursor);
    expect(m.dPrice).toBeCloseTo(-10, 9);
    expect(m.up).toBe(false);
    expect(m.bars).toBe(8);
    expect(m.dTimeMs).toBe(1_000_000);
  });

  it("zero anchor price → pct is null (no divide-by-zero)", () => {
    const m = computeMetrics(P(0, 0, 0), P(5, 100, 1));
    expect(m.pct).toBeNull();
    expect(m.dPrice).toBeCloseTo(5, 9);
  });

  it("bars rounds a fractional logical difference", () => {
    const m = computeMetrics(P(1, 0, 10.2), P(1, 0, 13.9));
    expect(m.bars).toBe(4); // |13.9-10.2| = 3.7 → 4
  });
});

describe("fmtSpan", () => {
  it("minutes only", () => { expect(fmtSpan(45 * 60_000)).toBe("45m"); });
  it("hours + minutes", () => { expect(fmtSpan((2 * 3600 + 15 * 60) * 1000)).toBe("2h 15m"); });
  it("days + hours", () => { expect(fmtSpan((3 * 86400 + 4 * 3600) * 1000)).toBe("3d 4h"); });
  it("under a minute → 0m", () => { expect(fmtSpan(5000)).toBe("0m"); });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/measure.test.ts`
Expected: FAIL — cannot resolve `./measure` (module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/measure.ts`:

```ts
// Pure helpers for the price-measurement gesture (Spec B). No chart access here.
// Time is epoch-ms (broker server, UTC). Price/pct are unit-free ratios of price.

export interface Point {
  price: number;      // exact price at the pointer (from coordinateToPrice)
  barTimeMs: number;  // time of the bar under the pointer (gap-aware Δtime)
  logical: number;    // fractional bar index (from coordinateToLogical)
}

export interface MeasureMetrics {
  dPrice: number;         // cursor.price - anchor.price
  pct: number | null;     // null when anchor.price == 0 (guard divide-by-zero)
  bars: number;           // rounded |Δlogical|
  dTimeMs: number;        // |Δ bar time|, gap-aware
  up: boolean;            // dPrice >= 0
}

export function computeMetrics(anchor: Point, cursor: Point): MeasureMetrics {
  const dPrice = cursor.price - anchor.price;
  const pct = Math.abs(anchor.price) < 1e-9 ? null : (dPrice / anchor.price) * 100;
  const bars = Math.round(Math.abs(cursor.logical - anchor.logical));
  const dTimeMs = Math.abs(cursor.barTimeMs - anchor.barTimeMs);
  return { dPrice, pct, bars, dTimeMs, up: dPrice >= 0 };
}

// Human span for the measurement readout. Unlike format.ts:dur (mirrors Python,
// caps at hours), a chart measurement can cross days, so this adds a day bucket.
export function fmtSpan(ms: number): string {
  const totalMin = Math.floor(ms / 60_000);
  const days = Math.floor(totalMin / 1440);
  const hours = Math.floor((totalMin % 1440) / 60);
  const mins = totalMin % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/measure.test.ts`
Expected: PASS (8 assertions across 8 `it` blocks).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/measure.ts frontend/src/lib/measure.test.ts
git commit -m "feat(fe): measure.ts — pure Δprice/%/bars/time metrics + fmtSpan (Spec B)"
```

---

### Task 2: Gesture state machine + double-click detection (`measure.ts`)

**Files:**
- Modify: `frontend/src/lib/measure.ts` (append)
- Test: `frontend/src/lib/measure.test.ts` (append)

**Interfaces:**
- Consumes: `Point` from Task 1.
- Produces:
  - `type MeasureState = { phase: "idle" } | { phase: "measuring"; anchor: Point; cursor: Point } | { phase: "frozen"; anchor: Point; cursor: Point }`
  - `type MeasureEvent = { t: "start"; anchor: Point } | { t: "move"; cursor: Point } | { t: "release" } | { t: "clear" }`
  - `const IDLE: MeasureState` (the `{ phase: "idle" }` singleton)
  - `function measureReducer(s: MeasureState, e: MeasureEvent): MeasureState`
  - `const DBLCLICK_MS = 350`, `const DBLCLICK_PX = 5`
  - `function isDoubleClickHold(prevUpMs: number | null, prevX: number, prevY: number, nowMs: number, nowX: number, nowY: number): boolean`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/lib/measure.test.ts`:

```ts
import {
  measureReducer, isDoubleClickHold, IDLE, DBLCLICK_MS, DBLCLICK_PX,
  type MeasureState,
} from "./measure";

const pt = (price: number): Point => ({ price, barTimeMs: price * 1000, logical: price });

describe("measureReducer", () => {
  it("start from idle → measuring with anchor, cursor seeded to anchor", () => {
    const s = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    expect(s.phase).toBe("measuring");
    if (s.phase !== "idle") { expect(s.anchor).toEqual(pt(1)); expect(s.cursor).toEqual(pt(1)); }
  });

  it("move while measuring updates cursor, keeps anchor", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "move", cursor: pt(2) });
    if (s.phase === "measuring") { expect(s.cursor).toEqual(pt(2)); expect(s.anchor).toEqual(pt(1)); }
    else throw new Error("expected measuring");
  });

  it("release while measuring → frozen, keeping anchor & cursor", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "move", cursor: pt(3) });
    s = measureReducer(s, { t: "release" });
    expect(s.phase).toBe("frozen");
    if (s.phase === "frozen") { expect(s.anchor).toEqual(pt(1)); expect(s.cursor).toEqual(pt(3)); }
  });

  it("start from frozen replaces the old measurement", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "release" });
    s = measureReducer(s, { t: "start", anchor: pt(9) });
    expect(s.phase).toBe("measuring");
    if (s.phase !== "idle") expect(s.anchor).toEqual(pt(9));
  });

  it("clear always returns idle", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    expect(measureReducer(s, { t: "clear" })).toEqual(IDLE);
    s = measureReducer(s, { t: "release" });
    expect(measureReducer(s, { t: "clear" })).toEqual(IDLE);
    expect(measureReducer(IDLE, { t: "clear" })).toEqual(IDLE);
  });

  it("move/release are no-ops in idle", () => {
    expect(measureReducer(IDLE, { t: "move", cursor: pt(2) })).toEqual(IDLE);
    expect(measureReducer(IDLE, { t: "release" })).toEqual(IDLE);
  });

  it("move is a no-op while frozen (only a new start changes it)", () => {
    let s: MeasureState = measureReducer(IDLE, { t: "start", anchor: pt(1) });
    s = measureReducer(s, { t: "release" });
    const before = s;
    expect(measureReducer(s, { t: "move", cursor: pt(5) })).toEqual(before);
  });
});

describe("isDoubleClickHold", () => {
  it("true when second press is within time and distance", () => {
    expect(isDoubleClickHold(1000, 100, 100, 1000 + DBLCLICK_MS - 1, 100 + DBLCLICK_PX - 1, 100)).toBe(true);
  });
  it("false when too slow", () => {
    expect(isDoubleClickHold(1000, 100, 100, 1000 + DBLCLICK_MS + 1, 100, 100)).toBe(false);
  });
  it("false when too far", () => {
    expect(isDoubleClickHold(1000, 100, 100, 1010, 100 + DBLCLICK_PX + 1, 100)).toBe(false);
  });
  it("false when there was no previous up (null)", () => {
    expect(isDoubleClickHold(null, 0, 0, 1000, 0, 0)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/measure.test.ts`
Expected: FAIL — `measureReducer`, `isDoubleClickHold`, `IDLE`, `DBLCLICK_MS`, `DBLCLICK_PX` are not exported.

- [ ] **Step 3: Write minimal implementation**

Append to `frontend/src/lib/measure.ts`:

```ts
export type MeasureState =
  | { phase: "idle" }
  | { phase: "measuring"; anchor: Point; cursor: Point }
  | { phase: "frozen"; anchor: Point; cursor: Point };

export type MeasureEvent =
  | { t: "start"; anchor: Point }
  | { t: "move"; cursor: Point }
  | { t: "release" }
  | { t: "clear" };

export const IDLE: MeasureState = { phase: "idle" };

// Pure transition. `move`/`release` outside `measuring` are no-ops; `start`
// always (re)opens a fresh measurement; `clear` always returns to idle.
export function measureReducer(s: MeasureState, e: MeasureEvent): MeasureState {
  switch (e.t) {
    case "start":
      return { phase: "measuring", anchor: e.anchor, cursor: e.anchor };
    case "move":
      return s.phase === "measuring" ? { ...s, cursor: e.cursor } : s;
    case "release":
      return s.phase === "measuring"
        ? { phase: "frozen", anchor: s.anchor, cursor: s.cursor }
        : s;
    case "clear":
      return IDLE;
  }
}

export const DBLCLICK_MS = 350;
export const DBLCLICK_PX = 5;

// The second mousedown of a double-click, held: within DBLCLICK_MS of the last
// pointerup and within DBLCLICK_PX of where it happened.
export function isDoubleClickHold(
  prevUpMs: number | null, prevX: number, prevY: number,
  nowMs: number, nowX: number, nowY: number,
): boolean {
  if (prevUpMs === null) return false;
  if (nowMs - prevUpMs > DBLCLICK_MS) return false;
  return Math.hypot(nowX - prevX, nowY - prevY) < DBLCLICK_PX;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/measure.test.ts`
Expected: PASS (all Task 1 + Task 2 blocks green).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/measure.ts frontend/src/lib/measure.test.ts
git commit -m "feat(fe): measure.ts — pure gesture reducer + double-click-hold detector (Spec B)"
```

---

### Task 3: `MeasureOverlay` SVG component

**Files:**
- Create: `frontend/src/components/MeasureOverlay.tsx`
- Test: `frontend/src/components/MeasureOverlay.test.tsx`

**Interfaces:**
- Consumes: `MeasureMetrics` from Task 1, `fmtSpan` from Task 1, `price`/`pct` are formatted inline (numbers → strings) in the component.
- Produces:
  - `interface ProjectedPoint { x: number; y: number }`
  - `interface MeasureOverlayProps { anchor: ProjectedPoint; cursor: ProjectedPoint; metrics: MeasureMetrics; upColor: string; downColor: string }`
  - `export default function MeasureOverlay(props: MeasureOverlayProps): JSX.Element`
  - It is the caller's job (Task 4) to render nothing when there is no measurement or when either point is off-screen; this component assumes both points are valid pixels.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/MeasureOverlay.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MeasureOverlay from "./MeasureOverlay";
import type { MeasureMetrics } from "../lib/measure";

const metrics: MeasureMetrics = { dPrice: 12.34, pct: 0.617, bars: 9, dTimeMs: 8100_000, up: true };

describe("MeasureOverlay", () => {
  it("renders the label with Δprice, %, bars and span", () => {
    render(
      <MeasureOverlay
        anchor={{ x: 10, y: 200 }}
        cursor={{ x: 120, y: 60 }}
        metrics={metrics}
        upColor="#34d399"
        downColor="#fb7185"
      />,
    );
    const label = screen.getByTestId("measure-label");
    expect(label.textContent).toContain("12.34");
    expect(label.textContent).toContain("%");
    expect(label.textContent).toContain("9 bars");
    expect(label.textContent).toContain("2h 15m");
  });

  it("shows a dash for pct when null (zero anchor guard)", () => {
    render(
      <MeasureOverlay
        anchor={{ x: 0, y: 0 }}
        cursor={{ x: 50, y: 50 }}
        metrics={{ dPrice: 5, pct: null, bars: 1, dTimeMs: 60_000, up: true }}
        upColor="#34d399"
        downColor="#fb7185"
      />,
    );
    expect(screen.getByTestId("measure-label").textContent).toContain("—");
  });

  it("uses downColor when up=false", () => {
    const { container } = render(
      <MeasureOverlay
        anchor={{ x: 10, y: 10 }}
        cursor={{ x: 20, y: 40 }}
        metrics={{ ...metrics, dPrice: -3, up: false }}
        upColor="#34d399"
        downColor="#fb7185"
      />,
    );
    // the connecting line is stroked with the direction colour
    const line = container.querySelector('[data-testid="measure-line"]');
    expect(line?.getAttribute("stroke")).toBe("#fb7185");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/MeasureOverlay.test.tsx`
Expected: FAIL — cannot resolve `./MeasureOverlay`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/MeasureOverlay.tsx`:

```tsx
import { fmtSpan, type MeasureMetrics } from "../lib/measure";

export interface ProjectedPoint { x: number; y: number }
export interface MeasureOverlayProps {
  anchor: ProjectedPoint;
  cursor: ProjectedPoint;
  metrics: MeasureMetrics;
  upColor: string;
  downColor: string;
}

// Absolute SVG over the chart pane; the wrapper sets pointer-events:none so the
// chart stays interactive. Coordinates are already-projected pixels (Task 4).
export default function MeasureOverlay(props: MeasureOverlayProps) {
  const { anchor, cursor, metrics, upColor, downColor } = props;
  const color = metrics.up ? upColor : downColor;
  const left = Math.min(anchor.x, cursor.x);
  const top = Math.min(anchor.y, cursor.y);
  const w = Math.abs(cursor.x - anchor.x);
  const h = Math.abs(cursor.y - anchor.y);

  const sign = metrics.dPrice >= 0 ? "+" : "";
  const dPriceStr = `${sign}${metrics.dPrice.toFixed(3)}`;
  const pctStr = metrics.pct === null ? "—" : `${metrics.pct >= 0 ? "+" : ""}${metrics.pct.toFixed(2)}%`;
  const spanStr = `⏱ ${fmtSpan(metrics.dTimeMs)} · ${metrics.bars} bars`;

  // Label sits just outside the cursor endpoint, nudged to stay in view.
  const labelX = cursor.x + 8;
  const labelY = Math.max(cursor.y - 8, 12);

  return (
    <svg
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: "none" }}
      data-testid="measure-overlay"
    >
      <rect x={left} y={top} width={w} height={h} fill={color} fillOpacity={0.10} />
      <line
        data-testid="measure-line"
        x1={anchor.x} y1={anchor.y} x2={cursor.x} y2={cursor.y}
        stroke={color} strokeWidth={1.5}
      />
      <circle cx={anchor.x} cy={anchor.y} r={3} fill={color} />
      <circle cx={cursor.x} cy={cursor.y} r={3} fill={color} />
      <g transform={`translate(${labelX} ${labelY})`}>
        <foreignObject x={0} y={0} width={180} height={54} style={{ overflow: "visible" }}>
          <div
            data-testid="measure-label"
            style={{
              display: "inline-block", background: "rgba(15,15,25,0.85)", color: "#e6e6f0",
              font: "11px/1.35 ui-monospace, monospace", padding: "3px 6px", borderRadius: 4,
              border: `1px solid ${color}`, whiteSpace: "nowrap",
            }}
          >
            <div style={{ color }}>{dPriceStr} ({pctStr})</div>
            <div>{spanStr}</div>
          </div>
        </foreignObject>
      </g>
    </svg>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/MeasureOverlay.test.tsx`
Expected: PASS (3 blocks).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MeasureOverlay.tsx frontend/src/components/MeasureOverlay.test.tsx
git commit -m "feat(fe): MeasureOverlay — SVG box/line/marker/label for measure gesture (Spec B)"
```

---

### Task 4: Wire the gesture into `CandleChart`

**Files:**
- Modify: `frontend/src/components/CandleChart.tsx`

**Interfaces:**
- Consumes: everything from Tasks 1–3 (`measure.ts` exports, `MeasureOverlay`).
- Produces: no new public API. The gesture is internal to `CandleChart`; because `Chart.tsx` and `TradeView.tsx` already render `<CandleChart>`, both get it for free.

This task is integration wiring against the live lightweight-charts instance. jsdom has no chart geometry, so it has **no unit test** (consistent with the rest of `CandleChart`, which is untested and verified in-browser). It is verified by: existing suite still green, `tsc`/build clean, and the manual visual pass in the Definition of Done. Keep the wiring thin — all logic is already in `measure.ts`.

- [ ] **Step 1: Add imports and refs**

At the top of `frontend/src/components/CandleChart.tsx`, add to the React import so state/callbacks are available:

```tsx
import {
  forwardRef, useEffect, useImperativeHandle, useRef, useState, useCallback,
} from "react";
```

Add the measure imports (after the existing `import { wib } from "../lib/format";` line):

```tsx
import MeasureOverlay, { type ProjectedPoint } from "./MeasureOverlay";
import {
  measureReducer, computeMetrics, isDoubleClickHold, IDLE,
  type MeasureState, type Point,
} from "../lib/measure";
```

- [ ] **Step 2: Add gesture state + a data→pixel projector**

Inside the `CandleChart` function body, after the existing refs (e.g. after `const cbs = useRef(props); cbs.current = props;`), add:

```tsx
  const [measure, setMeasure] = useState<MeasureState>(IDLE);
  const lastUp = useRef<{ ms: number; x: number; y: number } | null>(null);
  const dragging = useRef(false);

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
```

- [ ] **Step 3: Add the pointer/keyboard effect (gesture wiring)**

Add a new `useEffect` after the chart-creation effect (the one that ends `}, []);` around the `subscribeVisibleLogicalRangeChange` block). It attaches native listeners to the chart DOM element and toggles chart panning during a drag:

```tsx
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
        dragging.current = false;
        c.applyOptions({ handleScroll: true, handleScale: true });
        setMeasure((s) => measureReducer(s, { t: "release" }));
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMeasure((s) => measureReducer(s, { t: "clear" }));
    };

    node.addEventListener("pointerdown", onDown);
    node.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    return () => {
      node.removeEventListener("pointerdown", onDown);
      node.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
    };
  }, [toPoint]);
```

- [ ] **Step 4: Auto-clear on symbol / timeframe / chart-type change**

Add this effect (near the other `props`-watching effects) so a measurement never lingers over data it no longer matches:

```tsx
  // Data identity changed → the stored data coordinates may no longer line up.
  useEffect(() => {
    setMeasure((s) => (s.phase === "idle" ? s : IDLE));
  }, [props.symbol, props.tf, props.settings.chartType]);
```

- [ ] **Step 5: Project the measurement and render the overlay**

Compute projected pixels on every render (cheap; the chart re-renders on data/crosshair anyway) and render `MeasureOverlay` alongside the chart div. Replace the component's return:

```tsx
  return <div ref={el} className="w-full h-full" />;
```

with:

```tsx
  const theme = props.settings.theme === "light" ? LIGHT : DARK;
  const project = (p: Point): ProjectedPoint | null => {
    const c = chart.current, s = series.current;
    if (!c || !s) return null;
    const x = c.timeScale().logicalToCoordinate(p.logical as never);
    const y = s.priceToCoordinate(p.price);
    if (x === null || y === null) return null;
    return { x: x as number, y: y as number };
  };
  let overlay: JSX.Element | null = null;
  if (measure.phase !== "idle") {
    const a = project(measure.anchor);
    const cur = project(measure.cursor);
    if (a && cur) {
      overlay = (
        <MeasureOverlay
          anchor={a} cursor={cur}
          metrics={computeMetrics(measure.anchor, measure.cursor)}
          upColor={theme.up} downColor={theme.down}
        />
      );
    }
  }

  return (
    <div ref={el} className="w-full h-full relative">
      {overlay}
    </div>
  );
```

Note: projection reads live chart state, so it naturally re-projects on pan/zoom/resize as React re-renders (crosshair moves and data pushes already trigger renders via parent state). If either endpoint scrolls off-screen, `project` returns `null` and the overlay is hidden while the state is kept — exactly the spec's off-screen behavior.

- [ ] **Step 6: Run the full front-end suite + typecheck + build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: vitest all green (including new `measure` + `MeasureOverlay` tests), `tsc` 0 errors, build succeeds. If `tsc` complains about `logicalToCoordinate` arg type, the `as never` cast in `project` handles the `Logical` branded type; keep it.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CandleChart.tsx
git commit -m "feat(fe): wire double-click-hold measure gesture into CandleChart (Spec B)"
```

---

## Post-implementation (after all tasks)

- [ ] **Backend/regression sanity (nothing backend changed, but the DoD requires it):**

Run: `uv run pytest -q && uv run journal rebuild`
Expected: pytest green, rebuild OK.

- [ ] **Update the roadmap Status column** in `docs/ROADMAP-trade-chart-features.md`: Spec B row → done (link this plan), note "pending human visual pass".

- [ ] **Refresh the graph:**

Run: `graphify update .`

- [ ] **Human visual pass (cannot be automated):** open `journal serve` → a chart (main Chart page and a `/trades/:id/view`), and verify: double-click-then-hold starts a measurement; readout updates live and shows Δprice/%/time/bars; drag does not pan; releasing freezes it; pan/zoom re-projects it; scrolling an endpoint off-screen hides it and back on-screen restores it; `Esc` and a plain click both clear it; switching symbol/timeframe clears it.

---

## Self-Review

**Spec coverage:**
- Readout Δprice/%/time-bars → Task 1 `computeMetrics` + Task 3 label. ✓
- No ticks/pips → excluded (Global Constraints). ✓
- Exact price (no snap) → `coordinateToPrice` in Task 4 `toPoint`, no rounding of price. ✓
- Gap-aware Δtime from bar time → `barTimeMs` from candles in `toPoint`; `computeMetrics.dTimeMs`. ✓
- Persist until Esc/click ("menetap") → reducer `frozen` state; `onUp`→release, `onKey`/plain-press→clear. ✓
- Double-click-then-hold detection + suppress pan → `isDoubleClickHold` + `handleScroll/handleScale=false`. ✓
- All charts via CandleChart → Task 4 modifies the shared component. ✓
- Single measurement, new replaces old → reducer `start` from any state. ✓
- Auto-clear on symbol/tf/chartType → Task 4 Step 4. ✓
- Off-screen hide-but-keep-state → `project` returns null → overlay null, state kept. ✓
- Divide-by-zero guard → `computeMetrics.pct` null path + Task 3 dash test. ✓
- FE-only, no deps → no `src/journal/` edits, no package.json deps. ✓
- Tests: metrics, fmtSpan, reducer transitions, double-click thresholds, overlay render → Tasks 1–3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type consistency:** `Point`, `MeasureState`, `MeasureEvent`, `MeasureMetrics`, `measureReducer`, `computeMetrics`, `isDoubleClickHold`, `IDLE`, `DBLCLICK_MS/PX`, `fmtSpan`, `MeasureOverlay`, `ProjectedPoint` are named identically across Tasks 1–4. `computeMetrics(anchor, cursor)` signature used in Task 4 matches Task 1. `MeasureOverlayProps` used in Task 4 matches Task 3. ✓
