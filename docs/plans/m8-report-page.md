# M8 — `/report` page + per-symbol breakdown

**Goal.** Add the one analytics cut the report is missing — **`by_symbol`** (grouped
by `symbol_base`, CLAUDE.md rule 11 / trap 12) — and split the web so the
**dashboard** stays an at-a-glance card view while a new **`/report`** page carries
the full deep tables (money, MAE/MFE, by-session, by-source, by-symbol).

**Non-goals.** No new DB columns (rule 2 — derive, don't backfill). No schema
migration. No new dependency (rule 8). No equity curve / tag analytics (separate
milestones). No adapter or MT5 import anywhere in this work (rules 1 & 12).

This is a **copy-from-existing-code** plan. Every new piece mirrors a piece that
already ships. Cite the source line when you write it; do not invent APIs.

---

## Phase 0 — Discovery (DONE; this is the consolidated result)

Read directly during planning — no assumptions carried forward. Allowed APIs and
the exact shapes to copy:

### Data model facts (measured)
- `trades.symbol_base` is a stored `TEXT NOT NULL` column, already indexed by
  `ix_trades_symbol (account_login, symbol_base, open_time_msc)`
  (`store/schema.sql:141,160,202`). **Group by this column directly — it is
  already normalised. Do NOT call `domain/symbols.py` or re-normalise `symbol`.**
- Symbols in this account: `XAUUSD`, `BTCUSD`, `EURUSD` (from `XAUUSDc` etc.).
  Data-driven, not a fixed set — see the ordering note in Phase 1.
- `net_profit` full coverage; `r_multiple` sparse (§9). Money is in account
  currency `USC` — never print a bare `$` (CLAUDE.md "This account").

### `analytics/report.py` — the shape to extend
- `BucketStat` (frozen dataclass, `report.py:32`): `label, n, win_rate,
  expectancy, n_with_r, avg_r`. **Reuse as-is for symbols — no new type.**
- `bucket_stat(label, rows) -> BucketStat` (`report.py:85`): does §9 gating and
  `_TOL` win classification. **Call it per symbol; do not re-implement gating.**
- `ReportResult` (`report.py:50`) already carries `by_session` and `by_source`
  as `tuple[BucketStat, ...]` in fixed order (`report.py:81-82`).
- `build_report(conn)` (`report.py:116`): the SELECT at `report.py:130-134`
  fetches `net_profit, r_multiple, mae, mae_r, mfe_r, open_time_msc, magic` — it
  does **not** yet select `symbol_base`. by_session/by_source built at
  `report.py:173-190` by bucketing the already-fetched `rows`.

### CLI renderer — the block to mirror
- `report()` (`cli.py:476`) prints sections with `typer.echo`. by_source block is
  `cli.py:524-526` (`for b in r.by_source: typer.echo(_bucket_line(b, currency))`).
- Helpers: `_fmt` (`cli.py:443`), `_gated` (`cli.py:453`), `_bucket_line`
  (`cli.py:461`). **Reuse `_bucket_line` for the symbol rows.**

### Web layer — the wiring to mirror
- Route pattern: dashboard route `app.py:71-78` — `try: ctx =
  views.dashboard_context(conn); ctx["header"] = views.account_header(conn)
  except RuntimeError as e: return error_page(...)`, then
  `render(request, "dashboard.html", ctx)`.
- Context builder: `views.dashboard_context(conn)` (`views.py:40`) returns
  `{"report": build_report(conn)}` — nothing more. `account_header` (`views.py:25`).
- Jinja env (`app.py:46-54`): filters `money, pct, rmult, num, gated, wib, dur,
  price`; **globals** `gated`, `is_gated` (called as functions with two args).
- Template to copy: `templates/dashboard.html` — has the `bucket_rows(buckets,
  ccy)` macro (`:4-14`), the Money panel (`:52-58`), MAE/MFE panel (`:60-67`),
  by-session table (`:69-75`), by-source table (`:77-83`), and the §9 note
  (`:85-87`). The KPI cards (`:20-41`) and Outcomes cards (`:43-50`) stay on the
  dashboard.
- Nav: `templates/base.html:12-16` — `Dashboard | Trades | Weekly`.
- Formatters (`web/format.py`): `money(x, ccy, *, sign)`, `pct`, `rmult`, `num`,
  `gated(n, avg, *, unit)`, `is_gated(n, avg)`. Copy filter usage verbatim from
  dashboard.html — do not hand-format numbers in the template.

### Tests — where they live
- `tests/test_report.py` (21 tests) exercises `build_report` against a seeded DB.
- `tests/test_web.py` (14 tests) exercises context builders + routes via
  FastAPI `TestClient`.

### Anti-patterns to guard (grep-checkable)
- ❌ `GROUP BY symbol` / bucketing on the verbatim `symbol` — MUST be
  `symbol_base` (rule 11, trap 12). Guard: `grep -n "symbol_base" report.py`
  present; no bucketing keyed on `r["symbol"]`.
- ❌ importing/using `domain/symbols.py` in report.py — the column is pre-normalised.
- ❌ assuming exactly 3 symbols or a hardcoded symbol order.
- ❌ gating the raw `n` / `n_with_r` — only *averages* gate (BucketStat contract,
  `report.py:39-47`).
- ❌ `==`/`<`/`>` on a raw money REAL — win/loss goes through `_TOL` (rule 5); by
  reusing `bucket_stat` you inherit this, so **do not** re-classify in the route.
- ❌ a second SQL read in the `/report` route/template — reuse `build_report`.

---

## Phase 1 — `by_symbol` in the analytics core (TDD; do this first)

**CLAUDE.md rule 7: tests before implementation for `analytics/`.**

### 1a. Write failing tests — `tests/test_report.py`
Mirror the existing by_session/by_source assertions. Add tests that:
1. `build_report(conn).by_symbol` is a `tuple[BucketStat, ...]`.
2. There is exactly one bucket per distinct `symbol_base` among **closed** trades.
3. Buckets are ordered by `symbol_base` **ascending** (deterministic;
   see 1b rationale).
4. `sum(b.n for b in by_symbol) == r.n_closed` (every closed trade lands in
   exactly one symbol bucket).
5. A symbol with `n < 20` has `win_rate is None` and `expectancy is None` but a
   truthful raw `n` (gating parity with by_session).
6. Empty DB → `by_symbol == ()`.

Run `uv run pytest -k report` → these must FAIL (AttributeError / missing field).

### 1b. Implement — `analytics/report.py`
Copy the by_source pattern exactly:
1. Add `symbol_base` to the SELECT column list at `report.py:131`.
2. Add field to `ReportResult` after `by_source` (`report.py:82`):
   `by_symbol: tuple[BucketStat, ...]`. Update the docstring at `report.py:78-80`
   to note by_symbol is data-driven, `symbol_base`-ascending.
3. After the by_source block (`report.py:190`), build by_symbol:
   ```python
   # Symbol breakdown — grouped by symbol_base (rule 11 / trap 12), NOT the
   # verbatim symbol. Unlike session/source this set is data-driven, so order
   # it deterministically (symbol_base ascending) rather than a fixed tuple.
   symbol_groups: dict[str, list[sqlite3.Row]] = {}
   for r in rows:
       symbol_groups.setdefault(r["symbol_base"], []).append(r)
   by_symbol = tuple(
       bucket_stat(sb, symbol_groups[sb]) for sb in sorted(symbol_groups)
   )
   ```
4. Pass `by_symbol=by_symbol` in the `ReportResult(...)` construction
   (`report.py:192-214`).

> **Ordering rationale.** by_session/by_source use a fixed tuple so the rendered
> table shape never shifts. Symbols are not a closed set, so a fixed tuple would
> either omit a future symbol or hardcode absent ones. `sorted()` gives a stable,
> gap-free order that grows correctly. State this in the docstring so a later
> reader doesn't "fix" it into a fixed tuple.

### 1c. CLI rendering — `cli.py`
After the by_source block (`cli.py:524-526`), add — mirroring it verbatim:
```python
typer.echo()
typer.echo("-- by symbol (grouped by symbol_base, rule 11; same per-bucket gating) --")
for b in r.by_symbol:
    typer.echo(_bucket_line(b, r.currency))
```

### Verification — Phase 1
- `uv run pytest -k report` → **paste the passing output** (DoD).
- `uv run journal rebuild` succeeds (DoD).
- `uv run journal report` → the new **by symbol** section prints; XAUUSD shows a
  number, thin symbols show `n/a (n=…, perlu ≥20)`.
- `grep -n "symbol_base" src/journal/analytics/report.py` → present in SELECT +
  grouping. Confirm no bucketing on `r["symbol"]`.

---

## Phase 2 — split the web: trim dashboard, add `/report`

### 2a. Context builder — `web/views.py`
Add next to `dashboard_context` (`views.py:40`):
```python
def report_context(conn: sqlite3.Connection) -> dict:
    """Deep analytics tables for the /report page. Same ReportResult the
    dashboard's cards read (build_report already did §9 gating); the two pages
    are two views of one object, so there is exactly one SQL read per request."""
    return {"report": build_report(conn)}
```
(Kept separate from `dashboard_context` for test symmetry; both are one-liners
over `build_report`. Do **not** add SQL here.)

### 2b. Route — `web/app.py`
After the dashboard route (`app.py:78`), copy it for `/report`:
```python
@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        ctx = views.report_context(conn)
        ctx["header"] = views.account_header(conn)
    except RuntimeError as e:
        return error_page(request, str(e))
    return render(request, "report.html", ctx)
```

### 2c. New template — `templates/report.html`
Copy from `dashboard.html`: the `bucket_rows` macro (`:4-14`), the Money panel
(`:52-58`), MAE/MFE panel (`:60-67`), by-session table (`:69-75`), by-source
table (`:77-83`), the §9 note (`:85-87`). Then add a **by-symbol** table copying
the by-source table shape — first column header "Symbol", body
`{{ bucket_rows(r.by_symbol, r.currency) }}`. Title block: "Report — mt5-journal".
Reuse the exact filter/global calls (`| money`, `| pct`, `gated(...)`,
`is_gated(...)`) — no hand-formatting.

### 2d. Trim `dashboard.html`
Keep the KPI cards (`:20-41`) and Outcomes cards (`:43-50`). **Remove** the Money
panel, MAE/MFE panel, by-session table, by-source table, and the §9 note (now on
`/report`). Add a link near the top: `<p><a href="/report">Lihat report
lengkap →</a></p>`. `dashboard_context` is unchanged (the KPI cards still read
`report`).

### 2e. Nav — `templates/base.html`
Add `<a href="/report">Report</a>` after the Dashboard link (`base.html:13`):
`Dashboard | Report | Trades | Weekly`.

### Verification — Phase 2
- `uv run journal serve` then, in another shell:
  - `curl -s localhost:8000/report | grep -i -E "XAUUSD|Symbol"` → symbol table present.
  - `curl -s localhost:8000/ | grep -c "Win rate"` → dashboard still renders cards.
  - `curl -s -o /dev/null -w '%{http_code}' localhost:8000/report` → `200`.
- (Confirm the serve port from `cli.py serve`; adjust if not 8000.)

---

## Phase 3 — web tests + full verification

### 3a. Tests — `tests/test_web.py`
Mirror existing route/context tests:
1. `report_context(conn)["report"]` is a `ReportResult` with a `by_symbol`.
2. `GET /report` → 200; body contains a known `symbol_base` label and "Symbol".
3. `GET /` (dashboard) still 200 and still shows a KPI card (regression guard for
   the trim).
4. No-account / multi-account → `/report` renders the friendly error page
   (mirror the dashboard's `RuntimeError → error_page` test).

### 3b. Final verification (Definition of Done — paste outputs)
- `uv run pytest` → **all green, pasted** (not just `-k`).
- `uv run journal rebuild` → succeeds.
- `uv run journal report` → by-symbol section correct.
- Anti-pattern grep sweep:
  - `grep -rn "GROUP BY symbol\b" src/` → none (must be `symbol_base`).
  - `grep -n "symbols" src/journal/analytics/report.py` → no `domain/symbols`
    import added.
  - `report.html` uses `gated(`/`is_gated(` as functions, `| money`/`| pct` as
    filters — matching `app.py:46-54`.
- `graphify update .` to refresh the graph (AST-only, no API cost).

---

## Surface area (expected diff)
- **Modified:** `analytics/report.py` (field + SELECT + grouping), `cli.py`
  (one echo block), `web/views.py` (one function), `web/app.py` (one route),
  `templates/dashboard.html` (trim + link), `templates/base.html` (nav link).
- **New:** `templates/report.html`, tests in `test_report.py` + `test_web.py`.
- **Untouched:** schema, adapter, ingest, domain, render — no migration, no new dep.

## Commit sequence (each must pass tests standalone — replayable)
1. `feat(M8): by_symbol breakdown in build_report + CLI` (Phase 1)
2. `feat(M8): /report web page; trim dashboard to at-a-glance cards` (Phase 2+3)

Then update `docs/HANDOFF.md` roadmap: add the M8 row.
