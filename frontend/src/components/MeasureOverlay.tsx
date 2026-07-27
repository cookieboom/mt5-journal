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
