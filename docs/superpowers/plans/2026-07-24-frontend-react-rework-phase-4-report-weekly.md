# Frontend React SPA Rework — Phase 4 (Report + Weekly + analytics charts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React `/report` page (KPI + MAE/MFE + by-session/source/symbol tables with honest §9 gating) and `/weekly/:week?` page (week picker, redirect to the last complete ISO week), plus three client-rendered analytics charts — R-multiple histogram, MAE/MFE scatter, and a daily-P&L calendar heatmap — over new thin JSON endpoints. Also fold in five minor Phase 2/3 follow-ups.

**Architecture:** New JSON endpoints (`GET /api/report`, `GET /api/weekly`, `GET /api/weekly/{week}`) are thin wrappers over the EXISTING, tested builders `views.report_context` (→ `build_report`) and `views.weekly_context` (→ `build_weekly`). One NEW read-only builder, `views.analytics_series_context`, ships the raw closed-trade columns the three charts need (`close_time_msc, net_profit, r_multiple, mae_r, mfe_r`); it is the same tier as the existing `equity_curve` (a plain DB read, no domain logic, no MT5). **Charts render entirely client-side from these raw arrays** — the client does all binning, day-bucketing, and mark layout, mirroring the existing `SymbolBars`/`TradeSparkbar` pattern (NOT the server-SVG `_svg_geometry` pattern, which stays scoped to the equity/R line curves). **Sample-size gating stays client-side** via the existing `format.ts:isGated`, exactly as the Dashboard already does.

**Tech Stack:** Python 3.12 / FastAPI (unchanged runtime); the existing `frontend/` React 18 + TS + Tailwind + Vitest. No new dependency (Recharts, already in `package.json`, is deliberately NOT used).

## Global Constraints

- **The web layer never imports MT5 / never touches the bridge** (CLAUDE.md rules 1, 12). `web/api.py` imports only `web/views`; `views.analytics_series_context` does a plain `conn.execute` DB read. No `import MetaTrader5`, no MT5 constants.
- **Money is `accounts.currency` (USC), raw in JSON; the client formats.** `net_profit`/`net_total`/`expectancy`/`avg_win`/`avg_loss` stay raw numbers; never emit "$"; the currency stays glued to the number at display (`money(x, ccy)`).
- **Rule 4 survives end-to-end as `null` ≠ `0`.** `r_multiple`/`mae_r`/`mfe_r` = `null` means **unknown** — such trades are EXCLUDED from the R-histogram and MAE/MFE scatter (never plotted at 0), exactly as `build_report` excludes them from averages. A gated average arrives as JSON `null` and renders "n/a"/"perlu ≥20", never 0.
- **§8/§9 sample-size gating is client-side** (recorded decision, spec §2.1). `build_report`/`build_weekly` already pre-gate each averaged field to `None` when its own `n < 20`; the client mirror `isGated(n, avg)` greys/suppresses the bucket. Every statistic renders WITH its `n`. `_MIN_N = 20`.
- **Timestamps are broker SERVER time; WIB = UTC+7 at display only** (rule 3). Server clock is UTC (`server_utc_offset_s = 0`). The calendar buckets trades by **UTC day** (floor `close_time_msc` to UTC midnight) — the same realized attribution `build_weekly` uses — and labels are display-only.
- **Money and R are `REAL`.** Compare with tolerance, never `==` (rule 5). The report/weekly builders already do this; the plan adds no new float comparison on the server. Client geometry uses `>`/`<`/`Math.abs` on already-computed values (a glance cue, not a classification), matching `SymbolBars`.
- **No new dependency** (rule 8). Python tests are pure functions over a seeded DB (no httpx/TestClient); the frontend uses Vitest. Legacy Jinja `/report`, `/weekly*` routes stay untouched.
- **One PR** to `main` at the end of the phase.
- **Definition of done:** `uv run pytest -q` passes with pasted output; `npm --prefix frontend run build` exits 0 and `npm --prefix frontend run test` is green; `uv run journal rebuild` still succeeds; `graphify update .` run after code changes.

---

### Task 1: `analytics_series_context` builder + `report_payload` (pure, tested)

