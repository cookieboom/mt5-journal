# Frontend Rework → React SPA (design)

**Date:** 2026-07-23
**Status:** Approved (design); pending spec review
**Branch:** `claude/frontend-react-rework`
**Scope:** Presentation layer only. Replace the Jinja2 server-rendered UI with a
React single-page app served by the existing FastAPI process. No change to any
Python domain logic, data model, or trading behaviour.

---

## 1. Goal

The current UI (`journal serve`, FastAPI + Jinja2, one `app.css`) works but reads
as dated and under-informative. Rework it into a modern, information-dense
dashboard while keeping the app **100% local, single-user, one-command**.

Two user complaints drive this:
1. *"kurang modern"* → a deliberate visual system (dark, glass, violet/cyan
   accents), higher-fidelity charts, clear hierarchy.
2. *"kurang informatif"* → visualise analytics that already exist (equity/R
   curve, per-symbol/session breakdowns as charts, live P&L, daily calendar)
   instead of text tables.

**Non-goals / hard boundaries:**
- No new trade signals or recommendations (CLAUDE.md rule 9). Every new view
  describes *past* data.
- No MT5 import or constants outside `adapter/` (rules 1, 12) — the web layer
  never touches the bridge; unchanged here.
- No change to `deals_raw`/`orders_raw`, `trades` reconstruction, analytics
  math, or the money-unit rule (all figures are `accounts.currency` = USC).
- Do not weaken the M9 trade-command safety model (two-step confirm, 1.00-lot
  cap, asymmetric rules, loopback-only bind).

---

## 2. Architecture

One process, unchanged entry point:

```
journal serve  →  FastAPI :8000  (loopback-only bind, as today)
                    ├─ /api/*                    → JSON (new: web/api.py)
                    ├─ /trades/{id}/chart.png     → mplfinance PNG (reused as-is)
                    ├─ /static/*                  → chart cache + assets
                    └─ /*  (or /app/* in transit) → React build (SPA index.html)
```

- **`web/api.py` (new):** a thin FastAPI router returning JSON. Each endpoint is
  a wrapper over an existing `web/views.py` context builder (they already return
  plain dicts) — **no new business logic lives in the API layer.**
- **`web/views.py` reused.** Where a builder currently mixes template-only shape,
  keep the JSON payload clean; if a builder needs a JSON-friendly variant, add a
  sibling function rather than editing the tested one.
- **Serving the SPA:** FastAPI mounts the built assets and serves `index.html`
  as the catch-all for client-side routes. `/api/*`, `/static/*`, and the chart
  PNG route take precedence.
- **Loopback-only** binding is preserved (M9 security note); nothing new listens
  on the network.

### 2.1 JSON contract

- **Raw numbers, not pre-formatted strings.** JSON carries numeric values plus a
  `currency` field (`"USC"`); the client formats. R-multiple is unit-free.
- **Sample-size honesty travels in the payload.** Any bucketed/averaged stat
  arrives with its count(s) and its average as JSON `null` when withheld; the
  client greys or suppresses it. Mirrors `fmt.is_gated` / docs §8.
  **Implementation note (Phase 1):** gating is computed *client-side* by
  `frontend/src/lib/format.ts:isGated`, a faithful mirror of
  `web/format.py:is_gated` (the same client-mirrors-server pattern `format.ts`
  uses for the money/R formatters), gating each stat on its *relevant* count —
  e.g. the per-symbol R value gates on `n_with_r`, not the closed-trade `n`. The
  originally-proposed explicit server `gated: bool` flag was not added, because
  the analytics layer already encodes gating as `avg = None` and the client
  mirror suffices for the Dashboard. **Phase 4** (the `/report` page, with much
  denser bucket tables) must re-evaluate whether to surface an explicit
  server-computed `gated` flag per bucket rather than relying on the client
  heuristic — decided there, not inherited silently.
