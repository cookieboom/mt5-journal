// The palette DESIGN.md names, in one place.
//
// `tailwind.config.ts` reads `palette` and `shadow`, so `text-neg` in a class
// string and a candle colour on the canvas resolve to the same hex. Canvas and
// SVG code cannot reach Tailwind at all — it imports from here rather than
// hard-coding, so a future light theme has one file to hunt through.

export const palette = {
  bg: "#0b0a1a",
  "bg-glow": "#1a1740",
  panel: "rgba(255,255,255,0.045)",
  "panel-border": "rgba(255,255,255,0.09)",
  ink: "#e8e6ff",
  muted: "#9a97c4",
  violet: "#a78bfa",
  cyan: "#22d3ee",
  pos: "#34d399",
  neg: "#fb7185",
  // Degraded but not wrong: partial coverage, a dropped feature, a caveat.
  // Rose stays reserved for "this is wrong" / "this went against you".
  warn: "#fbbf24",
  // Canvas annotations. Never chrome — see DESIGN.md § Colors / Tertiary.
  "exit-amber": "#f59e0b",
  "mark-amber": "#fbbf24",
  "mark-sky": "#7dd3fc",
  "mark-chalk": "#e6e6f0",
  "closed-slate": "#3f3f52",
} as const;

export const shadow = {
  // The One Glow Rule: the live dot is the only light-emitting element.
  glow: `0 0 8px ${palette.cyan}`,
  float: "0 12px 32px rgba(0,0,0,0.45)",
} as const;

/** `#rrggbb` at `alpha`. Tints belong to the token, not to a pasted rgba(). */
export function tint(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** White at `alpha` — grids, hairlines, axis rules over the dusk ground. */
export const white = (alpha: number) => `rgba(255,255,255,${alpha})`;

// Chart themes. Dark inherits the page ground (transparent); light is the
// selectable second theme and the seed of a future app-wide light mode.
export const chartDark = {
  bg: "transparent",
  text: palette.muted,
  grid: white(0.06),
  border: palette["panel-border"],
  up: palette.pos,
  down: palette.neg,
} as const;

export const chartLight = {
  bg: "#ffffff",
  text: "#334155",
  grid: "rgba(0,0,0,0.06)",
  border: "rgba(0,0,0,0.12)",
  up: "#059669",
  down: "#e11d48",
} as const;
