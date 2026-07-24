# Frontend React SPA Rework — Phase 5 (Cutover) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the React SPA the sole UI served at `/`, retire the legacy Jinja2 UI and its now-unused plumbing, and fold in six deferred Phase-4 cleanups — at full parity with all tests green.

**Architecture:** `web/app.py` mounts the built SPA assets at `/assets` and registers a single `GET /{full_path:path}` catch-all — registered LAST — that returns the SPA `index.html` for every path not already handled; `/api/*` and `/trades/{id}/chart.png` keep precedence. All Jinja HTML page routes, form-POST write routes, template files, template wiring, and the `/static` stylesheet are deleted; their JSON `/api/*` twins already back the SPA. `format.py` and every `/api/*` route stay (verified consumers). Route precedence is guarded by a structural test using Starlette's own matcher — **no `httpx`/`TestClient`, so no new dependency.**

**Tech Stack:** Python 3.12 / FastAPI (runtime unchanged, minus the now-removable `jinja2` + `python-multipart`); the existing `frontend/` React 18 + TS + Tailwind + Vitest. No new dependency.

## Global Constraints

- **The web layer never imports MT5 / never touches the bridge** (CLAUDE.md rules 1, 12). This phase touches only serving/plumbing; no adapter import is added.
- **Money is `accounts.currency` (USC), raw in JSON; the client formats.** Unchanged — no money path is edited except the client `gatedR` copy formatter.
- **Rule 4 survives as `null` ≠ `0`.** The chart null-filter extraction (Task 5) drops trades with `null` `r_multiple`/`mae_r`/`mfe_r` — never plots them at 0. A genuine `0` is kept.
- **§8/§9 sample-size gating stays client-side.** `isGated(n, avg)` / the new `gatedR(n, avg)` mirror `format.py`; `_MIN_N = 20`. Every statistic renders WITH its `n`.
- **Timestamps are broker SERVER time; WIB = UTC+7 display only** (rule 3). Unchanged.
- **Charts are cache, reproducible from the DB** (rule 6). `GET /trades/{id}/chart.png` is KEPT exactly as-is.
- **No new dependency** (rule 8). Route tests use Starlette's `route.matches(...)` (already present via FastAPI), not `TestClient`. `jinja2` and `python-multipart` are REMOVED only after grep confirms zero remaining use.
- **One PR** to `main` at the end of the phase.
- **Definition of done:** `uv run pytest -q` passes with pasted output; `npm --prefix frontend run build` exits 0 and `npm --prefix frontend run test` is green; `uv run journal rebuild` still succeeds; `graphify update .` run after code changes; and a live check confirms the SPA serves at `/` with `/api/*` + `chart.png` still responding.

