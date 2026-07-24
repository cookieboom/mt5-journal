# Frontend Rework → React SPA — Phase 5 (Cutover) design

**Date:** 2026-07-24
**Status:** Approved (design)
**Branch:** `claude/frontend-react-rework-phase-5`
**Scope:** Presentation-layer cutover only. Move the React SPA from its
transition path `/app` to `/`, retire the legacy Jinja2 UI and its now-unused
plumbing, and fold in six deferred Phase-4 cleanups. No change to any Python
domain logic, data model, analytics math, or trading behaviour.

This is the final phase of the Jinja→React rework (spec
`2026-07-23-frontend-react-rework-design.md`, §6 row 5). Phases 0–4 are merged to
`main`; the SPA has been live at `/app` alongside the Jinja UI. This phase flips
`/` to the SPA and removes Jinja.

---

## 1. Goal & gate

**Goal:** the React SPA is the sole UI, served at `/`, with the Jinja templates
and their exclusive plumbing removed. **Gate: full parity, all tests green.**

Full parity is already achieved page-for-page by Phases 1–4 (Dashboard, Live,
Trades+detail, Report, Weekly, Commands, the three analytics charts). This phase
adds **no new user-facing feature** — it changes *where* the SPA is served and
*deletes* the superseded Jinja layer.

**Non-goals / hard boundaries (unchanged from the parent spec):**
- No MT5 import or constants in `web/` (CLAUDE.md rules 1, 12). Untouched here.
- No change to `deals_raw`/`orders_raw`, `trades` reconstruction, analytics math,
  or the money-unit rule (every figure is `accounts.currency` = USC).
- NULL ≠ 0 (rule 4) preserved end-to-end; R-multiple stays unit-free.
- Timestamps are broker server-time epoch-ms UTC (rule 3); WIB = UTC+7 display
  only, client-side.
- Charts are cache, reproducible from the DB (rule 6) — the `chart.png` endpoint
  stays.
- No new dependency (rule 8). One PR to `main`.

---

## 2. Serving change — SPA at `/` (`src/journal/web/app.py`)

Decision (recorded with the user): **drop `/app` entirely.** No redirect, no
dual-serve. The SPA has existed at `/app` for ~1 day, single user; and once the
SPA serves at `/`, every real page URL (`/`, `/live`, `/trades`, `/trades/{id}`,
`/report`, `/weekly`, `/weekly/{week}`, `/commands`) resolves automatically as a
React Router route, so URL parity is free. Only the transition-only `/app/*`
prefix is orphaned, and it is deleted.

Changes:
- **Mount built assets at `/assets`** (was `/app/assets`), guarded by the
  existing `_FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "assets").is_dir()`
  check so the app still boots before `npm run build` has produced `dist/`.
- **Add an SPA catch-all** that returns `index.html` for any path not already
  handled. It is registered **last**, after every `/api/*` route, the
  `/trades/{id}/chart.png` route, and the `/assets` mount, so it never shadows a
  data route. Implementation: a `GET /{full_path:path}` returning the cached
  `index.html`, present only inside the `_FRONTEND_DIST` guard.