- **Timestamps** are epoch-ms UTC (rule 3); the client converts to WIB for
  display only.
- **NULL vs 0** distinction (rule 4, esp. `sl_initial`/`tp_initial`) is preserved
  as JSON `null` vs `0`; the client renders "unknown" vs "none set" distinctly
  and excludes `null`-SL trades from R stats exactly as the server already does.

---

## 3. Frontend

- **Stack:** Vite + React 18 + TypeScript + Tailwind CSS + shadcn/ui + Recharts.
  Source in a new top-level **`frontend/`**. Build output is served by FastAPI;
  **the runtime never needs Node** — only the build step does. Node 24 (nvm) is
  already on the machine.
- **Routing:** React Router; routes mirror the pages
  (`/`, `/live`, `/trades`, `/trades/:id`, `/report`, `/weekly/:week?`,
  `/commands`).
- **App shell:** left sidebar nav + top bar (account chip, live/staleness
  indicator). Responsive: sidebar collapses under ~820px.

### 3.1 Visual system (direction "C", approved)

- Dark base `#0b0a1a` with a radial violet wash; glass panels
  (`rgba(255,255,255,.045)` + hairline border + blur).
- Accents: violet `#a78bfa` → cyan `#22d3ee` gradient for logo, active nav,
  chart fills/strokes, progress bars, live pulse.
- **Data numerals are solid, high-contrast, tabular** — gradients/glow are
  accent only, never applied to figures the user must read. P&L green
  `#34d399` / red `#fb7185`.
- Charts follow the `dataviz` skill (consistent palette, light/dark legibility,
  accessible; tooltips/axes/legends per its rules).

### 3.2 Pages & key components

- **Dashboard `/`:** KPI hero row (Net R, win rate, expectancy, floating P&L —
  each with `n`), cumulative-R area chart, per-symbol contribution bars + win
  rate, recent-trades table.
- **Live `/live`:** open positions with per-position floating P&L, total float,
  staleness badge, "updated Ns ago"; per-position action buttons (modify SL/TP,
  close, partial close, add volume) → two-step confirm modal (§4).
- **Trades `/trades`:** filterable list (symbol/status/source), sortable, links
  to detail. **Trade detail `/trades/:id`:** trade facts, annotate form
  (setup/confidence/emotion/followed-plan/notes), tag add/remove, chart PNG,
  NULL-aware fields with tooltips.
- **Report `/report`:** the M8 analytics tables as charts — money, MAE/MFE,
  by-session/by-source/by-symbol; plus R-multiple histogram and MAE/MFE scatter.
- **Weekly `/weekly/:week?`:** weekly report; `/weekly` redirects to last
  complete ISO week (as today).
- **Commands `/commands`:** trade-command audit log.
- **New (existing data):** daily P&L calendar heatmap; win/loss streaks &
  drawdown readout. Anything needing *new* computation is out of scope for now
  and noted as a future item, not built speculatively.

---

## 4. Live trade actions — safety-critical, unchanged semantics

The mandatory **two-step confirm** is preserved exactly:

1. User fills SL/TP/volume and hits an action → `POST /api/live/{position_id}/{action}/preview`.
   **Writes nothing.** Returns a validated preview or a `CommandError`.
2. React shows a confirm modal with the preview.
3. On confirm → `POST /api/live/{position_id}/{action}` → `execute.enqueue`
   writes a single `pending` row. `journal live` (the bridge owner) picks it up
   and executes.

- Server-side validation (`CommandError`, 1.00-lot cap, asymmetric safety rules,
  the `""` vs `"0"` field distinction via `views._opt_float`) is **unchanged**.
  React cannot bypass it — `enqueue` re-validates on the server, so client state
  is never trusted. No client-only gating of dangerous actions.
- The web process still never calls the bridge. Enqueue → SQLite → `journal
  live` remains the only path to a real order.

---

## 5. Real-time

