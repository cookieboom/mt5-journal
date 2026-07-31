# Advanced SL/TP Management — Design

Status: brainstormed, awaiting spec approval → plan
Supersedes: the `feature/advanced-sltp-management` branch built by another AI tool
("Kiro CLI") on 2026-07-28, scrapped 2026-07-31 (see
`sltp-kiro-scrap-2026-07-31` in claude-mem project memory). That branch was
technically correct (pytest/tsc/vitest/rebuild all green) but never went
through this project's brainstorm → spec → plan → SDD process, and only
covered the training/replay side. This design redoes the process and expands
scope to cover live positions too.

## Problem

The project has two separate SL/TP systems:

1. **Training/replay** (fake positions, `training_positions` table) — no way
   to adjust SL/TP after opening a position mid-replay; the Kiro branch added
   drag-on-chart for this.
2. **Live** (real positions, real broker orders via `trade_commands` queue) —
   SL/TP can only be changed today through a plain number form on
   `LivePositionCard` (type a number, click "Ubah SL/TP…").

Both suffer from the same UX gap: no direct, visual way to drag SL/TP lines
on the chart itself, even though the chart already renders SL/TP/entry as
price lines (Phase B, Phase D `overlayLines`).

## Scope

**In scope:** drag-to-set SL/TP on chart, for BOTH live and replay positions,
sharing the same `CandleChart` component. Double-click a SL/TP line to
remove it. Session hit-rate stats (SL hit / TP hit / manual close) — training
only for now.

**Out of scope:** opening new positions via chart interaction (only
modifying SL/TP of already-open positions); extending hit-rate analytics to
real trade history (separate future project — most historical trades have
`sl_initial = NULL`, rule 4); any change to how positions are opened or
closed (Close / Tutup sebagian / Tambah buttons on `LivePositionCard` are
untouched).

## Architecture

`CandleChart` owns only the **gesture mechanics** (hit-test a SL/TP/entry
price line, drag, ghost line, double-click-to-remove) and knows nothing
about live vs. replay. It exposes:

```
draggablePositions?: DraggablePosition[]   // { id, direction, entry_price, sl, tp }
onSlTpChange?(positionId: number, change: { sl?: number; tp?: number }) => void
```

`change.sl === 0` / `change.tp === 0` means "remove" (matches the existing
rule-4 convention already used by the live form: empty = leave unchanged, 0 =
remove).

Each consuming page supplies its own commit semantics:

- **Replay (`Chart.tsx`, training mode):** `onSlTpChange` calls
  `training.modify_sltp` (PATCH `/api/training/positions/{id}/sltp`)
  immediately — no confirmation, matches existing training UX (fake money).
