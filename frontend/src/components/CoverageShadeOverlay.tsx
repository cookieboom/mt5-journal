import type { Segment } from "../lib/coverage";

const FILL = { unfetched: "rgba(251,113,133,0.14)", closed: "rgba(63,63,82,0.28)", covered: "" };

// Absolute-positioned bands over the chart canvas. `project(ms)` returns the x
// pixel for a timestamp (CandleChart supplies one backed by the time scale's
// timeToCoordinate, same as MeasureOverlay); null when off-screen.
export default function CoverageShadeOverlay({
  segments, project, height,
}: {
  segments: Segment[]; project: (ms: number) => number | null; height: number;
}) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {segments.filter((s) => s.kind !== "covered").map((s, i) => {
        const x0 = project(s.from);
        const x1 = project(s.to);
        if (x0 == null || x1 == null || x1 <= x0) return null;
        return (
          <div key={i} data-shade={s.kind}
            style={{ position: "absolute", left: x0, width: x1 - x0, top: 0,
                     height, background: FILL[s.kind] }} />
        );
      })}
    </div>
  );
}