**Verified facts (from the current tree, so tasks don't re-derive them):**
- `format.py` is used by `views.py` (`fmt.level_word` at `views.py:73`, `fmt.server_offset_s` at `views.py:112`) and unit-tested directly in `tests/test_web.py:68-124` → **KEEP in full.**
- The 5 Jinja template-rendering tests in `test_web.py` build their OWN Jinja env (`_env()`) and render template files directly — they do NOT go through `app.py` routes, so they keep passing until the template FILES are deleted (Task 3).
- No test currently uses `TestClient`/`httpx` (not installed). `test_api.py` tests payload builders directly; `test_web.py` tests builders/formatters directly.
- `Placeholder.tsx` has zero imports (orphan). `App.tsx` does NOT import it.
- Structural route resolution via Starlette works on the current app (validated): `/api/dashboard`→`api_dashboard`, `/report`→`report_page` (Jinja, to become `spa`).

---

### Task 1: Frontend — serve the SPA from `/` (not `/app`)

Point the Vite build and React Router at the site root, and fix the one hardcoded `/app` link. Build output is verification-only (`frontend/dist` is gitignored — not committed).

**Files:**
- Modify: `frontend/vite.config.ts` (`base: "/app/"` → `base: "/"`)
- Modify: `frontend/src/App.tsx:13` (`<BrowserRouter basename="/app">` → no basename)
- Modify: `frontend/src/pages/TradeDetail.tsx:82` (`href="/app/trades"` → `href="/trades"`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a build whose `dist/index.html` references `/assets/...` (consumed by Task 2's `/assets` mount + catch-all).

- [ ] **Step 1: Set the Vite base to root**

In `frontend/vite.config.ts`, change the base and its comment:
```ts
// Served by FastAPI at the site root (Phase 5 cutover; Jinja retired).
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

- [ ] **Step 2: Drop the router basename**

In `frontend/src/App.tsx`, line 13, change:
```tsx
    <BrowserRouter basename="/app">
```
to:
```tsx
    <BrowserRouter>
```

- [ ] **Step 3: Fix the hardcoded back-link**

In `frontend/src/pages/TradeDetail.tsx`, line 82, change `href="/app/trades"` to `href="/trades"`:
```tsx
      <p className="mt-4 text-[12px]"><a className="text-cyan hover:underline" href="/trades">← kembali ke daftar</a></p>
```
(The `<img src={`/trades/${trade.position_id}/chart.png`}` on line 68 is already root-absolute and stays untouched — it is the KEPT chart endpoint.)

- [ ] **Step 4: Build and verify the asset base flipped to `/assets/`**

Run: `npm --prefix frontend run build`
Expected: exits 0. Then verify the emitted asset refs are root-based:
Run: `grep -o '/assets/[^"]*' frontend/dist/index.html | head`
Expected: paths like `/assets/index-*.js` and `/assets/index-*.css` (NOT `/app/assets/...`).

- [ ] **Step 5: Run the frontend tests**

Run: `npm --prefix frontend run test`
Expected: PASS (no behavior changed; this guards against an accidental break).

- [ ] **Step 6: Commit**

```bash
git add frontend/vite.config.ts frontend/src/App.tsx frontend/src/pages/TradeDetail.tsx
git commit -m "feat(web): serve SPA from / (drop /app base + basename)"
```

---

### Task 2: Backend — cutover `app.py` to serve the SPA at `/`, retire Jinja routes

Replace the `/app` transition mount with a root catch-all, delete all Jinja HTML page routes + form-POST write routes + their helpers + the template engine wiring + the `/static` mount, and fold in the single `_parse_week` (fold-in #1). Add a structural route-precedence test (no `httpx`).

**Files:**
- Modify: `src/journal/web/app.py`
- Test: `tests/test_web.py` (add a "route wiring" section)

**Interfaces:**
- Consumes: `frontend/dist/index.html` (from Task 1) at runtime; the `_FRONTEND_DIST` path constant already in `app.py:48`.
- Produces: a route named `spa` (the catch-all) that resolves every non-API, non-chart path; `/api/*` and `trade_chart` keep precedence. `_parse_week(week) -> tuple[int,int]` is the sole ISO-week parser, used by `api_weekly`.

- [ ] **Step 1: Write the failing route-wiring tests**

Append to `tests/test_web.py` (a new section at the end; imports go at top of the appended block):
```python
# --------------------------------------------------- route wiring (no httpx)
from starlette.routing import Match
from journal.web.app import create_app


def _resolve(app, method, path):
    """The FIRST route to fully-match (method, path), via Starlette's own matcher.
    Lets us assert route PRECEDENCE without an HTTP client (no httpx dependency)."""
    scope = {"type": "http", "method": method, "path": path}
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "name", None)
    return None


def test_api_and_chart_routes_beat_spa_catchall():
    app = create_app(":memory:")
    assert _resolve(app, "GET", "/api/dashboard") == "api_dashboard"
    assert _resolve(app, "GET", "/api/trades/1") == "api_trade_detail"
    assert _resolve(app, "GET", "/api/weekly/2026-W28") == "api_weekly"
    assert _resolve(app, "GET", "/trades/1/chart.png") == "trade_chart"


def test_root_and_client_routes_serve_the_spa():
    app = create_app(":memory:")
    for path in ("/", "/report", "/live", "/trades", "/trades/1", "/weekly/2026-W28", "/commands"):
        assert _resolve(app, "GET", path) == "spa", path


def test_legacy_app_prefix_is_just_a_client_path_now():
    app = create_app(":memory:")
    # /app was the transition mount; after cutover it is an ordinary client path
    # served by the SPA shell, NOT a dedicated route.
    assert _resolve(app, "GET", "/app") == "spa"
    assert _resolve(app, "GET", "/app/trades") == "spa"


def test_jinja_write_routes_are_gone():
    app = create_app(":memory:")
    # The Jinja form-POST write path is retired; the JSON /api/* twins remain.
    assert _resolve(app, "POST", "/trades/1/annotate") is None
    assert _resolve(app, "POST", "/live/1/close") is None
    assert _resolve(app, "POST", "/api/trades/1/annotate") == "api_annotate"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web.py -k "route_ or root_and_client or legacy_app or jinja_write or api_and_chart" -v`
Expected: FAIL — currently `/` resolves to `dashboard`, `/report` to `report_page`, `/trades/1/annotate` to `post_annotate`, and there is no `spa` route covering these paths.

- [ ] **Step 3: Prune the imports and delete the template engine wiring**

In `src/journal/web/app.py`, update the top imports (lines 20-31). New form:
```python
from fastapi import Body, Depends, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..annotate import AnnotateError, add_tag, list_tags, remove_tag, set_annotation
from ..execute import CommandError, enqueue
from ..render.chart import NoCandlesError, TradeNotFoundError, render_trade
from ..store.db import connect
from . import views
from . import api
```
(Removed: `Form`, `Request` from `fastapi`; `RedirectResponse` from responses; `Jinja2Templates` import; `from . import format as fmt`. `datetime` on line 16 STAYS — `_parse_week` uses it. `StaticFiles` STAYS — the `/assets` mount uses it. `HTMLResponse` STAYS — the SPA catch-all returns it.)

Then in `create_app`, DELETE the `/static` mount and the entire `templates = Jinja2Templates(...)` block including its `.env.filters` / `.env.globals` wiring (current lines 58-70), and DELETE the `render()` and `error_page()` helpers (current lines 79-83). The `get_conn` dependency (lines 72-77) STAYS.

- [ ] **Step 4: Delete the Jinja HTML page routes and form-POST write routes**

In `src/journal/web/app.py`, DELETE these route functions entirely (they are superseded by `/api/*`):
- `dashboard` — `GET /` (current lines 87-94)
- `report_page` — `GET /report` (current lines 284-293)
- `trades` — `GET /trades` (current lines 297-309)
- `trade_detail` — `GET /trades/{position_id}` (current lines 311-323)
- `weekly_latest` — `GET /weekly` (current lines 347-352)
- `weekly` — `GET /weekly/{week}` (current lines 354-364)
- `live` — `GET /live` (current lines 368-377)
- `live_commands` — `GET /live/commands` (current lines 379-387)
- `_parse_fields` helper (current lines 389-396)
- `live_confirm` — `POST /live/{position_id}/{action}/confirm` (current lines 398-431)
- `live_enqueue` — `POST /live/{position_id}/{action}` (current lines 433-460)
- `_back` helper (current lines 464-465)
- `post_annotate` — `POST /trades/{position_id}/annotate` (current lines 467-490)
- `post_add_tag` — `POST /trades/{position_id}/tags` (current lines 492-505)
- `post_remove_tag` — `POST /trades/{position_id}/tags/delete` (current lines 507-516)

DO NOT delete: `trade_chart` (`GET /trades/{position_id}/chart.png`, current lines 325-336) and `_parse_week` (current lines 340-345) — both are kept.

- [ ] **Step 5: Fold-in #1 — make `_parse_week` the sole ISO-week parser**

`_parse_week` (kept) currently sits inside the deleted weekly block; move its definition up so it lives just above the `/api/weekly` routes (e.g. right after `get_conn`), unchanged in body:
```python
    def _parse_week(week: str) -> tuple[int, int]:
        """'YYYY-Www' → (iso_year, iso_week), validated via strptime's ISO
        directives like `cli._parse_iso_week`. The one ISO-week parser (fold-in)."""
        dt = datetime.strptime(f"{week}-1", "%G-W%V-%u")
        y, w, _ = dt.isocalendar()
        return y, w
```
Then replace the inline parse in `api_weekly` (current lines 171-186) so it uses `_parse_week`:
```python
    @app.get("/api/weekly/{week}")
    def api_weekly(week: str, conn: sqlite3.Connection = Depends(get_conn)):
        try:
            y, w = _parse_week(week)
        except ValueError:
            return JSONResponse(
                {"error": f"Minggu harus format ISO 'YYYY-Www' (mis. 2026-W28), got {week!r}."},
                status_code=400,
            )
        try:
            return JSONResponse(api.weekly_payload(conn, y, w))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```
(`api_weekly_latest` on current lines 161-169 is unchanged.)

- [ ] **Step 6: Replace the `/app` SPA block with the root catch-all**

In `src/journal/web/app.py`, replace the entire `# --- SPA (/app)` block (current lines 518-532) with:
```python
    # --------------------------------------------------------------- SPA (React)
    # The built SPA is the ONLY UI (Jinja retired, Phase 5). Assets mount at
    # /assets when a build exists; a catch-all — registered LAST — returns the SPA
    # shell for every other path so React Router owns the client routes. /api/* and
    # the chart PNG are declared above and keep precedence.
    if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="spa-assets",
        )

    _NO_BUILD_HTML = (
        "<!doctype html><meta charset='utf-8'><title>mt5-journal</title>"
        "<body style='font-family:system-ui;background:#0b0a1a;color:#e5e7eb;"
        "padding:2rem'><h1>SPA belum di-build</h1><p>Jalankan "
        "<code>npm --prefix frontend run build</code> lalu muat ulang.</p></body>"
    )

    def _spa_index() -> str:
        index = _FRONTEND_DIST / "index.html"
        return index.read_text(encoding="utf-8") if index.is_file() else _NO_BUILD_HTML

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def spa(full_path: str = ""):
        return HTMLResponse(_spa_index())

    return app
```
Note the comment near `_FRONTEND_DIST` (current lines 46-48) still says "Served at /app during the transition"; update it to "Served at the site root (Phase 5 cutover)."

- [ ] **Step 7: Run the route-wiring tests — now green**

Run: `uv run pytest tests/test_web.py -k "root_and_client or legacy_app or jinja_write or api_and_chart" -v`
Expected: PASS (all four).

- [ ] **Step 8: Run the full web + api suites (nothing else regressed)**

Run: `uv run pytest tests/test_web.py tests/test_api.py -q`
Expected: PASS. (The template-rendering tests in `test_web.py` still pass here — they render template files via their own Jinja env and are removed in Task 3.)

- [ ] **Step 9: Commit**

```bash
git add src/journal/web/app.py tests/test_web.py
git commit -m "feat(web): serve SPA at /, retire Jinja routes + template wiring

Root catch-all (registered last) serves the SPA shell; /api/* and chart.png
keep precedence (structural test, no httpx). Deletes 8 Jinja page routes, 5
form-POST write routes, render()/error_page(), the Jinja2Templates engine +
filter/global wiring, and the /static mount. Folds _parse_week into the sole
ISO-week parser used by /api/weekly/{week}."
```

---

### Task 3: Delete the dead template files, static asset, template tests, and unused deps

With no code referencing them, remove the Jinja template files, the `/static` stylesheet, the 5 template-rendering tests, and the now-unused `jinja2` + `python-multipart` dependencies — each verified by grep before removal.

**Files:**
- Delete: `src/journal/web/templates/` (all 10 `.html`)
- Delete: `src/journal/web/static/app.css` (and the empty `static/` dir)
- Modify: `tests/test_web.py` (remove template-rendering tests + `_env`/`_render` helpers)
- Modify: `pyproject.toml` (remove `jinja2`, `python-multipart`)

**Interfaces:**
- Consumes: Task 2 (routes/wiring already gone). Produces: nothing new.

- [ ] **Step 1: Verify no code references the templates or `/static`**

Run: `grep -rniE "templateresponse|jinja2templates|render\(|error_page\(|/static|app\.css|templates/" src/journal/ | grep -v "\.pyc"`
Expected: no hits in `src/journal/web/app.py` (only possibly unrelated matches elsewhere — inspect; there should be none referencing the web templates/static). If any real consumer remains, STOP and fix Task 2 first.

- [ ] **Step 2: Delete the template files and the static stylesheet**

```bash
git rm -r src/journal/web/templates
git rm src/journal/web/static/app.css
```

- [ ] **Step 3: Remove the 5 template-rendering tests and their helpers**

In `tests/test_web.py`, DELETE the `# --------- template rendering` section: the `_env()` helper, the `_render(name, ctx)` helper, and the tests `test_all_pages_render_with_seeded_db`, `test_all_pages_render_with_empty_db`, `test_report_gated_cell_explains_itself_in_html`, `test_rendered_money_carries_currency_no_bare_dollar`, `test_live_strip_labels_floating_not_realized`.

KEEP everything else, including the formatter unit tests (`test_gated_below_20_explains_itself`, `test_money_carries_currency_and_never_bare_dollar`, the `price`/`level_word`/`money` None tests) — they preserve the invariants the deleted HTML tests guarded, at the formatter level.

- [ ] **Step 4: Run pytest — confirm the deletions are clean**

Run: `uv run pytest tests/test_web.py -q`
Expected: PASS with the template tests gone and the route-wiring + builder + formatter tests still green.

- [ ] **Step 5: Verify `jinja2` and `python-multipart` are now unused, then remove them**

Run: `grep -rniE "jinja2|jinja|multipart|[^a-z]Form\(" src/journal/ tests/`
Expected: no hits (after Tasks 2-3, no `Form(...)` and no Jinja anywhere). If clean, edit `pyproject.toml` — delete the `"jinja2",` and `"python-multipart",` lines from `dependencies`:
```toml
dependencies = [
    "siliconmetatrader5",
    "pandas",
    "mplfinance",
    "typer",
    "fastapi",
    "uvicorn",
]
```
If ANY hit remains, keep the corresponding dependency and note why.

- [ ] **Step 6: Re-sync and run the full suite**

Run: `uv run pytest -q`
Expected: PASS (uv re-resolves the environment without `jinja2`/`python-multipart`). Confirm the app still imports and builds routes:
Run: `uv run python -c "from journal.web.app import create_app; create_app(':memory:'); print('app ok')"`
Expected: `app ok`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(web): delete Jinja templates, /static css, template tests, unused deps

Removes templates/ (10 files), static/app.css, the 5 template-rendering tests
(+ _env/_render helpers), and the now-unused jinja2 + python-multipart deps.
Invariants they guarded survive in the formatter unit tests + vitest."
```

---

### Task 4: Fold-in #2 — shared `gatedR(n, avg)` formatter

Unify the two duplicated R-gating copy variants in `Report.tsx` into one formatter in `format.ts`, mirroring `format.py:gated`.

**Files:**
- Modify: `frontend/src/lib/format.ts` (add `gatedR`)
- Modify: `frontend/src/lib/format.test.ts` (test both branches)
- Modify: `frontend/src/pages/Report.tsx` (use `gatedR` at both sites)

**Interfaces:**
- Consumes: existing `isGated`, `rmult` in `format.ts`.
- Produces: `gatedR(n: number, avg: number | null): string`.

- [ ] **Step 1: Write the failing formatter test**

Append to `frontend/src/lib/format.test.ts` (import `gatedR` alongside the existing imports):
```ts
describe("gatedR", () => {
  it("withheld (avg null under n<20) says why with the count", () => {
    expect(gatedR(6, null)).toBe("n=6 (perlu ≥20)");
  });
  it("shows the R value when present", () => {
    expect(gatedR(25, 1.2)).toBe("1.20R");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix frontend run test -- format`
Expected: FAIL — `gatedR is not a function` / not exported.

- [ ] **Step 3: Implement `gatedR`**

Append to `frontend/src/lib/format.ts`:
```ts
export function gatedR(n: number, avg: number | null): string {
  // §9 sample-size honesty for an R statistic: when the average was withheld
  // (null under n<20) say why with the count; otherwise the R value. The one
  // R-gating formatter — mirrors web/format.py:gated for R. rule 4 / docs §9.
  return isGated(n, avg) ? `n=${n} (perlu ≥20)` : rmult(avg);
}
```

- [ ] **Step 4: Use `gatedR` at both Report sites**

In `frontend/src/pages/Report.tsx`:
- Line 3 import — add `gatedR`: `import { money, pct, rmult, isGated, gatedR } from "../lib/format";`
- In `BucketTable`, delete the `const rGated = isGated(b.n_with_r, b.avg_r);` line (current line 24) and change the Avg-R cell (current line 31) from `{rGated ? `n=${b.n_with_r} (≥20)` : rmult(b.avg_r)}` to:
```tsx
                  <td className="py-2 num">{gatedR(b.n_with_r, b.avg_r)}</td>
```
  (Keep `const rowGated = ...` and its row-className use unchanged.)
- Delete the local `rGate` helper (current line 55: `const rGate = (n, avg) => ...`) and replace its three call sites (current lines 82-84) with `gatedR`:
```tsx
          <Kv label="Avg MAE (R)">{gatedR(r.n_with_mae_r, r.avg_mae_r)}</Kv>
          <Kv label="Avg MFE (R)">{gatedR(r.n_with_mfe_r, r.avg_mfe_r)}</Kv>
          <Kv label="Avg R (akun)">{gatedR(r.n_with_r, r.avg_r)}</Kv>
```

- [ ] **Step 5: Run tests + build**

Run: `npm --prefix frontend run test`
Expected: PASS (including the new `gatedR` cases).
Run: `npm --prefix frontend run build`
Expected: exits 0 (no unused-var / type error — `rmult` is still used elsewhere in `Report.tsx`; confirm the build does not flag `rGated`/`rGate` as removed-but-referenced).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/format.ts frontend/src/lib/format.test.ts frontend/src/pages/Report.tsx
git commit -m "refactor(web): unify Report R-gating into shared gatedR formatter"
```

---

### Task 5: Fold-in #5 + #6 — extract chart null-filters + boundary tests

Move the "drop null R / MAE / MFE" filters out of the chart components into pure helpers in `charts.ts` and unit-test them (rule 4, no React Testing Library), and add the missing histogram/`dayStartUtcMs` boundary cases.

**Files:**
- Modify: `frontend/src/lib/charts.ts` (add `rValues`, `maeMfePoints`)
- Modify: `frontend/src/lib/charts.test.ts` (null-drop + boundary cases)
- Modify: `frontend/src/components/RHistogram.tsx` (use `rValues`)
- Modify: `frontend/src/components/MaeMfeScatter.tsx` (use `maeMfePoints`)

**Interfaces:**
- Consumes: `ChartTrade` from `./types` (`{ position_id, symbol_base, close_time_msc, net_profit, r_multiple: number|null, mae_r: number|null, mfe_r: number|null }`).
- Produces: `rValues(series: ChartTrade[]): number[]`; `maeMfePoints(series: ChartTrade[]): (ChartTrade & { mae_r: number; mfe_r: number })[]`.

- [ ] **Step 1: Write the failing helper + boundary tests**

In `frontend/src/lib/charts.test.ts`, extend the import and add tests:
```ts
import { histogramBins, dayStartUtcMs, calendarCells, rValues, maeMfePoints } from "./charts";
```
```ts
  it("rValues: drops null r_multiple, keeps reals incl. a genuine 0", () => {
    const base = { symbol_base: "XAUUSD", close_time_msc: 0, net_profit: 0, mae_r: null, mfe_r: null };
    const s = [
      { ...base, position_id: 1, r_multiple: 1.5 },
      { ...base, position_id: 2, r_multiple: null },
      { ...base, position_id: 3, r_multiple: 0 },
    ];
    expect(rValues(s)).toEqual([1.5, 0]); // rule 4: null dropped, real 0 kept
  });

  it("maeMfePoints: drops a trade missing either MAE or MFE", () => {
    const base = { symbol_base: "XAUUSD", close_time_msc: 0, net_profit: 0, r_multiple: null };
    const s = [
      { ...base, position_id: 1, mae_r: -0.4, mfe_r: 2.1 },
      { ...base, position_id: 2, mae_r: -0.4, mfe_r: null },
      { ...base, position_id: 3, mae_r: null, mfe_r: 2.1 },
    ];
    expect(maeMfePoints(s).map((p) => p.position_id)).toEqual([1]);
  });

  it("histogramBins: -2 and 3 land left-closed at their edge bins", () => {
    const b = histogramBins([-2, 3]);
    expect(b[1].count).toBe(1); // [-2,-1): the -2
    expect(b[6].count).toBe(1); // [3,∞): the 3
  });

  it("dayStartUtcMs: midnight is a no-op; end-of-day floors to that midnight", () => {
    const mid = Date.UTC(2026, 0, 15, 0, 0, 0);
    expect(dayStartUtcMs(mid)).toBe(mid);
    const eod = Date.UTC(2026, 0, 15, 23, 59, 59, 999);
    expect(dayStartUtcMs(eod)).toBe(mid);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix frontend run test -- charts`
Expected: FAIL — `rValues`/`maeMfePoints` not exported (the two boundary tests may already pass; the helper tests must fail).

- [ ] **Step 3: Implement the helpers**

In `frontend/src/lib/charts.ts`, add the type import at the top and the two helpers (e.g. below the header comment):
```ts
import { ChartTrade } from "./types";

export function rValues(series: ChartTrade[]): number[] {
  // rule 4: a null r_multiple is UNKNOWN — dropped, never plotted as 0.
  return series.map((t) => t.r_multiple).filter((r): r is number => r !== null);
}

export function maeMfePoints(
  series: ChartTrade[],
): (ChartTrade & { mae_r: number; mfe_r: number })[] {
  // rule 4: a trade missing MAE or MFE is dropped, never coerced to 0.
  return series.filter(
    (t): t is ChartTrade & { mae_r: number; mfe_r: number } =>
      t.mae_r !== null && t.mfe_r !== null,
  );
}
```

- [ ] **Step 4: Point the components at the helpers**

In `frontend/src/components/RHistogram.tsx`: change the import line 2 to `import { histogramBins, rValues } from "../lib/charts";` and replace line 5 with:
```tsx
  const values = rValues(series);
```
In `frontend/src/components/MaeMfeScatter.tsx`: add `import { maeMfePoints } from "../lib/charts";` and replace the inline `const pts = series.filter(...)` (current lines 4-7) with:
```tsx
  const pts = maeMfePoints(series);
```

- [ ] **Step 5: Run tests + build**

Run: `npm --prefix frontend run test`
Expected: PASS (all charts cases).
Run: `npm --prefix frontend run build`
Expected: exits 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/charts.ts frontend/src/lib/charts.test.ts frontend/src/components/RHistogram.tsx frontend/src/components/MaeMfeScatter.tsx
git commit -m "refactor(web): extract chart null-filters to charts.ts (rule 4) + boundary tests"
```

---

### Task 6: Fold-in #3 + #4 — parity copy + delete orphaned `Placeholder.tsx`

Two small parity copy tweaks and one dead-file deletion.

**Files:**
- Modify: `frontend/src/pages/Report.tsx:91` (per-symbol table title)
- Modify: `frontend/src/pages/Weekly.tsx:28` (`<h1>` copy)
- Delete: `frontend/src/pages/Placeholder.tsx`

**Interfaces:** none.

- [ ] **Step 1: Parity copy — Report per-symbol title**

In `frontend/src/pages/Report.tsx`, line 91, change `title="Per symbol"` to `title="Per symbol (symbol_base)"`:
```tsx
        <BucketTable title="Per symbol (symbol_base)" rows={r.by_symbol} ccy={ccy} />
```

- [ ] **Step 2: Parity copy — Weekly heading**

In `frontend/src/pages/Weekly.tsx`, line 28, change `Weekly ·` to `Weekly review ·`:
```tsx
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Weekly review · {wk(r.iso_year, r.iso_week)}</h1>
```

- [ ] **Step 3: Delete the orphaned Placeholder page**

Confirm it is still unimported, then delete:
Run: `grep -rn "Placeholder" frontend/src/`
Expected: only `frontend/src/pages/Placeholder.tsx` (its own definition). Then:
```bash
git rm frontend/src/pages/Placeholder.tsx
```

- [ ] **Step 4: Build + test**

Run: `npm --prefix frontend run build`
Expected: exits 0 (no dangling import to the deleted file).
Run: `npm --prefix frontend run test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Report.tsx frontend/src/pages/Weekly.tsx
git commit -m "chore(web): parity copy (Per symbol / Weekly review) + drop orphan Placeholder"
```

---

### Task 7: Docs + final verification

Update `CLAUDE.md` and annotate `docs/HANDOFF.md`, then run the full Definition-of-Done gate. (The memory flip of `frontend-react-rework.md` → DONE is handled by the orchestrator after merge, not in this task.)

**Files:**
- Modify: `CLAUDE.md` (Milestones + "Currently on")
- Modify: `docs/HANDOFF.md` (a forward note; do NOT rewrite historical M7 lines)

**Interfaces:** none.

- [ ] **Step 1: Update `CLAUDE.md` Milestones**

In `CLAUDE.md`, append a line to the Milestones block (after the M9 line, current line 113):
```
· **Frontend rework** (Jinja→React SPA, served at `/`; Jinja UI retired at
Phase 5 cutover)
```
And replace the "Currently on:" line (current line 115) with:
```
Currently on: **Frontend React rework COMPLETE — the SPA is the sole UI, served
at `/` (Jinja retired, Phase 5 cutover). M9 live-bridge smoke still pending a
human run — see docs/HANDOFF.md**
```

- [ ] **Step 2: Annotate `docs/HANDOFF.md`**

Add ONE forward note near the top of `docs/HANDOFF.md` (leave the historical M7 "FastAPI/Jinja2 dashboard" description intact as history):
```
> **Update 2026-07-24 (Phase 5 cutover):** the web UI is now the React SPA
> served at `/`; the Jinja2 templates, `/static/app.css`, the form-POST write
> routes, and the `jinja2`/`python-multipart` deps have been retired. `journal
> serve` and the loopback/WAL coexistence notes below are unchanged.
```

- [ ] **Step 3: Full Python suite (paste the output)**

Run: `uv run pytest -q`
Expected: PASS. Paste the actual summary line.

- [ ] **Step 4: Frontend build + tests**

Run: `npm --prefix frontend run build`
Expected: exits 0.
Run: `npm --prefix frontend run test`
Expected: PASS.

- [ ] **Step 5: Rebuild guard**

Run: `uv run journal rebuild`
Expected: succeeds (trades rebuilt from raw).

- [ ] **Step 6: Live smoke — SPA at `/`, data routes intact**

Start the server (background), then probe:
```bash
JOURNAL_DB=data/journal.db uv run journal serve &
sleep 2
curl -s -o /dev/null -w "GET / -> %{http_code} %{content_type}\n" http://127.0.0.1:8000/
curl -s -o /dev/null -w "GET /report -> %{http_code} %{content_type}\n" http://127.0.0.1:8000/report
curl -s -o /dev/null -w "GET /api/dashboard -> %{http_code} %{content_type}\n" http://127.0.0.1:8000/api/dashboard
curl -s -o /dev/null -w "GET /app -> %{http_code}\n" http://127.0.0.1:8000/app
```
Expected: `/` and `/report` return `200 text/html` (SPA shell); `/api/dashboard` returns `200 application/json`; `/app` returns `200` (served by the SPA shell, no dedicated route). Stop the server afterward (`kill %1`).

- [ ] **Step 7: Update the code graph**

Run: `graphify update .`
Expected: completes; code graph refreshed.

- [ ] **Step 8: Commit**

```bash
git add CLAUDE.md docs/HANDOFF.md
git commit -m "docs: Phase 5 cutover — SPA served at /, Jinja retired"
```

---

## Self-Review

**Spec coverage** (spec §2–§8 → task):
- §2 serve at `/`, `/assets`, catch-all last, drop `/app`, pre-build fallback → Task 1 (frontend base) + Task 2 (backend).
- §3 retire templates, `/static`, render/error_page, Jinja2 wiring, 8 page routes, 5 form-POST routes, template tests → Task 2 (routes/wiring) + Task 3 (files, tests, deps).
- §4 keep `/api/*`, `chart.png`, `format.py`, single `_parse_week` → preserved in Task 2 (explicit KEEP list + fold-in #1); grep-guarded in Task 3.
- §5 fold-ins: #1 `_parse_week` → Task 2; #2 `gatedR` → Task 4; #3 parity copy → Task 6; #4 delete Placeholder → Task 6; #5 chart null-filter → Task 5; #6 boundary tests → Task 5.
- §6 docs/memory → Task 7 (CLAUDE.md, HANDOFF.md); memory flip by orchestrator post-merge.
- §7 route-precedence guard → Task 2 structural tests; DoD (pytest paste, build, vitest, rebuild, graphify, live check) → Task 7.
- §8 risks: catch-all shadowing → Task 2 precedence test; over-eager deletion → grep-before-delete in Tasks 2/3; stale build → Task 7 fresh build; lost invariants → Task 3 maps each to a surviving test.

**Placeholder scan:** none — every code step carries literal code; every command has an expected result.

**Type consistency:** `gatedR(n: number, avg: number | null): string`, `isGated`, `rmult` consistent across Task 4. `rValues`/`maeMfePoints` signatures identical between definition (Task 5 Step 3), test (Step 1), and component use (Step 4). `_parse_week(week) -> tuple[int,int]` consistent in Task 2. Route name `spa` used identically in the catch-all and every route-wiring assertion.

**Delivery:** one PR to `main`; `graphify update .` in Task 7. This phase closes the Jinja→React rework.
