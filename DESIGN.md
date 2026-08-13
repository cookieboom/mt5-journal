---
name: mt5-journal
description: A dark desk-lamp instrument panel for one trader's own trading history.
colors:
  bg: "#0b0a1a"
  bg-glow: "#1a1740"
  panel: "rgba(255,255,255,0.045)"
  panel-border: "rgba(255,255,255,0.09)"
  ink: "#e8e6ff"
  muted: "#9a97c4"
  violet: "#a78bfa"
  cyan: "#22d3ee"
  pos: "#34d399"
  neg: "#fb7185"
  warn: "#fbbf24"
  exit-amber: "#f59e0b"
  mark-amber: "#fbbf24"
  mark-sky: "#7dd3fc"
  mark-chalk: "#e6e6f0"
  closed-slate: "#3f3f52"
  chart-light-bg: "#ffffff"
  chart-light-ink: "#334155"
  chart-light-pos: "#059669"
  chart-light-neg: "#e11d48"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "23px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "9.5px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "0.09em"
  numeral:
    fontFamily: "Inter, system-ui, sans-serif"
    fontWeight: 700
    fontFeature: "tnum"
  mono:
    fontFamily: "'SF Mono', ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
  glass: "14px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  "2xl": "24px"
components:
  card-glass:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.glass}"
    padding: "16px"
  kpi-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.numeral}"
    rounded: "{rounded.glass}"
    padding: "14px"
  button-glass:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    rounded: "{rounded.glass}"
    padding: "4px 10px"
  button-glass-hover:
    textColor: "{colors.ink}"
  button-commit:
    backgroundColor: "rgba(34,211,238,0.20)"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "6px 12px"
  button-mine:
    backgroundColor: "rgba(167,139,250,0.20)"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "6px 12px"
  input-field:
    backgroundColor: "rgba(255,255,255,0.05)"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
  nav-item:
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  nav-item-active:
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  pill-now:
    backgroundColor: "rgba(34,211,238,0.10)"
    textColor: "{colors.cyan}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  pill-wrong:
    backgroundColor: "rgba(251,113,133,0.10)"
    textColor: "{colors.neg}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  chip-mine:
    backgroundColor: "rgba(167,139,250,0.15)"
    textColor: "{colors.violet}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  chip-derived:
    backgroundColor: "rgba(255,255,255,0.06)"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
---

# Design System: mt5-journal

## Overview

**Creative North Star: "The Night Desk"**

One trader, one lit desk, one dark room. The page has no daylight in it: a violet
dusk falls from the top-right corner of the viewport and everything else is a
translucent panel floating on that dusk, edged by a single hairline of light. The
interface is the desk lamp, not the work. What glows is data — a live price, a
covered range, an R-multiple in tabular numerals — and everything that is not data
sits back at 60% brightness until it is touched.

The density is deliberately high and the type is deliberately small. This is a
single-window instrument beside a running MT5 terminal, not a page to be admired
across a room. Labels shrink to 9.5px so the numbers can own the contrast; panels
carry 14–16px of padding, not 32px; the gap between cards is 12–14px. Nothing is
sized to fill space. When a surface has nothing to say, it says so in muted text
rather than growing an illustration.

The system is unsentimental by construction. There is no celebratory colour, no
success state that behaves differently from a failure state, no motion that
rewards. Green and rose are outcomes, not moods. What the interface *does* express
is liveness and ownership: cyan is the pulse of now, violet is the trace of the
person using it. Everything else is glass.

**Key Characteristics:**
- Violet dusk radial ground, fixed, never scrolling with content
- Translucent panels at 4.5% white with a 9% white hairline and an 8px backdrop blur
- Tabular numerals on every figure; the number never moves as it updates
- Type ceiling of 23px; label floor of 9.5px uppercase at 0.09em
- Cyan reserved for now, violet reserved for the user's own marks
- High density: 186px nav rail, 20–24px page padding, 12–14px card gutters

## Colors

A cold violet-blue ground with two accents that mean different things, and a
green/rose pair that means only one thing: outcome.

### Primary
- **Signal Cyan** (`{colors.cyan}`): the pulse of *now*. Live dot and its glow, feed
  age, coverage percentage, backfill controls, the commit button on a queued
  command, the replay entry point. If a value would be different one second from
  now, cyan is how the interface says so.