- Client **polls `/api/live` every ~2.5 s** (and lighter polling on Dashboard
  for floating P&L / "updated Ns ago"). Simple, robust, no WebSocket infra.
- Staleness uses the existing `views.live_context` logic (surfaced in JSON); the
  client shows a warning badge when data is stale.
- SSE/push is a possible later upgrade; not built now (thin benefit for a local
  single-user tool).

---

## 6. Delivery — incremental, React alongside Jinja

Lowest-risk path for a live-trading app on a real account. During transition the
SPA is served at **`/app`**; the legacy Jinja UI stays reachable at its current
paths until parity. Each phase turns one page real; the final phase flips `/` to
the SPA and retires Jinja.

| Phase | Deliverable | Review gate |
|---|---|---|
| 0 | Scaffold `frontend/` (Vite+React+TS+Tailwind+shadcn), design tokens, app shell; `web/api.py` skeleton; FastAPI serves the build at `/app` | build runs, shell renders, tests green |
| 1 | **Dashboard** page + `/api/dashboard` (KPI, cumulative-R, per-symbol, recent) | visual + data parity |
| 2 | **Live** page + `/api/live` + preview/enqueue endpoints + two-step confirm modal + polling | **highest stakes** — verify confirm flow & validation end-to-end |
| 3 | **Trades** list+filters & **Trade detail** (annotate/tags, chart) + APIs | write-path parity (annotate/tag) |
| 4 | **Report** + **Weekly** + **Commands** + calendar heatmap, R-histogram, MAE/MFE scatter + APIs | analytics parity |
| 5 | **Cutover:** serve SPA at `/`, remove Jinja templates + unused Jinja plumbing, update `CLAUDE.md`/docs | full parity, all tests green |

Every phase: user reviews; the existing 378 tests stay green; `journal rebuild`
still succeeds.

---

## 7. Dependencies (CLAUDE.md rule 8)

- **Python runtime deps: unchanged** (python 3.12, sqlite3, pandas, mplfinance,
  typer, fastapi, uvicorn).
- **New build-time (Node) deps:** React, Vite, TypeScript, Tailwind, shadcn/ui,
  Recharts, plus Vitest for tests. Confined to `frontend/`; not required to run
  the shipped app. Approved by the user when selecting the Vite+React stack.

---

## 8. Testing / Definition of done

- **API:** pytest with FastAPI `TestClient` over the fake adapter + fixtures (no
  live MT5). Each endpoint asserts shape, units, `n`/`gated` flags, and
  NULL-vs-0 fidelity.
- **Frontend:** Vitest for pure logic — the money/R formatters, `gated` greying,
  WIB conversion, and the two-step confirm state machine.
- **Regression:** all existing tests pass; `journal rebuild` succeeds; a live
  smoke of the Live page confirm flow before cutover (Phase 2 & 5).
- Done = tests pass **with pasted output**, `journal rebuild` OK, page verified
  in the running app — not "looks right".

---

## 9. Risks & mitigations

- **Live page regressions touch real money** → built in its own phase with an
  end-to-end confirm-flow verification before proceeding; server validation
  untouched; two-terminal (`journal live` + `journal serve`) coexistence already
  proven (WAL + busy_timeout).
- **Formatter divergence (server vs client)** → JSON carries raw values + units +
  `gated`; a single TS formatter module mirrors `format.py`, unit-tested against
  the same cases.
- **Serving/routing regressions during transition** → SPA isolated at `/app`;
  legacy UI untouched until the explicit Phase-5 cutover.
- **Build toolchain drift** → `frontend/` is self-contained; the Python runtime
  and `journal serve` never depend on Node at run time.

---

## 10. Open decisions (defaulted, revisit if needed)

- JSON sends raw numbers, client formats — **chosen.**
- Recharts for charts — **chosen** (revisit if `dataviz` favors another lib).
- SPA at `/app` during transition, `/` at cutover — **chosen.**
