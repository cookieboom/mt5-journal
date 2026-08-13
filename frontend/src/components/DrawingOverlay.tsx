import { colorOf, type Projected } from "../lib/drawings";
import { font } from "../lib/type";

// Absolute SVG over the chart pane, pointer-events:none so the chart stays
// interactive — the same arrangement MeasureOverlay uses. Everything here is a
// pure function of already-projected pixels; no chart API, no state.
export default function DrawingOverlay({
  projected, selectedId,
}: {
  projected: Projected[];
  selectedId: string | null;
}) {
  return (
    <svg
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: "none" }}
      data-testid="drawing-overlay"
    >
      {projected.map(({ d, a, b }) => {
        if (a === null) return null;
        const color = colorOf(d);
        const selected = d.id === selectedId;
        const handles = selected && b !== null && d.kind !== "hline" ? (
          <>
            <circle data-testid={`handle-${d.id}-a`} cx={a.x} cy={a.y} r={4} fill={color} />
            <circle data-testid={`handle-${d.id}-b`} cx={b.x} cy={b.y} r={4} fill={color} />
          </>
        ) : null;

        if (d.kind === "text") {
          return (
            <g key={d.id}>
              <text
                data-testid={`drawing-${d.id}`}
                x={a.x + 4} y={a.y}
                fill={color}
                style={{ font: font("meta") }}
              >
                {d.text}
              </text>
              {selected && (
                <circle data-testid={`handle-${d.id}-a`} cx={a.x} cy={a.y} r={4} fill={color} />
              )}
            </g>
          );
        }

        if (b === null) return null;

        if (d.kind === "rect") {
          return (
            <g key={d.id}>
              <rect
                data-testid={`drawing-${d.id}`}
                x={Math.min(a.x, b.x)} y={Math.min(a.y, b.y)}
                width={Math.abs(b.x - a.x)} height={Math.abs(b.y - a.y)}
                fill={color} fillOpacity={0.10}
                stroke={color} strokeWidth={selected ? 2 : 1}
              />
              {handles}
              {/* The other two corners resize too (hitTest "c1"/"c2"), so they
                  get the same dot — a grab point has to be visible. */}
              {selected && (
                <>
                  <circle data-testid={`handle-${d.id}-c1`} cx={a.x} cy={b.y} r={4} fill={color} />
                  <circle data-testid={`handle-${d.id}-c2`} cx={b.x} cy={a.y} r={4} fill={color} />
                </>
              )}
            </g>
          );
        }

        return (
          <g key={d.id}>
            <line
              data-testid={`drawing-${d.id}`}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={color} strokeWidth={selected ? 2 : 1.5}
              strokeDasharray={d.kind === "hline" ? "4 3" : undefined}
            />
            {handles}
          </g>
        );
      })}
    </svg>
  );
}