Add the chart-data builder and the composed report payload, plus extend the test seed helper to set `mae_r`/`mfe_r`, and delete the dead `_seed_spec` helper (Phase 2 follow-up #5).

**Files:**
- Modify: `src/journal/web/views.py` (add `analytics_series_context`)
- Modify: `src/journal/web/api.py` (add `report_payload`)
- Modify: `tests/test_web.py` (add a builder test)
- Modify: `tests/test_api.py` (extend `_seed_trade` with `mae_r`/`mfe_r`; add `report_payload` test; remove dead `_seed_spec`)

**Interfaces:**
- Consumes: `views.report_context(conn)` (existing → `{"report": ReportResult}`); `one_account_login`.
- Produces:
  - `analytics_series_context(conn) -> {"series": [ {position_id, symbol_base, close_time_msc, net_profit, r_multiple, mae_r, mfe_r} … ]}` — every CLOSED trade with a non-null `close_time_msc`, ordered by `close_time_msc ASC`. `r_multiple`/`mae_r`/`mfe_r` stay `None` when unknown (rule 4). A plain DB read; no averaging, no gating (raw per-trade rows).
  - `report_payload(conn) -> {"header": {...}, "report": {...}, "series": [ {…} … ]}` — composes `account_header` + `report_context["report"]` + `analytics_series_context["series"]`, JSON-safe via `to_jsonable`.

- [ ] **Step 1: Extend `_seed_trade` in `tests/test_api.py` to carry `mae_r`/`mfe_r`**

Replace the existing `_seed_trade` (currently `tests/test_api.py:38-51`) with:
```python
def _seed_trade(conn, position_id, *, symbol_base="XAUUSD", direction="buy",
                status="closed", net_profit=0.0, r_multiple=None,
                sl_initial=None, magic=None, close_time_msc=None,
                mae_r=None, mfe_r=None):
    symbol = symbol_base + "c"
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, duration_s, volume, "
        "open_price, close_price, sl_initial, net_profit, r_multiple, mae_r, mfe_r, "
        "magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.1, 4000.0, 4001.0, ?, ?, ?, ?, ?, ?, 2, 1)",
        (_LOGIN, position_id, symbol, symbol_base, direction, status, _ms(9),
         close_time_msc or _ms(10), 3600, sl_initial, net_profit, r_multiple,
         mae_r, mfe_r, magic),
    )
    conn.commit()
```
(This is additive — every existing caller omits `mae_r`/`mfe_r`, so they default to `NULL` exactly as before.)

- [ ] **Step 2: Remove the dead `_seed_spec` helper (Phase 2 follow-up #5)**

In `tests/test_api.py`, delete the entire `_seed_spec` function (currently `tests/test_api.py:108-115`, the block starting `def _seed_spec(conn, symbol="XAUUSDc", *, trade_mode=4,` through its `conn.commit()`). It is defined but never called anywhere in the file (verified: the only match for `_seed_spec` is its own `def`). Leave the `# --- live / commands seed helpers …` comment above it and the `_seed_position`/`_seed_command` helpers below it intact.

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_web.py` (it already has its own `_seed_account`/`_seed_trade`; the `views` module is imported there as `views`):
```python
def test_analytics_series_context_raw_closed_trades_nulls_preserved(conn):
    _seed_account(conn)
    # a fully-known trade, an R-unknown trade, and an OPEN trade (excluded)
    _seed_trade(conn, 1, net_profit=250.0, r_multiple=1.5,
                close_time_msc=_ms(10), sl_initial=3990.0)
    _seed_trade(conn, 2, net_profit=-80.0, r_multiple=None,
                close_time_msc=_ms(11))
    _seed_trade(conn, 3, status="open", net_profit=0.0, close_time_msc=None)
    ctx = views.analytics_series_context(conn)
    series = ctx["series"]
    assert [s["position_id"] for s in series] == [1, 2]  # open one excluded, time-ordered
    assert series[0]["net_profit"] == 250.0
    assert series[1]["r_multiple"] is None               # rule 4: unknown stays null
    # mae_r/mfe_r default null when the poller/candles haven't filled them
    assert series[0]["mae_r"] is None and series[0]["mfe_r"] is None
```
Append to `tests/test_api.py`:
```python
def test_report_payload_composes_report_and_series(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=250.0, r_multiple=1.5, mae_r=-0.4, mfe_r=2.1,
                close_time_msc=_ms(10))
    _seed_trade(conn, 2, net_profit=-80.0, r_multiple=None, close_time_msc=_ms(11))
    p = api.report_payload(conn)
    json.dumps(p)  # must not raise
    assert set(p.keys()) == {"header", "report", "series"}
    assert p["header"]["currency"] == "USC"
    assert p["report"]["n_closed"] == 2
    # §9 gate: only 2 R-known trades → avg_r withheld as null, never 0
    assert p["report"]["avg_r"] is None
    # series carries the raw per-trade chart source; nulls preserved (rule 4)
    by_pos = {s["position_id"]: s for s in p["series"]}
    assert by_pos[1]["mfe_r"] == 2.1
    assert by_pos[2]["r_multiple"] is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_web.py -k analytics_series tests/test_api.py -k report_payload -v`
Expected: FAIL — `AttributeError: module 'journal.web.views' has no attribute 'analytics_series_context'` and `... 'journal.web.api' has no attribute 'report_payload'`.

- [ ] **Step 5: Implement the builder**

In `src/journal/web/views.py`, add after `report_context` (the function ending near `views.py:244`):
```python
def analytics_series_context(conn: sqlite3.Connection) -> dict:
    """Raw per-trade rows for the /report charts (R-histogram, MAE/MFE scatter,
    daily-P&L calendar). Every CLOSED trade with a realized `close_time_msc`,
    ordered by close time. Same tier as `equity_curve`: a plain DB read, no
    averaging and no §9 gating — the client bins/buckets and applies gating.
    `r_multiple`/`mae_r`/`mfe_r` stay NULL when unknown (rule 4); such trades are
    dropped per-chart on the client, never plotted as 0. Money is raw USC."""
    login = one_account_login(conn)
    rows = conn.execute(
        "SELECT position_id, symbol_base, close_time_msc, net_profit, "
        "r_multiple, mae_r, mfe_r FROM trades "
        "WHERE account_login = ? AND status = 'closed' AND close_time_msc IS NOT NULL "
        "ORDER BY close_time_msc ASC",
        (login,),
    ).fetchall()
    return {"series": rows}
```

- [ ] **Step 6: Implement the payload**

In `src/journal/web/api.py`, add after `dashboard_payload` (near `api.py:53`):
```python
def report_payload(conn: sqlite3.Connection) -> dict:
    """Header + the M8 analytics report + the raw per-trade chart series for
    /api/report. Composes `views.report_context` and `views.analytics_series_context`
    (like `dashboard_payload` composes its pieces); adds no logic. The §9 gate and
    NULLs arrive as JSON null and pass through untouched (rule 4). Money stays raw USC."""
    return to_jsonable({
        "header": views.account_header(conn),
        "report": views.report_context(conn)["report"],
        "series": views.analytics_series_context(conn)["series"],
    })
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_web.py -k analytics_series tests/test_api.py -k report_payload -v` then `uv run pytest -q`
Expected: the new tests PASS; full suite passes with zero new failures (the `_seed_spec` removal breaks nothing — it was unused). Paste the `-q` summary.

- [ ] **Step 8: Commit**

```bash
git add src/journal/web/views.py src/journal/web/api.py tests/test_web.py tests/test_api.py
git commit -m "feat(web): analytics_series_context + report_payload (raw chart series; drop dead _seed_spec)"
```

---

### Task 2: `weekly_payload` (pure, tested)

Add the weekly JSON payload wrapping the existing `weekly_context`.

**Files:**
- Modify: `src/journal/web/api.py` (add `weekly_payload`)
- Modify: `tests/test_api.py` (add a `weekly_payload` test + annotation/tag seed helpers if absent)

**Interfaces:**
- Consumes: `views.weekly_context(conn, iso_year, iso_week)` (existing → `{header, result: WeeklyResult, weeks, start_ms}`).
- Produces: `weekly_payload(conn, iso_year: int, iso_week: int) -> {"header": {...}, "result": {...}, "weeks": [[year, week] …], "start_ms": int}` — JSON-safe; `result` is the full `WeeklyResult` (with `by_session`, `by_source`, `notes`); gated averages are `null` (rule 4); money raw USC.

- [ ] **Step 1: Add annotation/tag seed helpers to `tests/test_api.py` (if not already present from Phase 3)**

Check whether `_seed_annotation` and `_seed_tag` exist in `tests/test_api.py`. Phase 3 added them; if present, skip this step. If absent, append:
```python
def _seed_annotation(conn, position_id, *, setup=None, confidence=None,
                     emotion=None, followed_plan=None, notes=None):
    conn.execute(
        "INSERT INTO annotations (account_login, position_id, segment, setup, "
        "confidence, emotion, followed_plan, notes, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?, 1, 1)",
        (_LOGIN, position_id, setup, confidence, emotion, followed_plan, notes),
    )
    conn.commit()


def _seed_tag(conn, position_id, tag, source="manual"):
    conn.execute(
        "INSERT INTO tags (account_login, position_id, segment, tag, source) "
        "VALUES (?, ?, 0, ?, ?)",
        (_LOGIN, position_id, tag, source),
    )
    conn.commit()
```

- [ ] **Step 2: Write the failing test**

`_ms(hour, day)` in `tests/test_api.py` builds a timestamp in January 2026. 2026-01-15 is a Thursday, ISO week 3, so a trade closed that day lands in ISO 2026-W03. Append:
```python
def test_weekly_payload_shape_gating_and_notes(conn):
    _seed_account(conn)
    # two closed trades in ISO 2026-W03 (Jan 15 = Thu of week 3); one annotated
    _seed_trade(conn, 1, net_profit=250.0, close_time_msc=_ms(10, day=15))
    _seed_trade(conn, 2, net_profit=-80.0, close_time_msc=_ms(11, day=15))
    _seed_annotation(conn, 1, setup="breakout", confidence=4, followed_plan=1)
    _seed_tag(conn, 1, "revenge", source="manual")

    p = api.weekly_payload(conn, 2026, 3)
    json.dumps(p)  # must not raise
    assert set(p.keys()) == {"header", "result", "weeks", "start_ms"}
    r = p["result"]
    assert r["iso_year"] == 2026 and r["iso_week"] == 3
    assert r["n_closed"] == 2
    assert r["net_total"] == 170.0           # 250 + (-80); a sum, always shown
    assert r["win_rate"] is None             # §9: 2 < 20 → gated to null, not 0
    # notes surfaces the annotated/manually-tagged trade
    assert [n["position_id"] for n in r["notes"]] == [1]
    assert r["notes"][0]["setup"] == "breakout"
    # weeks nav lists (year, week) tuples as JSON arrays
    assert [2026, 3] in p["weeks"]


def test_weekly_payload_empty_week_is_honest(conn):
    _seed_account(conn)
    p = api.weekly_payload(conn, 2026, 3)
    assert p["result"]["n_closed"] == 0
    assert p["result"]["net_total"] == 0
    assert p["result"]["notes"] == []
    assert p["weeks"] == []                   # no closed trades → empty nav
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -k weekly_payload -v`
Expected: FAIL — `AttributeError: module 'journal.web.api' has no attribute 'weekly_payload'`.

- [ ] **Step 4: Implement**

In `src/journal/web/api.py`, add after `report_payload`:
```python
def weekly_payload(conn: sqlite3.Connection, iso_year: int, iso_week: int) -> dict:
    """Header + one ISO week's `WeeklyResult` + the week-navigation list for
    /api/weekly. Wraps `views.weekly_context`; adds no logic. `net_total` is a
    realized sum (always shown); rate/average fields arrive `null` when §9-gated
    (a single week rarely clears n≥20) — never 0 (rule 4). Money raw USC; the
    route resolves which (year, week) to pass."""
    return to_jsonable(views.weekly_context(conn, iso_year, iso_week))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -k weekly_payload -v` then `uv run pytest -q`
Expected: PASS; full suite green. Paste the `-q` summary.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py tests/test_api.py
git commit -m "feat(web): weekly_payload (thin over weekly_context; gated averages stay null)"
```

---

### Task 3: `GET /api/report` + `GET /api/weekly` + `GET /api/weekly/{week}` routes

Expose the three read payloads over HTTP, mirroring the legacy Jinja routes' behaviour (latest-week resolution + `YYYY-Www` parse), which stay untouched.

**Files:**
- Modify: `src/journal/web/app.py` (three GET routes after `api_trade_detail`)

**Interfaces:**
- Consumes: `api.report_payload`, `api.weekly_payload`; `last_complete_iso_week`; `get_conn`; `datetime` (already imported at `app.py:16`).
- Produces: `GET /api/report` → JSON or 400 `{error}`; `GET /api/weekly` → the last-complete-week JSON; `GET /api/weekly/{week}` (`week` = `YYYY-Www`) → JSON, 400 `{error}` on a bad week string or on `RuntimeError` (no/multi account).

- [ ] **Step 1: Add the routes**

In `src/journal/web/app.py`, directly after the `api_trade_detail` route (it ends near `app.py:152`, before the `# --- two-step trade command` comment), add:
```python
    @app.get("/api/report")
    def api_report(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.report_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/weekly")
    def api_weekly_latest(conn: sqlite3.Connection = Depends(get_conn)):
        from ..analytics.weekly import last_complete_iso_week

        y, w = last_complete_iso_week()
        try:
            return JSONResponse(api.weekly_payload(conn, y, w))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/weekly/{week}")
    def api_weekly(week: str, conn: sqlite3.Connection = Depends(get_conn)):
        # 'YYYY-Www' → (iso_year, iso_week), validated via strptime's ISO
        # directives exactly like the legacy `_parse_week` / `cli._parse_iso_week`.
        try:
            dt = datetime.strptime(f"{week}-1", "%G-W%V-%u")
            y, w, _ = dt.isocalendar()
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

- [ ] **Step 2: Run the suite to confirm no regression**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. Paste the summary.

- [ ] **Step 3: Manual check (documented)**

Seed a temp DB with two closed trades in ISO 2026-W03 and query all three routes:
```bash
DB=/Users/reisa/.claude/jobs/86660f53/tmp/report.db
rm -f "$DB"
python3 - <<'PY'
from journal.store.db import connect
c = connect("/Users/reisa/.claude/jobs/86660f53/tmp/report.db")
c.execute("INSERT INTO accounts (login,currency,first_seen_at) VALUES (0,'USC',1)")
# Jan 15 2026 = Thu, ISO 2026-W03. close_time in ms.
import datetime as d
def ms(day,hour): return int(d.datetime(2026,1,day,hour,tzinfo=d.timezone.utc).timestamp()*1000)
c.execute("INSERT INTO trades (account_login,position_id,symbol,symbol_base,direction,status,open_time_msc,close_time_msc,duration_s,volume,open_price,close_price,sl_initial,net_profit,r_multiple,mae_r,mfe_r,magic,deal_count,rebuilt_at) VALUES (0,1,'XAUUSDc','XAUUSD','buy','closed',?,?,3599,0.1,4000.0,4010.0,NULL,250.0,NULL,NULL,NULL,NULL,2,1)",(ms(15,9),ms(15,10)))
c.execute("INSERT INTO trades (account_login,position_id,symbol,symbol_base,direction,status,open_time_msc,close_time_msc,duration_s,volume,open_price,close_price,sl_initial,net_profit,r_multiple,mae_r,mfe_r,magic,deal_count,rebuilt_at) VALUES (0,2,'BTCUSDc','BTCUSD','sell','closed',?,?,3599,0.1,60000.0,59000.0,NULL,-80.0,NULL,NULL,NULL,777,2,1)",(ms(15,9),ms(15,11)))
c.commit()
PY
JOURNAL_DB=$DB uv run journal serve & sleep 2
echo "-- /api/report:"; curl -s http://localhost:8000/api/report | python3 -m json.tool | head -30
echo "-- /api/weekly/2026-W03:"; curl -s http://localhost:8000/api/weekly/2026-W03 | python3 -c "import sys,json;d=json.load(sys.stdin);print('n_closed',d['result']['n_closed'],'net_total',d['result']['net_total'],'win_rate',d['result']['win_rate'],'weeks',d['weeks'])"
echo "-- /api/weekly (latest, expect 200 with resolved week):"; curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/weekly
echo "-- /api/weekly/garbage (expect 400):"; curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/weekly/not-a-week
kill %1
```
Expected: `/api/report` shows `report.n_closed:2`, a `series` array of two rows with `r_multiple:null`; `/api/weekly/2026-W03` shows `n_closed 2 net_total 170.0 win_rate None weeks [[2026, 3]]`; `/api/weekly` returns `200`; `/api/weekly/not-a-week` returns `400`. If the server can't run here, drive `api.report_payload`/`api.weekly_payload` against the seeded DB directly instead — do NOT fabricate output.

- [ ] **Step 4: Commit**

```bash
git add src/journal/web/app.py
git commit -m "feat(web): /api/report + /api/weekly(+/{week}) read routes"
```

---

### Task 4: Report page — tables (KPI + money + MAE/MFE + by-session/source/symbol)

Types for the full report payload, and the Report page reading `/api/report`, rendering the analytics tables at visual+data parity with `report.html`, gated client-side. Charts land in Task 5. Wire `/report`.

**Files:**
- Modify: `frontend/src/lib/types.ts` (extend `Report`; add `Bucket`, `ChartTrade`, `ReportData`)
- Create: `frontend/src/pages/Report.tsx`
- Modify: `frontend/src/App.tsx` (route `/report` → `Report`)

**Interfaces:**
- Consumes: `GET /api/report` via `useApi`; `format.ts` (`money`, `pct`, `rmult`, `isGated`).
- Produces: `Bucket`, `ChartTrade`, `ReportData` types (Task 5 reuses `ChartTrade`); the `Report` page. `by_session`/`by_source`/`by_symbol` are each `Bucket[]`.

- [ ] **Step 1: Extend `types.ts`**

Replace the existing `Report` interface (currently `frontend/src/lib/types.ts:4-12`) with the fuller shape and add the new interfaces immediately after it:
```ts
export interface Bucket {
  label: string;
  n: number;
  win_rate: number | null;
  expectancy: number | null;
  n_with_r: number;
  avg_r: number | null;
}
export interface Report {
  currency: string;
  n_total: number; n_closed: number;
  n_wins: number; n_losses: number; n_breakeven: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  avg_r: number | null; n_with_r: number;
  n_with_mae: number;
  n_with_mae_r: number; avg_mae_r: number | null;
  n_with_mfe_r: number; avg_mfe_r: number | null;
  by_session: Bucket[];
  by_source: Bucket[];
  by_symbol: Bucket[];
}
export interface ChartTrade {
  position_id: number;
  symbol_base: string;
  close_time_msc: number;
  net_profit: number;
  r_multiple: number | null;
  mae_r: number | null;
  mfe_r: number | null;
}
export interface ReportData {
  header: Header;
  report: Report;
  series: ChartTrade[];
}
```
(Extending `Report` is additive — `by_symbol`'s existing fields are unchanged, so `SymbolBars` and `DashboardData` keep compiling; `DashboardData` already references `Report`.)

- [ ] **Step 2: Create `Report.tsx`** (tables only; charts wired in Task 5)

A gated bucket greys and its averaged cells read "n/a"/"perlu ≥20", never 0 — mirroring `report.html`'s `is_gated(b.n, b.expectancy)` row grey and `gated(b.n_with_r, b.avg_r)` R cell.
```tsx
import { useApi } from "../lib/api";
import { ReportData, Bucket } from "../lib/types";
import { money, pct, rmult, isGated } from "../lib/format";

function BucketTable({ title, rows, ccy }: { title: string; rows: Bucket[]; ccy: string }) {
  return (
    <div className="glass p-4">
      <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">{title}</h2>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="text-muted text-left">
              {["", "n", "Win", "Expectancy", "Avg R"].map((h, i) => (
                <th key={i} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => {
              const rowGated = isGated(b.n, b.expectancy);
              const rGated = isGated(b.n_with_r, b.avg_r);
              return (
                <tr key={b.label} className={"border-t border-white/5 " + (rowGated ? "text-muted/60" : "")}>
                  <td className="py-2">{b.label}</td>
                  <td className="py-2 num">{b.n}</td>
                  <td className="py-2 num">{pct(b.win_rate)}</td>
                  <td className="py-2 num">{money(b.expectancy, ccy, { sign: true })}</td>
                  <td className="py-2 num">{rGated ? `n=${b.n_with_r} (≥20)` : rmult(b.avg_r)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Report() {
  const { data, error, loading } = useApi<ReportData>("/api/report");
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { report: r } = data;
  const ccy = r.currency;

  const Kv = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="flex justify-between gap-4 py-1.5 border-b border-white/5 text-[13px]">
      <span className="text-muted">{label}</span><span className="num text-right">{children}</span>
    </div>
  );
  const rGate = (n: number, avg: number | null) => (isGated(n, avg) ? `n=${n} (perlu ≥20)` : rmult(avg));

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Report</h1>
      <div className="text-[12px] text-muted mb-4">
        Coverage penuh untuk money (n={r.n_closed} closed). Net dalam {ccy} (US cents).
        Baris grey = bucket di bawah n≥20 (docs §9): count &amp; net tetap tampil, rate/rata-rata ditahan.
      </div>

      <div className="grid md:grid-cols-2 gap-4 mb-4">
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">
            Money (coverage penuh, n={r.n_closed})
          </h2>
          <Kv label="Win rate">{pct(r.win_rate)}</Kv>
          <Kv label="Profit factor">{r.profit_factor === null ? "n/a" : r.profit_factor.toFixed(2)}</Kv>
          <Kv label="Expectancy">{money(r.expectancy, ccy, { sign: true })}</Kv>
          <Kv label="Avg win"><span className="text-pos">{money(r.avg_win, ccy)}</span></Kv>
          <Kv label="Avg loss"><span className="text-neg">{money(r.avg_loss, ccy, { sign: true })}</span></Kv>
          <Kv label="W / L / BE">{r.n_wins} / {r.n_losses} / {r.n_breakeven}</Kv>
        </div>
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">
            MAE / MFE (§9: perlu n≥20, butuh candle + SL)
          </h2>
          <Kv label="Candle coverage">{r.n_with_mae} / {r.n_closed} closed</Kv>
          <Kv label="Avg MAE (R)">{rGate(r.n_with_mae_r, r.avg_mae_r)}</Kv>
          <Kv label="Avg MFE (R)">{rGate(r.n_with_mfe_r, r.avg_mfe_r)}</Kv>
          <Kv label="Avg R (akun)">{rGate(r.n_with_r, r.avg_r)}</Kv>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-4">
        <BucketTable title="Per session (UTC)" rows={r.by_session} ccy={ccy} />
        <BucketTable title="Per source (EA = magic≠0)" rows={r.by_source} ccy={ccy} />
        <BucketTable title="Per symbol" rows={r.by_symbol} ccy={ccy} />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire the `/report` route**

In `frontend/src/App.tsx` add `import Report from "./pages/Report";` and change `<Route path="/report" element={<Placeholder name="Report" />} />` to `<Route path="/report" element={<Report />} />`.

- [ ] **Step 4: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0 (no unused imports, no type errors — `series` is intentionally unused until Task 5; if the linter flags it, destructure only `report` here and add `series` back in Task 5); Vitest green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/pages/Report.tsx frontend/src/App.tsx
git commit -m "feat(web): Report page — KPI + MAE/MFE + by-session/source/symbol tables (client gating)"
```

---

### Task 5: Three analytics charts (R-histogram, MAE/MFE scatter, calendar heatmap)

Pure geometry helpers (TDD with Vitest) + three presentational chart components rendering client-side from the raw `series`, wired into the Report page. R-unknown / MAE-unknown trades are dropped per chart (rule 4), never plotted as 0. Colours follow the existing pos/neg + violet→cyan tokens (dataviz: consistent palette, legible in the dark theme).

**Files:**
- Create: `frontend/src/lib/charts.ts` (pure helpers)
- Create: `frontend/src/lib/charts.test.ts` (Vitest)
- Create: `frontend/src/components/RHistogram.tsx`
- Create: `frontend/src/components/MaeMfeScatter.tsx`
- Create: `frontend/src/components/CalendarHeatmap.tsx`
- Modify: `frontend/src/pages/Report.tsx` (render the three charts from `series`)

**Interfaces:**
- Consumes: `ChartTrade` (Task 4); `format.ts` (`money`, `rmult`, `wib`).
- Produces:
  - `histogramBins(values: number[]): { from: number; to: number; label: string; count: number }[]` — fixed R bins with open ends: `(-∞,-2), [-2,-1), [-1,0), [0,1), [1,2), [2,3), [3,∞)`. Every bin always present (count 0 allowed) so the axis is stable.
  - `dayStartUtcMs(msc: number): number` — floor an epoch-ms to UTC midnight.
  - `calendarCells(series: {close_time_msc: number; net_profit: number}[]): { day_ms: number; net: number; n: number }[]` — group by UTC day, summed net + trade count, ascending by day.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/charts.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { histogramBins, dayStartUtcMs, calendarCells } from "./charts";

describe("charts", () => {
  it("histogramBins: fixed bins with open ends, all present, correct counts", () => {
    const bins = histogramBins([-5, -1.5, -0.2, 0.5, 1.5, 2.5, 9]);
    expect(bins.length).toBe(7);
    const counts = bins.map((b) => b.count);
    // (-inf,-2):[-5]  [-2,-1):[-1.5]  [-1,0):[-0.2]  [0,1):[0.5]
    // [1,2):[1.5]  [2,3):[2.5]  [3,inf):[9]
    expect(counts).toEqual([1, 1, 1, 1, 1, 1, 1]);
    expect(bins[3].label).toBe("[0,1)");
  });

  it("histogramBins: boundary values land in the LEFT-closed bin", () => {
    // 0 → [0,1) not [-1,0);  1 → [1,2);  -1 → [-1,0)
    const b = histogramBins([0, 1, -1]);
    expect(b[2].count).toBe(1); // [-1,0): the -1
    expect(b[3].count).toBe(1); // [0,1): the 0
    expect(b[4].count).toBe(1); // [1,2): the 1
  });

  it("dayStartUtcMs: floors to UTC midnight", () => {
    const noon = Date.UTC(2026, 0, 15, 12, 30, 0); // 2026-01-15 12:30 UTC
    expect(dayStartUtcMs(noon)).toBe(Date.UTC(2026, 0, 15, 0, 0, 0));
  });

  it("calendarCells: groups by UTC day, sums net, counts, ascending", () => {
    const d15a = Date.UTC(2026, 0, 15, 9);
    const d15b = Date.UTC(2026, 0, 15, 20);
    const d16 = Date.UTC(2026, 0, 16, 3);
    const cells = calendarCells([
      { close_time_msc: d16, net_profit: -10 },
      { close_time_msc: d15a, net_profit: 250 },
      { close_time_msc: d15b, net_profit: -50 },
    ]);
    expect(cells.length).toBe(2);
    expect(cells[0]).toEqual({ day_ms: Date.UTC(2026, 0, 15), net: 200, n: 2 });
    expect(cells[1]).toEqual({ day_ms: Date.UTC(2026, 0, 16), net: -10, n: 1 });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test`
Expected: FAIL — cannot resolve `./charts` (module not created yet).

- [ ] **Step 3: Implement the helpers**

Create `frontend/src/lib/charts.ts`:
```ts
// Pure geometry helpers for the /report analytics charts. Client computes
// binning/bucketing from the raw server series (rule: charts render client-side
// from raw numbers). null R/MAE/MFE trades are filtered by the caller BEFORE
// these run — these never see nulls and never invent a 0.

const BIN_EDGES = [-Infinity, -2, -1, 0, 1, 2, 3, Infinity];

export function histogramBins(
  values: number[],
): { from: number; to: number; label: string; count: number }[] {
  const bins = BIN_EDGES.slice(0, -1).map((from, i) => {
    const to = BIN_EDGES[i + 1];
    const label =
      from === -Infinity ? `(-∞,${to})`
      : to === Infinity ? `[${from},∞)`
      : `[${from},${to})`;
    return { from, to, label, count: 0 };
  });
  for (const v of values) {
    // left-closed, right-open: find the bin where from <= v < to.
    const idx = bins.findIndex((b) => v >= b.from && v < b.to);
    if (idx >= 0) bins[idx].count += 1;
  }
  return bins;
}

export function dayStartUtcMs(msc: number): number {
  const DAY = 86_400_000;
  return Math.floor(msc / DAY) * DAY;
}

export function calendarCells(
  series: { close_time_msc: number; net_profit: number }[],
): { day_ms: number; net: number; n: number }[] {
  const byDay = new Map<number, { day_ms: number; net: number; n: number }>();
  for (const t of series) {
    const day = dayStartUtcMs(t.close_time_msc);
    const cell = byDay.get(day) ?? { day_ms: day, net: 0, n: 0 };
    cell.net += t.net_profit;
    cell.n += 1;
    byDay.set(day, cell);
  }
  return [...byDay.values()].sort((a, b) => a.day_ms - b.day_ms);
}
```
> Note: `Math.floor(msc / DAY) * DAY` floors to UTC midnight because epoch 0 is itself a UTC midnight and `DAY` evenly divides every UTC midnight — no timezone library needed (server clock is UTC, `offset_s = 0`).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test`
Expected: PASS (all `charts` + existing `format`/`parse` tests green).

- [ ] **Step 5: Create `RHistogram.tsx`**

Bars scaled to the tallest bin; win bins (R≥0) cyan, loss bins violet-muted. Shows total `n` and greys the whole chart with a note when `n < 20` (§9 honesty — a distribution of <20 points is shown but flagged thin).
```tsx
import { ChartTrade } from "../lib/types";
import { histogramBins } from "../lib/charts";

export default function RHistogram({ series }: { series: ChartTrade[] }) {
  const values = series.map((t) => t.r_multiple).filter((r): r is number => r !== null);
  const bins = histogramBins(values);
  const max = Math.max(1, ...bins.map((b) => b.count));
  const thin = values.length < 20;
  return (
    <div className="glass p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted">Distribusi R</h2>
        <span className={"text-[11px] " + (thin ? "text-muted/60" : "text-muted")}>
          n={values.length}{thin ? " (perlu ≥20)" : ""}
        </span>
      </div>
      {values.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center">Belum ada trade dengan R diketahui.</div>
      ) : (
        <div className={"flex items-end gap-1.5 h-[140px] " + (thin ? "opacity-60" : "")}>
          {bins.map((b) => (
            <div key={b.label} className="flex-1 flex flex-col items-center justify-end gap-1">
              <span className="text-[10px] num text-muted">{b.count || ""}</span>
              <div className={"w-full rounded-t " + (b.from >= 0 ? "bg-cyan/70" : "bg-violet/60")}
                   style={{ height: `${(b.count / max) * 100}%` }} title={`${b.label}: ${b.count}`} />
              <span className="text-[8.5px] num text-muted whitespace-nowrap">{b.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Create `MaeMfeScatter.tsx`**

Plots one dot per trade that has BOTH `mae_r` and `mfe_r`. x = MAE (R, ≤0 typically), y = MFE (R, ≥0). A trade missing either is dropped (rule 4). Dot colour by realized net sign.
```tsx
import { ChartTrade } from "../lib/types";

export default function MaeMfeScatter({ series }: { series: ChartTrade[] }) {
  const pts = series.filter(
    (t): t is ChartTrade & { mae_r: number; mfe_r: number } =>
      t.mae_r !== null && t.mfe_r !== null,
  );
  const W = 320, H = 200, pad = 28;
  const xs = pts.map((p) => p.mae_r);
  const ys = pts.map((p) => p.mfe_r);
  const xmin = Math.min(0, ...xs), xmax = Math.max(0, ...xs);
  const ymin = Math.min(0, ...ys), ymax = Math.max(0, ...ys);
  const xspan = xmax - xmin || 1, yspan = ymax - ymin || 1;
  const X = (v: number) => pad + (W - 2 * pad) * (v - xmin) / xspan;
  const Y = (v: number) => H - pad - (H - 2 * pad) * (v - ymin) / yspan;
  return (
    <div className="glass p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted">MAE vs MFE (R)</h2>
        <span className="text-[11px] text-muted">n={pts.length}</span>
      </div>
      {pts.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center">Belum ada trade dengan MAE &amp; MFE (perlu candle + SL).</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[200px]">
          <line x1={X(0)} y1={pad} x2={X(0)} y2={H - pad} stroke="rgba(255,255,255,0.15)" strokeWidth="1" />
          <line x1={pad} y1={Y(0)} x2={W - pad} y2={Y(0)} stroke="rgba(255,255,255,0.15)" strokeWidth="1" />
          {pts.map((p) => (
            <circle key={p.position_id} cx={X(p.mae_r)} cy={Y(p.mfe_r)} r="3.5"
                    className={p.net_profit >= 0 ? "fill-pos/80" : "fill-neg/80"}>
              <title>#{p.position_id} {p.symbol_base}: MAE {p.mae_r}R, MFE {p.mfe_r}R</title>
            </circle>
          ))}
          <text x={W - pad} y={Y(0) - 4} textAnchor="end" className="fill-muted text-[9px]">MAE →</text>
          <text x={X(0) + 4} y={pad + 8} className="fill-muted text-[9px]">MFE ↑</text>
        </svg>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Create `CalendarHeatmap.tsx`**

One cell per UTC day that has trades (a compact strip, not a full month grid — this account trades sparsely). Green→red by net sign, opacity by magnitude relative to the busiest day. Uses `calendarCells` + `money`/`wib`.
```tsx
import { ChartTrade } from "../lib/types";
import { calendarCells } from "../lib/charts";
import { money, wib } from "../lib/format";

export default function CalendarHeatmap(
  { series, currency, offsetS }: { series: ChartTrade[]; currency: string; offsetS: number },
) {
  const cells = calendarCells(series);
  const maxAbs = Math.max(1, ...cells.map((c) => Math.abs(c.net)));
  return (
    <div className="glass p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted">Kalender P&amp;L harian</h2>
        <span className="text-[11px] text-muted">{cells.length} hari · net dalam {currency}</span>
      </div>
      {cells.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center">Belum ada trade tertutup.</div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {cells.map((c) => {
            const alpha = 0.18 + 0.72 * (Math.abs(c.net) / maxAbs);
            const bg = c.net >= 0
              ? `rgba(52,211,153,${alpha})` : `rgba(251,113,133,${alpha})`;
            const day = wib(c.day_ms, offsetS).slice(0, 10); // date part only
            return (
              <div key={c.day_ms} className="w-9 h-9 rounded flex items-center justify-center text-[8.5px] num text-ink"
                   style={{ backgroundColor: bg }}
                   title={`${day}: ${money(c.net, currency, { sign: true })} · ${c.n} trade`}>
                {c.n}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```
> The cell label is the trade COUNT (activity); colour carries net sign+magnitude (P&L). Hover gives the exact USC figure and date. This keeps the money figure exact-on-demand rather than colour-only.

- [ ] **Step 8: Render the three charts in `Report.tsx`**

Add the imports at the top of `frontend/src/pages/Report.tsx`:
```tsx
import RHistogram from "../components/RHistogram";
import MaeMfeScatter from "../components/MaeMfeScatter";
import CalendarHeatmap from "../components/CalendarHeatmap";
```
Change the destructure `const { report: r } = data;` to also pull the header + series:
```tsx
  const { header, report: r, series } = data;
```
Then, immediately BEFORE the final closing `</div>` of the returned JSX (after the three `BucketTable`s' grid), insert:
```tsx
      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <RHistogram series={series} />
        <MaeMfeScatter series={series} />
      </div>
      <div className="mt-4">
        <CalendarHeatmap series={series} currency={r.currency} offsetS={header.offset_s} />
      </div>
```

- [ ] **Step 9: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0 (all of `header`/`series` now consumed — no unused-var error); Vitest green (charts + format + parse).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/lib/charts.ts frontend/src/lib/charts.test.ts \
  frontend/src/components/RHistogram.tsx frontend/src/components/MaeMfeScatter.tsx \
  frontend/src/components/CalendarHeatmap.tsx frontend/src/pages/Report.tsx
git commit -m "feat(web): Report charts — R-histogram, MAE/MFE scatter, daily P&L calendar (client-rendered)"
```

---

### Task 6: Weekly page — week picker + redirect to last complete week

Types for the weekly payload, and the Weekly page. `/weekly` (no param) fetches the server-resolved latest week and redirects to its dated URL (`/weekly/YYYY-Www`, matching the Jinja redirect); `/weekly/:week` shows that week. Wire `/weekly/:week?`, swap both placeholders.

**Files:**
- Modify: `frontend/src/lib/types.ts` (add `TradeNote`, `WeeklyResult`, `WeeklyData`)
- Create: `frontend/src/pages/Weekly.tsx`
- Modify: `frontend/src/App.tsx` (route `/weekly` + `/weekly/:week` → `Weekly`)

**Interfaces:**
- Consumes: `GET /api/weekly` and `GET /api/weekly/{week}` via `useApi`; `format.ts` (`money`, `pct`); `useParams`/`useNavigate`/`Link` from react-router; `Bucket` (Task 4).
- Produces: `TradeNote`, `WeeklyResult`, `WeeklyData` types; the `Weekly` page.

- [ ] **Step 1: Extend `types.ts`**

Append at the end of `frontend/src/lib/types.ts`:
```ts
export interface TradeNote {
  position_id: number;
  symbol_base: string;
  net_profit: number;
  setup: string | null;
  confidence: number | null;
  emotion: string | null;
  followed_plan: number | null;  // 0 | 1 | null
  notes: string | null;
  tags: string[];
}
export interface WeeklyResult {
  account_login: number;
  currency: string;
  iso_year: number;
  iso_week: number;
  start_msc: number;
  end_msc: number;
  n_closed: number;
  n_wins: number;
  n_losses: number;
  n_breakeven: number;
  net_total: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  by_session: Bucket[];
  by_source: Bucket[];
  notes: TradeNote[];
}
export interface WeeklyData {
  header: Header;
  result: WeeklyResult;
  weeks: [number, number][];
  start_ms: number;
}
```

- [ ] **Step 2: Create `Weekly.tsx`**

`/weekly` (no `:week`) fetches `/api/weekly`, then redirects to the resolved week's dated URL so the address bar is shareable (parity with the Jinja 302). A dated URL fetches that week directly.
```tsx
import { useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useApi } from "../lib/api";
import { WeeklyData } from "../lib/types";
import { money, pct } from "../lib/format";

const wk = (y: number, w: number) => `${y}-W${String(w).padStart(2, "0")}`;

export default function Weekly() {
  const { week } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useApi<WeeklyData>(week ? `/api/weekly/${week}` : "/api/weekly");

  // No week in the URL → redirect to the server-resolved latest week's dated URL.
  useEffect(() => {
    if (!week && data) navigate(`/weekly/${wk(data.result.iso_year, data.result.iso_week)}`, { replace: true });
  }, [week, data, navigate]);

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, result: r, weeks } = data;
  const ccy = r.currency;
  const tone = r.net_total > 0 ? "text-pos" : r.net_total < 0 ? "text-neg" : "";

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Weekly · {wk(r.iso_year, r.iso_week)}</h1>
      <div className="text-[12px] text-muted mb-4">Mon–Sun UTC · trade diatribusikan ke minggu saat ditutup (realized).</div>

      {weeks.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {weeks.map(([y, w]) => {
            const active = y === r.iso_year && w === r.iso_week;
            return (
              <Link key={wk(y, w)} to={`/weekly/${wk(y, w)}`}
                className={"px-2.5 py-1 rounded-full text-[11px] ring-1 num " +
                  (active ? "bg-violet/20 ring-violet/45 text-ink" : "bg-white/5 ring-panel-border text-muted")}>
                {wk(y, w)}
              </Link>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">Realized net</div>
          <div className={"text-[18px] font-bold num mt-1 " + tone}>{money(r.net_total, ccy, { sign: true })}</div>
          <div className="text-[11px] text-muted mt-0.5">selalu ditampilkan (bukan gated)</div>
        </div>
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">Closed</div>
          <div className="text-[18px] font-bold num mt-1">{r.n_closed}</div>
        </div>
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">W / L / BE</div>
          <div className="text-[18px] font-bold num mt-1">
            <span className="text-pos">{r.n_wins}</span>/<span className="text-neg">{r.n_losses}</span>/{r.n_breakeven}
          </div>
        </div>
        <div className="glass p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted">Win rate</div>
          <div className="text-[18px] font-bold num mt-1">{pct(r.win_rate)}</div>
          <div className="text-[11px] text-muted mt-0.5">n={r.n_closed}{r.n_closed < 20 ? ", perlu ≥20" : ""}</div>
        </div>
      </div>

      <div className="glass p-4 mb-4">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Money (§9-gated di level minggu)</h2>
        <div className="grid grid-cols-2 gap-x-6 text-[13px]">
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Avg win</span><span className="num">{money(r.avg_win, ccy)}</span></div>
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Avg loss</span><span className="num">{money(r.avg_loss, ccy, { sign: true })}</span></div>
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Profit factor</span><span className="num">{r.profit_factor === null ? "n/a" : r.profit_factor.toFixed(2)}</span></div>
          <div className="flex justify-between py-1.5 border-b border-white/5"><span className="text-muted">Expectancy</span><span className="num">{money(r.expectancy, ccy, { sign: true })}</span></div>
        </div>
        <p className="text-[11px] text-muted mt-2">Satu minggu jarang mencapai n≥20, jadi rate/rata-rata umumnya "n/a" — itu jujur, bukan bug.</p>
      </div>

      <div className="glass p-4">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">
          Notes ({r.notes.length} trade dengan anotasi / tag manual)
        </h2>
        {r.notes.length === 0 ? (
          <div className="text-muted text-sm py-4">Belum ada trade beranotasi minggu ini.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr className="text-muted text-left">
                  {["Trade", "Net", "Setup", "Conf", "Emosi", "Plan", "Catatan", "Tags"].map((h, i) => (
                    <th key={i} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.notes.map((n) => (
                  <tr key={n.position_id} className="border-t border-white/5">
                    <td className="py-2">
                      <Link className="text-cyan hover:underline" to={`/trades/${n.position_id}`}>{n.symbol_base} #{n.position_id}</Link>
                    </td>
                    <td className={"py-2 num " + (n.net_profit > 0 ? "text-pos" : n.net_profit < 0 ? "text-neg" : "")}>
                      {money(n.net_profit, ccy, { sign: true })}
                    </td>
                    <td className="py-2">{n.setup ?? "—"}</td>
                    <td className="py-2 num">{n.confidence ?? "—"}</td>
                    <td className="py-2">{n.emotion ?? "—"}</td>
                    <td className="py-2">{n.followed_plan === 1 ? "ya" : n.followed_plan === 0 ? "tidak" : "—"}</td>
                    <td className="py-2">{n.notes ?? "—"}</td>
                    <td className="py-2">
                      <span className="flex flex-wrap gap-1">
                        {n.tags.map((t) => <span key={t} className="px-1.5 py-0.5 rounded text-[10px] bg-white/6 text-muted">{t}</span>)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
```
> `followed_plan` renders as a strict tri-state (`1`→ya, `0`→tidak, `null`→—) — rule 4: a null plan flag is "not recorded", never "tidak". `confidence ?? "—"` shows `—` only for `null`; a real `0` would show `0` (confidence is 1–5 so `0` never occurs, but the operator is null-safe, not falsy).

- [ ] **Step 3: Wire the routes**

In `frontend/src/App.tsx` add `import Weekly from "./pages/Weekly";` and replace `<Route path="/weekly" element={<Placeholder name="Weekly" />} />` with two routes:
```tsx
          <Route path="/weekly" element={<Weekly />} />
          <Route path="/weekly/:week" element={<Weekly />} />
```

- [ ] **Step 4: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0; Vitest green.

- [ ] **Step 5: Manual check (documented)**

Reuse the Task 3 seed DB (`report.db`, two closed trades in 2026-W03). Rebuild the frontend and open the SPA:
```bash
npm --prefix frontend run build
JOURNAL_DB=/Users/reisa/.claude/jobs/86660f53/tmp/report.db uv run journal serve & sleep 2
echo "-- /app/weekly should redirect to a dated week; the API it reads:"; curl -s http://localhost:8000/api/weekly | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['iso_year'],d['result']['iso_week'])"
kill %1
```
Then in a browser: open `http://localhost:8000/app/weekly` → confirm the URL becomes `/app/weekly/YYYY-Www` and the page renders; open `http://localhost:8000/app/report` → confirm the tables + three charts render, gated buckets grey, and a null-R trade is absent from the histogram (not a 0 bar). Do NOT fabricate — paste what you observe (or, if no browser here, paste the `/api/weekly` + `/api/report` JSON that backs each page).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/pages/Weekly.tsx frontend/src/App.tsx
git commit -m "feat(web): Weekly page — week picker + redirect to last complete ISO week"
```

---

### Task 7: Phase 2/3 minor fold-ins (confidence validation, form resync, tag guard, chart fallback)

Four small hardening fixes carried over from the Phase 3 PR body. Each is independent; keep them in one task/commit.

**Files:**
- Modify: `frontend/src/components/AnnotationForm.tsx` (confidence client validation #1; state resync #2)
- Modify: `frontend/src/components/TagEditor.tsx` (in-flight guard #3)
- Modify: `frontend/src/pages/TradeDetail.tsx` (chart `onError` → state fallback #4)

(Follow-up #2b — keying the detail route by `:id` — is resolved WITHOUT an `App.tsx` change; see Step 3.)

**Interfaces:**
- Consumes: existing `AnnotationForm`/`TagEditor`/`TradeDetail` props (unchanged); `useState`/`useEffect` from React.
- Produces: no new exported interface — behaviour-only hardening.

- [ ] **Step 1: Fix #1 — friendly confidence validation in `AnnotationForm.tsx`**

A non-integer or out-of-range confidence currently reaches FastAPI as a raw `422` (the route param is `int`), so the form shows "HTTP 422" instead of the domain's "confidence must be 1-5". Validate on the client BEFORE submit — without silently rounding (that would hide the user's intent). In `frontend/src/components/AnnotationForm.tsx`, inside `submit`, replace the `confidence` line of the `body` object and add a guard just before building `body`:
```tsx
    const confRaw = confidence.trim();
    if (confRaw !== "") {
      const c = Number(confRaw);
      if (!Number.isInteger(c) || c < 1 || c > 5) {
        setSaving(false);
        setErr("confidence harus bilangan bulat 1–5 (kosongkan bila belum dicatat)");
        return;
      }
    }
```
and set `confidence: confRaw === "" ? null : Number(confRaw),` in the `body`. (Keep the rest of `body` — `setup`/`emotion`/`followed_plan`/`notes` — exactly as is: `""` → `null`, rule 4.)

- [ ] **Step 2: Fix #2a — resync `AnnotationForm` state when the `annotation` prop changes**

The form seeds its fields from props via `useState` initializers with no resync — stale if the same component instance ever receives a new trade's annotation. Add, immediately after the last `useState` in `AnnotationForm` (before `submit`):
```tsx
  useEffect(() => {
    setSetup(a?.setup ?? "");
    setConfidence(a?.confidence != null ? String(a.confidence) : "");
    setEmotion(a?.emotion ?? "");
    setFp(a?.followed_plan === 1 ? "yes" : a?.followed_plan === 0 ? "no" : "");
    setNotes(a?.notes ?? "");
  }, [a]);
```
and add `useEffect` to the React import: `import { useEffect, useState } from "react";`.

- [ ] **Step 3: Fix #2b — key the detail route by `:id`**

In `frontend/src/App.tsx`, so navigating trade→trade remounts the page (belt-and-braces with Step 2), change the detail route to carry a key. Since `<Route>` can't take a runtime key from params directly, wrap the element:
```tsx
          <Route path="/trades/:id" element={<TradeDetail />} />
```
stays as-is; instead, at the TOP of `frontend/src/pages/TradeDetail.tsx`'s component body, the `useApi(`/api/trades/${id}`)` path already changes with `id`, and Step 2 resyncs the form — so no key is needed. **Skip the App.tsx change; the param-keyed `useApi` path + the Step-2 resync fully cover it.** (Documented here so the reviewer sees the follow-up was considered and resolved without a redundant remount.)

- [ ] **Step 4: Fix #3 — in-flight guard in `TagEditor.tsx`**

`add`/`del` can double-POST. Mirror the Live/Commands double-submit latch. In `frontend/src/components/TagEditor.tsx`, add a `busy` state and gate both handlers:
```tsx
  const [busy, setBusy] = useState(false);
```
Wrap `add`'s body:
```tsx
  const add = async () => {
    if (busy || newTag.trim() === "") return;
    setBusy(true); setErr(null);
    const r = await postJson(`/api/trades/${positionId}/tags`, { tag: newTag.trim() });
    setBusy(false);
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    setNewTag(""); onChanged();
  };
```
and `del`:
```tsx
  const del = async (tag: string) => {
    if (busy) return;
    setBusy(true); setErr(null);
    const r = await postJson(`/api/trades/${positionId}/tags/delete`, { tag });
    setBusy(false);
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    onChanged();
  };
```
Also add `disabled={busy}` to the "Tambah" button.

- [ ] **Step 5: Fix #4 — state-based chart fallback in `TradeDetail.tsx`**

Replace the imperative `onError` DOM mutation with React state. In `frontend/src/pages/TradeDetail.tsx`, add near the other hooks:
```tsx
  const [chartFailed, setChartFailed] = useState(false);
```
(and ensure `useState` is imported: `import { useState } from "react";` alongside the existing imports). Then replace the chart `<img …>`/`onError` block with:
```tsx
          {chartFailed ? (
            <p className="text-[12px] text-muted">Chart belum tersedia — jalankan <code>uv run journal candles</code> lalu buka lagi.</p>
          ) : (
            <img className="w-full rounded" src={`/trades/${trade.position_id}/chart.png`}
              alt={`chart trade ${trade.position_id}`} onError={() => setChartFailed(true)} />
          )}
```
(Keep the outer `chartable ? (…) : (<p>Hanya trade closed…</p>)` conditional; this replaces only the inner `<img>` branch.)

- [ ] **Step 6: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0 (no unused imports; `useEffect`/`useState` all consumed); Vitest green.

- [ ] **Step 7: Manual check (documented)**

With a served DB that has one closed trade, open `/app/trades/<id>` and confirm: entering confidence `3.5` or `9` shows the friendly "confidence harus bilangan bulat 1–5" message (not "HTTP 422") and writes nothing; adding a tag twice quickly inserts it once; a trade with no candle window shows the "Chart belum tersedia" note (not a broken image). Paste what you observe (or drive the annotate route with `confidence:3.5` via curl and show the client-side guard code path if no browser).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/AnnotationForm.tsx frontend/src/components/TagEditor.tsx frontend/src/pages/TradeDetail.tsx
git commit -m "fix(web): Phase 3 follow-ups — confidence validation, form resync, tag in-flight guard, chart fallback"
```

---

## Self-Review

- **Spec coverage (Phase 4, spec §6 row 4 "Report + Weekly + calendar heatmap, R-histogram, MAE/MFE scatter + APIs"; /commands already shipped Phase 2):**
  - `/report` page — money KPIs, MAE/MFE, by-session/by-source/by-symbol tables with §9 gating (Tasks 1,4) ✓
  - `/weekly/:week?` page — week picker, redirect to last complete ISO week (Tasks 2,6) ✓
  - three analytics charts — R-histogram, MAE/MFE scatter, daily-P&L calendar heatmap, client-rendered from raw series (Tasks 1,5) ✓
  - APIs `/api/report`, `/api/weekly`, `/api/weekly/{week}` — thin wrappers over `report_context`/`weekly_context` + the new `analytics_series_context` (Tasks 1,2,3) ✓
  - the two architecture decisions (§2.1) — gating stays client-side; charts render client-side from raw arrays — implemented as decided ✓
  - App.tsx placeholders for `/report` and `/weekly` swapped for real pages (Tasks 4,6) ✓
  - five Phase 2/3 minor follow-ups folded in (Tasks 1 #5, 7 #1–#4) ✓
- **Placeholder scan:** no TBD/TODO; every code step carries complete code; the `_seed_trade` replacement, the `Report` interface replacement, and the `Report.tsx`/`TradeDetail.tsx` edits are shown in full or as exact insert points.
- **Type consistency:** Python `report_payload` `{header, report, series}` matches TS `ReportData`; `report.by_session/by_source/by_symbol` are `Bucket[]` matching `ReportResult`/`BucketStat` fields (`label,n,win_rate,expectancy,n_with_r,avg_r`); `series` rows match `ChartTrade` (`position_id,symbol_base,close_time_msc,net_profit,r_multiple,mae_r,mfe_r`); `weekly_payload` `{header, result, weeks, start_ms}` matches `WeeklyData`, and `result` matches `WeeklyResult`/`TradeNote` (`notes[].tags` is `string[]`, matching `TradeNote.tags = tuple[str,...]`); the chart helpers (`histogramBins`, `dayStartUtcMs`, `calendarCells`) have identical signatures in `charts.ts`, `charts.test.ts`, and their three consumers; route URL segments (`/api/report`, `/api/weekly`, `/api/weekly/{week}`) match the frontend `useApi` paths.
- **Rule-4 note for the executor:** the charts are the integrity surface this phase. Do NOT mark Task 5 complete without confirming a trade with `r_multiple === null` is ABSENT from the histogram (never a 0-bar) and a trade missing `mae_r` or `mfe_r` is ABSENT from the scatter — the `.filter(r !== null)` / `mae_r !== null && mfe_r !== null` guards are exactly this. Gated averages must render "n/a"/"perlu ≥20", never 0.
- **Gating honesty:** every bucket/stat renders with its `n`; a bucket with `n < 20` greys (row) and its averaged cells read the gated text — mirroring `report.html` and the recorded §2.1 client-side decision.