- **Trace Violet** (`{colors.violet}`): the mark of *you*. Active navigation, manual
  tags, the selected drawing tool, drawn horizontal lines, the button that adds
  something of the user's own. Violet never reports a market fact.

### Secondary
- **Ledger Green** (`{colors.pos}`): a positive outcome — up candles, profitable R,
  the TP line. Never used for "success", "saved", or "OK".
- **Ledger Rose** (`{colors.neg}`): a negative outcome or a wrong state — down
  candles, losing R, the SL line, unfetched coverage holes, rejected commands, a
  dead daemon. One colour for "this went against you" and "this is broken", because
  both demand the same response: look.
- **Caution Amber** (`{colors.warn}`): degraded but not wrong — coverage that is
  85–99% complete, a feature the trainer dropped, a replay caveat. It exists so
  rose keeps meaning "wrong"; a partial fetch is not a broken one. Never an
  outcome, never a mood.

### Tertiary
Chart annotation colours, used only on the canvas and never in chrome:
- **Exit Amber** (`{colors.exit-amber}`): the realised exit price line.
- **Mark Amber** (`{colors.mark-amber}`): user-drawn rectangles.
- **Mark Sky** (`{colors.mark-sky}`): user-drawn trendlines.
- **Mark Chalk** (`{colors.mark-chalk}`): user-drawn text, and overlay tooltip text.
- **Closed Slate** (`{colors.closed-slate}`): market-closed spans in the coverage
  ribbon — absence that is legitimate, greyed rather than flagged.

### Neutral
- **Dusk Ink** (`{colors.bg}`) into **Dusk Glow** (`{colors.bg-glow}`): the fixed
  radial ground, `radial-gradient(130% 120% at 100% -10%, …)`. It is the only
  gradient in the system that is allowed to be atmospheric.
- **Panel Glass** (`{colors.panel}`) with **Panel Hairline** (`{colors.panel-border}`):
  every surface. Layering more glass on glass is how depth is built.
- **Paper Ink** (`{colors.ink}`): all primary text — a violet-tinted white, never
  pure `#fff` except on the active nav item.
- **Dusk Muted** (`{colors.muted}`): labels, secondary text, unselected controls,
  and — deliberately — the entry price line on charts, so entry reads as a
  reference rather than an outcome.

### Named Rules

**The Now-and-You Rule.** Cyan means *now*; violet means *you*. A cyan element that
does not change with time is a bug, and a violet element that reports a market fact
is a bug. Outcomes are green/rose only.

**The One Glow Rule.** The cyan live dot (`shadow-glow`, `0 0 8px` cyan) is the only
light-emitting element in the interface. Its rarity is what makes it read as a
heartbeat. Do not glow a second thing.

**The One Palette Rule.** Every colour in the interface comes from
`frontend/src/lib/theme.ts`. `tailwind.config.ts` reads that file for its class
tokens, and canvas/SVG code — which Tailwind cannot reach — imports `palette`,
`tint()`, and `white()` from it. A raw `bg-slate-900`, `text-rose-400`, or pasted
`#hex` in a component is a defect: it is a colour a light theme cannot find.

**The Honest Absence Rule.** Missing data is coloured, not hidden: rose for a hole
that could be filled, slate for a market closure that never had bars. Never render
a continuous chart across either.

## Typography

**Display / Body Font:** Inter (with `system-ui`, `sans-serif`)
**Mono Font:** SF Mono (with `ui-monospace`, `monospace`) — identifiers, symbols, CLI
command names quoted inside sentences.

**Character:** One neutral grotesque doing every job, separated by size and weight
rather than by family. The personality comes from the extremes — 9.5px letterspaced
labels against 23px tabular numerals — not from a display face. Type here is
instrumentation labelling.

### Hierarchy
- **Display** (700, 23px, tabular, `-0.01em`): KPI values only. The largest thing on
  any page is always a number.
- **Headline** (700, 18px, `-0.01em`): the page title, once per route.
- **Title** (600, 13px): panel headings. Often paired with an 11px muted subtitle
  that states the unit or the method (`rata-rata R · win rate`).
- **Body** (400, 12–13px): table cells, values, prose. 13px is the reading size,
  12px the dense-table size.