- **Pre-build fallback:** when `dist/` is absent, `/` returns a plain-text/HTML
  note telling the operator to run `npm --prefix frontend run build` (replacing
  today's Jinja dashboard at `/`). No Jinja, no template engine.
- **Delete** the `/app` asset mount, the `/app` and `/app/{full_path}` routes.

**Route-precedence invariant (test-guarded):** `/api/dashboard` must return JSON
and `/trades/{id}/chart.png` must return PNG bytes — never `index.html`. FastAPI
matches routes in declaration order and a mounted `StaticFiles`/explicit path
beats a later catch-all, but this is the one real hazard of the phase, so it is
asserted directly (see §7).

---

## 3. Retirement — legacy Jinja UI + exclusive plumbing

Every deletion is grep-verified for remaining consumers first, then `pytest` is
run before moving on (CLAUDE.md discipline: verify each removal).

**Templates & static:**
- Delete `src/journal/web/templates/` (all 10: `base.html`, `dashboard.html`,
  `report.html`, `trades.html`, `trade_detail.html`, `weekly.html`, `live.html`,
  `commands.html`, `confirm.html`, `error.html`).
- Delete `src/journal/web/static/app.css` and the `app.mount("/static", …)`
  mount. (The SPA serves its own CSS from `/assets`; `/static` served only the
  Jinja stylesheet — grep-verified no other consumer.)

**Plumbing in `app.py`:**
- Delete `render()` and `error_page()` helpers.
- Delete the `Jinja2Templates` construction and **all** `templates.env.filters`
  / `templates.env.globals` wiring.
- Remove now-unused imports (`Jinja2Templates`; and `Form`, `Request`,
  `HTMLResponse`, `RedirectResponse`, etc. **only where a grep confirms no
  remaining use** — some may still be needed by kept routes; check, don't assume).

**Jinja routes (HTML pages — 8):** `GET /`, `/report`, `/trades`,
`/trades/{position_id}`, `/weekly`, `/weekly/{week}`, `/live`, `/live/commands`.
Their JSON twins (`/api/dashboard`, `/api/report`, `/api/trades`,
`/api/trades/{id}`, `/api/weekly`, `/api/weekly/{week}`, `/api/live`,
`/api/commands`) already back the SPA.

**Jinja form-POST write routes (5):**
`POST /live/{position_id}/{action}/confirm`, `POST /live/{position_id}/{action}`,
`POST /trades/{position_id}/annotate`, `POST /trades/{position_id}/tags`,
`POST /trades/{position_id}/tags/delete`, plus the helpers used only by them
(`_parse_fields`, `_back`). The SPA uses the JSON write path
(`/api/live/{id}/{action}/preview` + `/api/live/{id}/{action}`,
`/api/trades/{id}/annotate|tags|tags/delete`), whose server-side validation
(`CommandError`, 1.00-lot cap, asymmetric rules, `""` vs `"0"`) is untouched.

**Tests (`tests/test_web.py`):** delete the 5 template-rendering tests and their
`_env`/`_render` helpers:
`test_all_pages_render_with_seeded_db`, `test_all_pages_render_with_empty_db`,
`test_report_gated_cell_explains_itself_in_html`,
`test_rendered_money_carries_currency_no_bare_dollar`,
`test_live_strip_labels_floating_not_realized`.
The invariants they guarded survive at the formatter level and in vitest:
- gating honesty (§9 "n/a (n=…, perlu ≥20)") → `test_gated_below_20_explains_itself`
  + client `gatedR`/`isGated` (vitest);
- money never bare `$` → `test_money_carries_currency_and_never_bare_dollar`
  (+ `test_report.py`);
- NULL ≠ 0 → the `price`/`level_word`/`money` None tests.
No coverage of a domain rule is lost.

---

## 4. Keep — shared endpoints and modules (verified consumers)

- **All `/api/*`** routes — the SPA's entire data + write surface.
- **`GET /trades/{position_id}/chart.png`** — the React `<img>` in
  `TradeDetail.tsx` loads it by absolute path; it is an endpoint, not a template.
  Rule 6 (charts are reproducible cache) keeps it as-is.
- **`src/journal/web/format.py` in full** — `views.py` calls `fmt.level_word`
  (feeds command previews) and `fmt.server_offset_s` (WIB conversion), and
  `tests/test_web.py` unit-tests every formatter directly. It is also the server
  mirror of `frontend/src/lib/format.ts`. Not touched beyond staying put.
- **`_parse_week`** — relocated so its sole surviving caller is
  `GET /api/weekly/{week}` (fold-in #1); see §5.

---

## 5. Fold-ins (six deferred Phase-4 items)

1. **`_parse_week` single home.** The Jinja `/weekly/{week}` route (a caller) is
   deleted; `/api/weekly/{week}` currently inline-parses. Keep one `_parse_week`
   in `app.py` as the parser for `/api/weekly/{week}`; remove the duplicate inline
   parse. (No cross-module move needed — both live in `app.py`.)
2. **Shared `gatedR(n, avg)` in `frontend/src/lib/format.ts`.** Report's KPI panel
   ("n=N (perlu ≥20)") and bucket rows ("n=N (≥20)") duplicate gating copy/logic;
   unify into one formatter, update both call sites, keep a vitest case per copy
   variant. Mirrors `format.py:gated`.
3. **Parity copy** (React components): Report per-symbol table heading
   "Per symbol" → "Per symbol (symbol_base)"; Weekly `<h1>` "Weekly ·" →
   "Weekly review ·". Preserves the clearer Jinja wording into the SPA.
4. **Delete `frontend/src/pages/Placeholder.tsx`** (orphan, 0 imports) and any
   stale `Placeholder` import in `App.tsx`.
5. **Chart null-filter guard, no new dep.** Extract the "drop trades with null
   `r_multiple`/`mae_r`/`mfe_r`" filter used by `RHistogram`/`MaeMfeScatter` into
   a pure helper in `charts.ts`, and unit-test it in `charts.test.ts` (rule 4: a
   null series value is dropped, never plotted at 0). No React Testing Library —
   the guard is tested as pure logic, not via component render.
6. **`charts.test.ts` boundary cases:** histogram boundary bins (`-2` and `3`)
   and `dayStartUtcMs` (a UTC-midnight input is a no-op; an end-of-day input
   floors to the same day).

---

## 6. Docs & memory

- **`CLAUDE.md`:** update the Milestones lines (M7 "journal serve", M9 "/live")
  that still describe the Jinja UI, to state the UI is now the React SPA served
  at `/` (Jinja retired at Phase 5). Do not alter the hard rules or account facts.
- **`docs/HANDOFF.md`:** if it references the Jinja UI or `/app`, update to the
  cutover reality; otherwise leave.
- **Memory `frontend-react-rework.md`:** flip from "in-flight" to **DONE** — all
  six phases (0–5) merged to `main`, Jinja retired, SPA at `/`. Update the
  MEMORY.md index hook line to match.

---

## 7. Testing / Definition of done

- **New/changed Python tests** (pure, no live MT5):
  - Route precedence: with a built `dist/` present (or a monkeypatched guard),
    assert `/api/dashboard` returns JSON and `/trades/{id}/chart.png` returns PNG
    bytes — i.e. the SPA catch-all does not shadow data routes. This replaces the
    deleted template tests as the phase's key regression guard.
  - Assert the retired Jinja routes are gone (e.g. `GET /report` no longer returns
    the Jinja HTML — it now falls through to the SPA catch-all or 404 pre-build);
    scope this to whatever is stable across build/no-build so it is not flaky.
  - The existing `/api/*` tests continue to pass unchanged.
- **Frontend:** `npm --prefix frontend run build` exits 0 (asset base `/assets`);
  `npm --prefix frontend run test` (vitest) green, including the new `charts.ts`
  null-filter and boundary cases and the `gatedR` variants.
- **Regression:** `uv run pytest -q` green with **pasted output**;
  `uv run journal rebuild` still succeeds; `graphify update .` run after code
  changes.
- **Live check:** start `journal serve`, confirm the SPA renders at `/`, a client
  route (e.g. `/report`) deep-loads via the catch-all, `/api/dashboard` returns
  JSON, and a trade chart PNG loads on `/trades/{id}`.
- Done = the above verified, **not** "looks right". One PR to `main`.

---

## 8. Risks & mitigations

- **Catch-all shadows a data route** (the one real hazard) → register it last;
  assert `/api/*` + `chart.png` precedence in a test (§7).
- **Over-eager deletion** (removing an import or helper still used by a kept
  route) → grep each symbol for remaining consumers before deleting; run `pytest`
  after each removal, not just at the end.
- **Stale build served at `/`** → the DoD requires a fresh
  `npm --prefix frontend run build`; `dist/` is gitignored, so the reviewer/user
  rebuilds too.
- **Losing an invariant with the template tests** → mitigated by mapping each
  deleted assertion to a surviving formatter/vitest test (§3); no domain rule
  goes uncovered.

---

## 9. Delivery

Same workflow as Phases 3–4: `superpowers:writing-plans` to produce a task-by-task
plan, `superpowers:subagent-driven-development` to execute (fresh implementer +
per-task spec/quality review, whole-branch Opus review at the end), one PR to
`main`, `graphify update .` after code changes. This phase closes the rework.