- **Live page (`Live.tsx`):** `Live.tsx` already runs every action (Close /
  Tutup sebagian / Tambah / the current SL/TP form) through a two-step
  preview→confirm flow: `onAction` POSTs `/api/live/{id}/{action}/preview`
  (server re-validates, writes nothing) → the existing generic
  `ConfirmModal.tsx` shows a read-only summary (`preview.intent`) → confirming
  POSTs the real enqueue. `ConfirmModal` has no editable fields — it's reused
  as-is by every action kind and must stay that way.
  `onSlTpChange` therefore opens a **new, small precision-edit dialog first**
  (`SltpConfirmDialog.tsx`) — pre-filled with the dragged price in an
  **editable** number input (drag is pixel-imprecise; typing corrects it).
  Confirming it calls the *same* `onAction(pos.position_id, "sltp", { sl, tp })`
  the old form used, which then runs the existing preview→`ConfirmModal`→
  enqueue pipeline unchanged. Net effect for the user: drag → small precision
  popup → existing generic confirm → sent. Cancelling the precision popup
  never calls `onAction` at all (no preview fetched, matches "writes nothing
  until confirmed").
  The plain-number SL/TP inputs + "Ubah SL/TP…" button are **removed** from
  `LivePositionCard`; Close / Tutup sebagian / Tambah stay as-is.
- Double-click on a SL/TP line on a **live** position also opens
  `SltpConfirmDialog` (pre-filled with `sl: 0` or `tp: 0`, copy: "Hapus SL?
  Posisi jadi tanpa stop-loss."), then the same existing `ConfirmModal` step.

This mirrors the existing pattern in the codebase (Spec B's `measure.ts`
gesture logic lives in the shared component, business semantics live in the
page) rather than hard-coding one consumer's behavior into `CandleChart`
(the mistake in Kiro's version).

No visual live/replay distinction beyond the confirmation dialog was
requested (user's call — the dialog is judged sufficient).

## Components

- **`frontend/src/lib/sltpDrag.ts`** (new, pure) — hit-testing math (price↔
  coordinate, 8px threshold), ghost-line price/title calculation,
  direction-aware SL vs TP target resolution. Pattern-matches `measure.ts`.
- **`CandleChart.tsx`** (extended) — renders draggable price lines via the
  existing `priceLines` ref + `createPriceLine`/`removePriceLine` machinery;
  new pointer handling for drag-a-position-line, coordinated with the
  existing Spec-B measure-gesture pointer handlers on the same node (a
  pointerdown that hits a SL/TP/entry line takes drag-position priority; only
  falls through to measure-gesture if it doesn't hit a line).
- **`SltpConfirmDialog.tsx`** (new) — live-only precision-edit dialog
  (editable price field, Confirm/Cancel), sits *in front of* the existing
  generic `ConfirmModal` (untouched) rather than replacing or extending it.
- **`training.py`** — **does not currently exist on `main`.** The Kiro branch
  (`feature/advanced-sltp-management`) had a technically-correct
  `modify_sltp`/`get_session_stats`/migration 008, but that whole branch was
  scrapped and `main` never merged it — so this is net-new work on `main`,
  built via this project's TDD process, using the Kiro version as a verified
  reference (not a blind rewrite; its logic was already checked and is
  reused verbatim except for the fix below). **New addition this round:** direction-sanity
  validation (buy: `sl < entry_price < tp`; sell: reversed), currently
  missing in both `modify_sltp` and the older `open_position` — fixed in
  both for consistency. Validation only applies to values actually being
  set: `sl`/`tp` of `0` (remove) or `None` (leave unchanged, `modify_sltp`
  only) are exempt; when only one side is being changed, the check uses the
  *other* side's currently-stored value (0/unset on that side skips that
  half of the check — there's nothing to be inconsistent with).
- **`domain/commands.py`** (live, existing) — untouched. Already validates
  `stops_level` and handles `modify_sltp` via the bridge-mediated command
  queue.

## Data Flow

**Replay:** drag release → `CandleChart` calls `onSlTpChange` → `Chart.tsx`
→ `training.modify_sltp` (direct DB write, direction-validated) → session
state refreshed via `useReplaySession`.

**Live:** drag release → `CandleChart` calls `onSlTpChange` → Live page opens
`SltpConfirmDialog` (pre-filled, editable) → user confirms → `onAction("sltp",
{sl, tp})` → **existing** preview fetch (`/api/live/{id}/sltp/preview`) →
**existing** `ConfirmModal` (summary text, second confirm) → **existing**
enqueue POST → `trade_commands` → `journal live` drains queue → bridge sends
order to broker → next poll reflects the change on `LivePositionCard`/chart.

## Error Handling

- **Replay:** `ValueError` (bad direction, position not found/closed) → HTTP
  400 → inline error message on the training page (existing pattern in that
  page, not new infrastructure).
- **Live:** `CommandError` (e.g. `stops_level` violation) → HTTP 400 → same
  toast/error path already used by Close / Tutup sebagian / Tambah — no new
  error infrastructure needed.

## Testing Strategy

- **Unit:** `sltpDrag.ts` pure functions (hit-test, ghost price, direction
  resolution) — vitest, no DOM.
- **Component:** `CandleChart` drag-a-line → `onSlTpChange` called with
  correct value; double-click → remove value; drag-a-line does NOT trigger
  the Spec-B measure gesture and vice versa.
- **Backend (TDD, RED first):** new direction-sanity validation tests in
  `test_sltp_modification.py` (buy/sell × SL-wrong-side/TP-wrong-side) added
  to the existing 13-test file (reused, not rewritten) before implementing
  the guard clause.
- **Live-specific component test:** `SltpConfirmDialog` — editing the
  pre-filled price before confirming sends the *edited* value, not the
  original drag value.
- **Gate before any commit:** full pytest + full vitest + `tsc --noEmit` +
  `journal rebuild` (migration 008 only adds `training_session_stats`, which
  `journal rebuild` never touches — rule 6 unaffected).
- **PENDING HUMAN (cannot be automated):** live drag → broker round-trip
  smoke test with the MT5 bridge container running, confirming the order is
  actually sent and `stops_level` is honored end-to-end — same category as
  every prior chart-phase visual pass in this project.

## Open Questions / Decisions Log

- Scope: BOTH live and replay, shared chart (user's choice, not Kiro's
  training-only scope).
- Live commit: confirmation dialog with editable price (not instant, not
  optimistic-undo).
- Live form: removed entirely, replaced by drag (no fallback numeric input
  kept).
- Live double-click-remove: allowed, with stronger confirm copy.
- Hit-rate stats: training/replay only, not extended to real trade history
  (separate future project).
- No visual live/replay chart distinction beyond the confirm dialog.