- **Label** (400, 9.5–11px, uppercase, `0.09em`): KPI captions and section eyebrows.
  Always muted, always the smallest thing in the panel.

### Named Rules

**The Tabular Rule.** Every figure carries `font-variant-numeric: tabular-nums` via
`.num`. A number that shifts its own column while polling is unreadable.

**The Solid Numeral Rule.** Numerals are never gradient, never semi-transparent,
never given a text shadow. Contrast belongs to data.

**The n-Beside-It Rule.** A statistic is set with its sample size adjacent in muted
11px (`n=42`, `12W · 7L · 3BE`). The pairing is typographic, not optional: never
style an `n` so small or so faint that the figure reads as unqualified.

## Layout

A two-column shell: a fixed **186px** navigation rail on the left (`border-r`
hairline, hidden below `md` / 768px) and a scrolling main column padded **20px**,
rising to **24px** at `md`. The shell is `min-h-screen` and the main column clips
overflow so the chart canvas never pushes the page sideways.

Inside main, content is a stack of grids with a **12–14px** gutter:
- KPI strip: `grid-cols-2` on phones, `lg:grid-cols-4` (1024px) on the desk.
- Primary/secondary split: single column on phones, `lg:grid-cols-[1.55fr_1fr]` —
  the wide slot always holds the time-series, the narrow slot the breakdown.
- Tables and long lists run full width and scroll inside their own panel.

Panel padding scales with importance, not with viewport: 12px for inline utility
strips, 14px for KPI cards, 16px for standard panels, 20–24px for a page-level empty
or error state. Vertical rhythm is 4px-based; the recurring intervals are 4, 8, 12,
16, 20.

The target is a laptop screen at ~1440px running one window beside MT5. Two
breakpoints do all the work: `md` (768px) restores the nav rail, `lg` (1024px)
restores the multi-column grids. Below `md` the rail disappears entirely and every
grid collapses to one column — read-only surfaces must stay legible there, since the
journal gets checked from a phone.

### Named Rules

**The One Window Rule.** Every route must be complete and readable at 1440×900 with
no horizontal scroll. If a panel only works on a wide monitor, it is the panel that
is wrong.

**The Rail-Optional Rule.** The 186px rail is chrome, not content. Nothing may be
reachable only through it; below 768px it is gone and every route must still stand
on its own heading.

## Elevation & Depth

The resting interface is flat. Depth is built from three stacked cues and no
shadows: translucency (4.5% white on the dusk ground), a 1px 9%-white hairline, and
an 8px `backdrop-filter: blur()`. Panels nested inside panels step up with a plain
5–6% white fill and no border — the ring belongs to the outer surface.

Floating layers may lift. Modals, drawers, and the chart's tool palette are allowed a
soft shadow to separate them from the canvas below, and modals darken the page with
`rgba(0,0,0,0.6)`. This is the one place elevation is real; resting surfaces stay
flat.

### Shadow Vocabulary
- **Live glow** (`box-shadow: 0 0 8px #22d3ee`): the live/fresh status dot only. See
  The One Glow Rule.
- **Floating layer** (`box-shadow: 0 12px 32px rgba(0,0,0,0.45)`): modals, drawers,
  and the drawing palette over a chart.
- **Scrim** (`background: rgba(0,0,0,0.6)`): the full-page backdrop behind a modal.

### Named Rules

**The Flat-At-Rest Rule.** A surface that sits in the page flow has no shadow. Only
a layer that floats *over* the page or over the chart canvas may cast one.

**The Hairline Rule.** Separation is a 1px `{colors.panel-border}` line, never a
heavier stroke and never a solid divider colour. If two panels need more separation
than a hairline, they need more space instead.

## Motion

The interface is almost entirely still. There is no entrance animation, no scroll
reveal, no hover lift, and no motion that marks a win differently from a loss —
the same unsentimentality that governs colour governs movement. Motion is a
sentence with one meaning: **something is happening right now.**

Three things move, and nothing else may:

- **Spinners** (`animate-spin`) while a bridge or backfill call is in flight. Every
  one sits beside a label that already names the work; the wheel is the second
  channel, never the only one.
- **Skeletons** (`animate-pulse`) before the numbers exist, so an empty panel is
  never mistaken for a loaded one with nothing in it.
