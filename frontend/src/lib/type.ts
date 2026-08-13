// The type scale DESIGN.md names, in one place — the typographic half of
// `theme.ts`.
//
// `tailwind.config.ts` reads `typeScale` as its *whole* `fontSize` theme, not
// as an extension: Tailwind's own default steps are removed on purpose. Two
// scales ran side by side here for a long time — 18 distinct sizes serving 6
// roles, an 11.5px label next to an 11px one next to a 12px one —
// and the roles stopped being legible as roles. Deleting the default steps is
// what stops the second scale coming back: an off-scale `text-sm` no longer
// resolves to anything, and shows up the moment the page is looked at.
//
// SVG and inline-style text cannot reach Tailwind, so it imports `font()` from
// here for the same reason canvas colours import `palette`.

/** One role per line: size, leading, tracking. Nothing between the steps. */
export const typeScale = {
  /** KPI values only. The largest thing on a page is always a number. */
  display: ["23px", { lineHeight: "1.1", letterSpacing: "-0.01em" }],
  /** The page title, once per route — and the title of a layer over it. */
  headline: ["18px", { lineHeight: "1.2", letterSpacing: "-0.01em" }],
  /** Panel headings. */
  title: ["13px", { lineHeight: "1.3", letterSpacing: "normal" }],
  /** Table cells, values, prose. The reading size. */
  body: ["12px", { lineHeight: "1.5", letterSpacing: "normal" }],
  /** The n-Beside-It role: sample sizes, units, method lines, subtitles. */
  meta: ["11px", { lineHeight: "1.4", letterSpacing: "normal" }],
  /** UPPERCASE captions and column heads. The floor — nothing is smaller.
   *  Was 9.5px, and 8.5px in three places that never noticed they had left
   *  the scale; 10px is the smallest size that survives a laptop screen at
   *  arm's length, which is the only screen this runs on. */
  label: ["10px", { lineHeight: "1.2", letterSpacing: "0.09em" }],
} as const;

export type TypeRole = keyof typeof typeScale;

/** `font:` shorthand for a role — SVG and inline styles, which Tailwind
 *  cannot reach. `family` defaults to the mono stack the overlays use. */
export function font(
  role: TypeRole,
  family = "ui-monospace, monospace",
): string {
  const [size, { lineHeight }] = typeScale[role];
  return `${size}/${lineHeight} ${family}`;
}
