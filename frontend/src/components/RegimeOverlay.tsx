import { regimeColor } from "../lib/lab";
import type { Band } from "../lib/regimeBands";

// Absolute SVG over the chart pane; the wrapper sets pointer-events:none so the
// chart stays interactive underneath. Same sibling-of-the-chart-div, project-by-
// TIME pattern as CoverageShadeOverlay/MeasureOverlay (Spec B lesson: a frozen
// overlay projected by logical index breaks the moment bars are prepended).
// A band with no `toX` mapping (off the currently visible/loaded range) is
// simply not drawn — never a fallback colour, which would read as a
// measurement rather than "no prediction here" (rule 4).
export default function RegimeOverlay({
  bands, toX, height,
}: {
  bands: Band[];
  /** Time (epoch ms) -> pixel x, backed by the chart's own time scale. */
  toX: (timeMsc: number) => number | null;
  height: number;
}) {
  return (
    <svg className="regime-overlay" width="100%" height={height}
         style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {bands.map((band) => {
        const x1 = toX(band.from);
        const x2 = toX(band.to);
        if (x1 === null || x2 === null) return null;
        return (
          <rect key={`${band.from}-${band.regime}`} x={x1} y={0}
                width={Math.max(1, x2 - x1)} height={height}
                fill={regimeColor(band.regime)} />
        );
      })}
    </svg>
  );
}