- **The sheet** (`animate-sheet-in`, 240ms, `cubic-bezier(0.16, 1, 0.3, 1)`) sliding
  in from the right edge. It is the only element in the system that is somewhere
  else before it exists, so it is the only one that gets to travel. Entrance only.

State changes are colour, not movement: `transition-colors` on hover and selection,
and a 1px `translateY` press on buttons with no transition at all. Nothing in the
system animates a layout property.

### Named Rules

**The Only-Now Rule.** If an element moves, something must be in flight *at that
moment*. A rose bar that pulses because the number is bad is decoration — rose
already means "look", and the pulse adds nothing a second glance would not.

**The Reduced-Motion Rule.** `prefers-reduced-motion: reduce` stops every loop
globally in `index.css`, but the *meaning* survives without the movement: a stopped
spinner drops to 50% so it does not read as hung, and a stopped skeleton drops to
55% so it still reads as provisional. Killing an animation is not enough — whatever
that animation was saying has to be said another way.

## Shapes

Four radii, each with a job: **4px** for inline controls, chips, and text inputs;
**8px** for navigation items and inner tinted blocks; **12px** for larger nested
containers; **14px** for the glass panel itself. Status indicators are full pills
(`9999px`) and status dots are 6px circles.

Form language is rectangular and calm — no clipping, no angled cuts, no decorative
borders. Interactive tinting is done with a translucent fill plus a `ring-1` of the
same hue at a higher alpha (fill ~15–25%, ring ~25–45%); this ring-over-tint pair is
the system's single mechanism for "this is active/selected/accented", used
identically on buttons, pills, chips, and the active nav item. Icons are text
glyphs (`⌖ ╱ ─ ▭ T ⚙ ▶`) at the control's own font size, not an icon library.

## Components

### Buttons
- **Shape:** 4px on committing actions, 14px when the button is itself a glass slab.
- **Glass (default):** the panel surface, muted text, no fill. Hover brightens text
  to ink. This is the workhorse — toolbar controls, timeframe switches, "Ke sekarang".
- **Commit (cyan):** 20% cyan fill, 45% cyan ring, ink text, semibold. The button
  that queues a real command against the account.
- **Mine (violet):** 20% violet fill, 40% violet ring. Adding the user's own data —
  tags, annotations, drawings.
- **Segmented group:** buttons share one glass slab with `overflow-hidden`; the
  selected member takes a 25% violet fill and ink text, the rest stay muted.
- **Disabled:** `opacity: 0.4`, no colour change. Never grey out by swapping hue.
- **Hover / Focus / Press:** the target character is *tactile and responsive*.
  Colour transitions run at 150ms; add a press state (`:active` translate of 1px or a
  fill step) and a visible `:focus-visible` ring at `{colors.cyan}` on every control.
  Today most controls transition colour only — treat press and focus as required on
  anything new.

### Chips
- **Mine (manual):** 15% violet fill, violet text, 4px radius, an inline `×` that
  turns rose on hover.
- **Derived (auto):** 6% white fill, muted text, no affordance. The visual difference
  between a fact the user asserted and a fact the rebuild derived is carried entirely
  by this colour pair, and must never be flattened.

### Cards / Containers
- **Corner:** 14px glass panel.
- **Background:** `{colors.panel}` over the fixed dusk ground, 8px backdrop blur.
- **Shadow:** none at rest (see Elevation).
- **Border:** 1px `{colors.panel-border}`.
- **Padding:** 14–16px; 12px for utility strips.
- **Anatomy:** 13px semibold title → optional 11px muted method line → 12px content.

### Inputs / Fields
- **Style:** 5% white fill, no border, 4px radius, 4px/8px padding, ink text.
- **Focus:** currently unstyled beyond the browser default — needs the cyan
  `:focus-visible` ring named above.
- **Selects:** rendered as a glass slab with a transparent background; options carry
  `bg-bg` so the native dropdown stays inside the world.
- **Error:** rose 12px text below the field, prefixed with the refusal
  (`Ditolak: …`). The field itself is not recoloured.

### Navigation
- **Rail:** 186px, hairline right border, 16px padding, a 6px violet→cyan gradient
  square beside the wordmark.
- **Item:** 13px, muted, 8px radius, brightens to ink on hover.
- **Active:** white text over a left-to-right `violet/25 → cyan/5` gradient with an
  inset `ring-1 ring-violet/35`. The only place the two accents are mixed, and the
  only place pure white text appears.
- **Below 768px:** the rail is not rendered.

### Status Pill (signature)
The system's most repeated element and the reason it reads as an instrument. A full
pill at 11px containing a 6px dot and a terse state string, in one of three tints:
- **Now** — cyan text, 10% cyan fill, 25% cyan ring, and the glowing dot: `live · 3s`.
- **Wrong** — rose text, 10% rose fill, 25% rose ring, flat dot: `basi · 214s`.
- **Absent** — muted text on 5% white, no ring, no dot: nothing is running.

The pill always states the machine-readable reason next to the state — an age in
seconds, a percentage, a count — never a bare word like "OK".

It ships as `components/StatusPill.tsx` (`tone="now" | "wrong" | "absent"`), and
`LiveDot`, `StalenessBadge`, and the dashboard header all render through it. A
surface that needs the same three tints in a different shape — `LabBadge`'s
multi-line card — imports `TONE_TINT` and supplies its own shape, so the
vocabulary stays in one file even when the geometry does not.

### Chart Canvas (signature)
The chart is transparent-backed and inherits the page ground, so panels and canvas
are one surface: grid `rgba(255,255,255,0.06)`, axis border
`{colors.panel-border}`, text `{colors.muted}`, up `{colors.pos}`, down
`{colors.neg}`. Price lines carry fixed meanings: entry muted, SL rose, TP green,
exit amber. Overlays (measurement, drawings, coverage shading) are SVG in the page's
own colours drawn over the canvas, and the tool palette floats at the pane's left
edge as a 7×7px-per-button glass column.

A second, light chart theme exists (`{colors.chart-light-bg}` ground,
`{colors.chart-light-ink}` text, `{colors.chart-light-pos}` /
`{colors.chart-light-neg}` candles) and is selectable in chart settings. It is the
seed of a future app-wide light mode: keep it maintained, keep its up/down pair
perceptually equivalent to the dark pair, and when new surfaces hard-code a colour,
hard-code it as a token so that light mode remains addable.

## Do's and Don'ts

### Do:
- **Do** reserve cyan for values that change with time and violet for the user's own
  marks (The Now-and-You Rule).
- **Do** set every figure in tabular numerals via `.num`, at full opacity, in a solid
  colour.
- **Do** place the sample size beside every statistic in muted 11px, and colour a
  suppressed low-`n` bucket muted rather than hiding it.
- **Do** build depth from translucency, one hairline, and blur — and reserve real
  shadow for layers that float over the page or the chart.
- **Do** tint-plus-ring for any active state (fill 15–25%, ring 25–45% of the same
  hue), so accents stay consistent across buttons, chips, pills, and nav.
- **Do** state absence in colour and words: rose for a fillable hole, slate for a
  market closure, muted for "nothing is running".
- **Do** keep every route complete at 1440×900 in one window, and legible in one
  column below 768px.
- **Do** give new controls a `:focus-visible` cyan ring and an `:active` press state;
  controls should feel operated, not merely recoloured.

### Don't:
- **Don't** add a second glowing element. One heartbeat, one glow.
- **Don't** use green or rose for anything but outcome — no green "saved", no rose
  "required field" that is not actually wrong.
- **Don't** introduce a display typeface, an icon library, or a decorative gradient.
  The dusk ground is the only atmospheric gradient; the nav-active gradient is the
  only functional one.
- **Don't** dress the interface as broker terminal chrome: no bevels, no gradient tab
  strips, no dense grey toolbars, no icon soup.
- **Don't** drift toward a generic SaaS dashboard: no `rounded-2xl` white cards, no
  blue-600 primary buttons, no cheerful empty-state illustrations.
- **Don't** drift toward AI-startup dark mode either: no animated aurora, no glowing
  gradient borders, no glassmorphism used as spectacle. The glass here is 4.5% white
  and one hairline — that is the whole trick, and it stays quiet.
- **Don't** grow padding or type to fill a wide screen. Density is the point.
- **Don't** hard-code a colour that a future light palette would have to hunt for;
  reach for a token.
