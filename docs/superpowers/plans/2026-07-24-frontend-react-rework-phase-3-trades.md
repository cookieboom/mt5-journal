# Frontend React SPA Rework — Phase 3 (Trades + Trade detail) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React `/trades` list (filter by symbol/status/source, per-row sparkbar + tags) and the `/trades/:id` detail page (NULL-aware facts, the reused chart PNG, an annotation form, add/remove manual tags) — the first phase with a **write path** (annotation + tags), though no money moves.

**Architecture:** New JSON endpoints (`GET /api/trades`, `GET /api/trades/{id}`, `POST /api/trades/{id}/annotate`, `POST /api/trades/{id}/tags`, `POST /api/trades/{id}/tags/delete`) are thin wrappers over the EXISTING, tested `web/views.py` (`trades_context`, `trade_detail_context`) and `annotate.py` (`set_annotation`, `add_tag`, `remove_tag`, `list_tags`). The chart is the EXISTING legacy route `GET /trades/{id}/chart.png` reused verbatim — the React `<img>` points straight at it, no new endpoint. The web layer still never imports MT5 and adds no business logic; all validation (confidence 1–5, orphan-guard, manual-only delete) stays in `annotate.py`.

**Tech Stack:** Python 3.12 / FastAPI (unchanged runtime); the existing `frontend/` React 18 + TS + Tailwind + Vitest.

## Global Constraints

- **The web layer never imports MT5 / never touches the bridge** (CLAUDE.md rules 1, 12). `web/api.py` imports only `web/views`; the write routes reuse `annotate.py` (pure sqlite writers). No `import MetaTrader5`, no MT5 constants.
- **Money is `accounts.currency` (USC), raw in JSON; the client formats.** `net_profit` stays a raw number; never emit "$"; the currency code stays glued to the number at display (`money(x, currency)`).
- **Rule 4 survives end-to-end as `null` ≠ `0`.** For trade FACTS, `sl_initial`/`tp_initial`/`r_multiple`/`mae_r`/`mfe_r` = `null` means **unknown** and renders "unknown"/"n/a" (never 0), and such a trade is excluded from R. For the ANNOTATION write, `confidence`/`followed_plan`/`setup`/`emotion`/`notes` absent-or-`null` = "not recorded" → stored `NULL`; never coerce an unset field to `0`/`""`. `set_annotation` stores `None`→`NULL` verbatim.
- **Prices show full precision** via the existing `price()` in `format.ts` (`String(x)`, an approved divergence from Jinja's `%g`); `null`→"unknown".
- **No sample-size gating in this phase.** `/trades` shows a single trade's own `r_multiple` (per-row, `null`→"n/a"), and detail shows one trade's facts — neither is an aggregate, so the §8/§9 `n<20` gate does not apply here. Do NOT invent a gate.
- **Timestamps are broker SERVER time; WIB = UTC+7 at display only** (rule 3). Use the existing `wib(msc, offset_s)` mirror; never compare a server time with wall-clock.
- **Reuse the existing chart route.** `GET /trades/{id}/chart.png` already renders (or serves the cached) PNG and 404s a missing window (rule 6, charts are cache). Do NOT add a new chart endpoint; the React `<img>` src is the absolute path `/trades/{id}/chart.png` (NOT under `/app`).
- **No new dependency.** Python tests are pure functions over a seeded DB (no httpx/TestClient); the frontend uses Vitest. Legacy Jinja `/trades*` routes stay untouched.
- **`followed_plan` crosses JSON as a real tri-state boolean:** `true`/`false`/`null` (NOT the Jinja form's `"yes"`/`"no"`/`""` strings). `set_annotation` already accepts `bool | int | None`.
- **Definition of done:** tests pass with pasted output; `uv run journal rebuild` still succeeds; `graphify update .` run after code changes.

---

### Task 1: `dur` client formatter

Mirror `web/format.py:dur` in `format.ts` for the trade-list duration cell: `null` → "—", else a compact human duration (`45s`, `12m`, `3m05s`, `2h07m`).

**Files:**
- Modify: `frontend/src/lib/format.ts` (add `dur`)
- Modify: `frontend/src/lib/format.test.ts` (add cases, import `dur`)

**Interfaces:**
- Produces: `dur(seconds: number | null): string` — mirrors `web/format.py:dur` exactly: `null`/`undefined`→"—"; `<60`→`"{s}s"`; `<3600`→`"{m}m{ss}s"` with 2-digit seconds, or `"{m}m"` when seconds are 0; else `"{h}h{mm}m"` with 2-digit minutes.

- [ ] **Step 1: Add the failing test cases**

Add `dur` to the import at the top of `frontend/src/lib/format.test.ts`:
```ts
import { money, rmult, pct, wib, isGated, price, dur } from "./format";
```
Append inside the `describe`, before its final closing `});`, a new `it`:
```ts
  it("dur: mirrors web/format.py (null=—, s/m/h ladder with zero-pad)", () => {
    expect(dur(null)).toBe("—");
    expect(dur(45)).toBe("45s");
    expect(dur(720)).toBe("12m");        // 12m exactly, seconds 0 → no s
    expect(dur(185)).toBe("3m05s");      // seconds zero-padded
    expect(dur(7620)).toBe("2h07m");     // minutes zero-padded
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test`
Expected: FAIL — `dur` is not exported.

- [ ] **Step 3: Implement**

Append to `frontend/src/lib/format.ts`:
```ts
export function dur(seconds: number | null): string {
  // Mirrors web/format.py:dur. null = unknown → "—".
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s ? `${m}m${String(s).padStart(2, "0")}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h${String(mm).padStart(2, "0")}m`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test`
Expected: PASS (all format tests green).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/format.ts frontend/src/lib/format.test.ts
git commit -m "feat(web): dur formatter mirroring web/format.py (null=—)"
```

---

### Task 2: `trades_payload` + `trade_detail_payload` (pure, tested)

Add the two read-side JSON payloads wrapping the existing builders, plus trade/annotation/tag seed helpers to `tests/test_api.py`.

**Files:**
- Modify: `src/journal/web/api.py` (add `trades_payload`, `trade_detail_payload`)
- Modify: `tests/test_api.py` (extend `_seed_trade`, add annotation/tag seed helpers + tests)

**Interfaces:**
- Consumes: `views.trades_context(conn, symbol=, status=, source=)`, `views.trade_detail_context(conn, position_id)` (both existing).
- Produces:
  - `trades_payload(conn, *, symbol=None, status=None, source=None) -> {"header": {...}, "trades": [row…], "tags": {position_id: [[tag, source]…]}, "symbols": [str…], "max_abs_net": float, "filters": {symbol, status, source}}` — JSON-safe (trade rows → dict; `tags` int keys stringify under json.dumps).
  - `trade_detail_payload(conn, position_id) -> dict | None` — `{"header", "trade": {...}, "annotation": {...}|null, "tags": [[tag, source]…], "session": str, "is_ea": bool, "chartable": bool}`; `None` when no such trade (route → 404).

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`, first make `_seed_trade` able to vary the fields the list/detail render. Replace the existing `_seed_trade` with:
```python
def _seed_trade(conn, position_id, *, symbol_base="XAUUSD", direction="buy",
                status="closed", net_profit=0.0, r_multiple=None,
                sl_initial=None, magic=None, close_time_msc=None):
    symbol = symbol_base + "c"
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, duration_s, volume, "
        "open_price, close_price, sl_initial, net_profit, r_multiple, magic, "
        "deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.1, 4000.0, 4001.0, ?, ?, ?, ?, 2, 1)",
        (_LOGIN, position_id, symbol, symbol_base, direction, status, _ms(9),
         close_time_msc or _ms(10), 3600, sl_initial, net_profit, r_multiple, magic),
    )
    conn.commit()
```
Append at the end of the file:
```python
# --- annotation / tag seed helpers (mirror tests/test_web.py) ---------------

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


def test_trades_payload_shape_filters_and_nulls(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, symbol_base="XAUUSD", net_profit=250.0, r_multiple=1.5, magic=None)
    _seed_trade(conn, 2, symbol_base="BTCUSD", net_profit=-80.0, r_multiple=None, magic=777)
    _seed_tag(conn, 1, "breakout", source="manual")
    p = api.trades_payload(conn)
    json.dumps(p)
    assert p["header"]["currency"] == "USC"
    assert {t["position_id"] for t in p["trades"]} == {1, 2}
    assert p["max_abs_net"] == 250.0
    assert "BTCUSD" in p["symbols"] and "XAUUSD" in p["symbols"]
    # rule 4: unknown R stays null, never 0
    by_pos = {t["position_id"]: t for t in p["trades"]}
    assert by_pos[2]["r_multiple"] is None
    # tags survive as [tag, source] pairs (json stringifies the int key)
    tags = json.loads(json.dumps(p))["tags"]
    assert tags["1"] == [["breakout", "manual"]]
    # source filter (ea = magic truthy) narrows to the EA trade
    ea = api.trades_payload(conn, source="ea")
    assert [t["position_id"] for t in ea["trades"]] == [2]
    assert ea["filters"]["source"] == "ea"
    # symbol filter narrows and is echoed back
    xau = api.trades_payload(conn, symbol="XAUUSD")
    assert [t["position_id"] for t in xau["trades"]] == [1]


def test_trade_detail_payload_facts_annotation_tags(conn):
    _seed_account(conn)
    _seed_trade(conn, 5, net_profit=100.0, r_multiple=None, sl_initial=None, magic=42)
    _seed_annotation(conn, 5, setup="breakout", confidence=4, followed_plan=1)
    _seed_tag(conn, 5, "auto-win", source="auto")
    p = api.trade_detail_payload(conn, 5)
    json.dumps(p)
    assert p["trade"]["position_id"] == 5
    assert p["trade"]["sl_initial"] is None      # rule 4: unknown, not 0
    assert p["trade"]["r_multiple"] is None
    assert p["is_ea"] is True                      # magic truthy
    assert p["chartable"] is True                  # closed + close_time set
    assert p["annotation"]["setup"] == "breakout"
    assert p["annotation"]["confidence"] == 4
    assert p["tags"] == [["auto-win", "auto"]]


def test_trade_detail_payload_missing_is_none(conn):
    _seed_account(conn)
    assert api.trade_detail_payload(conn, 999) is None


def test_trade_detail_payload_null_annotation(conn):
    _seed_account(conn)
    _seed_trade(conn, 7, net_profit=0.0)
    p = api.trade_detail_payload(conn, 7)
    assert p["annotation"] is None                 # no note yet → null, not {}
    assert p["tags"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k "trades_payload or trade_detail_payload" -v`
Expected: FAIL — `AttributeError: module 'journal.web.api' has no attribute 'trades_payload'`.

- [ ] **Step 3: Implement**

Append to `src/journal/web/api.py`:
```python
def trades_payload(
    conn: sqlite3.Connection,
    *,
    symbol: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> dict:
    """The /api/trades list: trade rows (newest-open first) with optional
    symbol/status/source filters, the per-page `max_abs_net` the sparkbar scales
    to, tags grouped by position_id, and the distinct symbol list for the filter
    chips. Wraps `views.trades_context`; adds no logic. Money stays raw USC;
    a trade's unknown `r_multiple` stays null (rule 4)."""
    return to_jsonable(
        views.trades_context(conn, symbol=symbol, status=status, source=source)
    )


def trade_detail_payload(conn: sqlite3.Connection, position_id: int) -> dict | None:
    """Full facts + human layer for one trade, or `None` if there is no such
    trade (route → 404). Wraps `views.trade_detail_context`; adds no logic.
    `sl_initial`/`tp_initial`/`r_multiple` may be null = unknown (rule 4, never
    0); `annotation` is null until one is written; `chartable` says whether the
    reused `/trades/{id}/chart.png` will render."""
    ctx = views.trade_detail_context(conn, position_id)
    return None if ctx is None else to_jsonable(ctx)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v` then `uv run pytest -q`
Expected: the new tests PASS; full suite passes with zero new failures. Paste the summary.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/api.py tests/test_api.py
git commit -m "feat(web): trades_payload + trade_detail_payload (pure, no-HTTP tests)"
```

---

### Task 3: `GET /api/trades` + `GET /api/trades/{id}` read routes

Expose the two read payloads over HTTP. The chart is NOT re-added — the existing `/trades/{id}/chart.png` route already serves it.

**Files:**
- Modify: `src/journal/web/app.py` (two GET routes after `api_commands`)

**Interfaces:**
- Consumes: `api.trades_payload`, `api.trade_detail_payload`; `get_conn`.
- Produces: `GET /api/trades?symbol=&status=&source=` → JSON; `GET /api/trades/{position_id}` → JSON or 404 `{error}`; 400 `{error}` on `RuntimeError` (no/multi account).

- [ ] **Step 1: Add the routes**

In `src/journal/web/app.py`, directly after the `api_commands` route (before the two-step `api_preview`/`api_enqueue` POST routes), add:
```python
    @app.get("/api/trades")
    def api_trades(
        symbol: str | None = None,
        status: str | None = None,
        source: str | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            return JSONResponse(
                api.trades_payload(conn, symbol=symbol, status=status, source=source)
            )
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/trades/{position_id}")
    def api_trade_detail(
        position_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ):
        try:
            payload = api.trade_detail_payload(conn, position_id)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if payload is None:
            return JSONResponse(
                {"error": f"Tidak ada trade dengan position_id {position_id}."},
                status_code=404,
            )
        return JSONResponse(payload)
```

- [ ] **Step 2: Run the suite to confirm no regression**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. Paste the summary.

- [ ] **Step 3: Manual check (documented)**

Seed a temp DB with one closed trade and query both routes:
```bash
DB=/Users/reisa/.claude/jobs/86660f53/tmp/trades.db
rm -f "$DB"
python3 - <<'PY'
from journal.store.db import connect
c = connect("/Users/reisa/.claude/jobs/86660f53/tmp/trades.db")
c.execute("INSERT INTO accounts (login,currency,first_seen_at) VALUES (0,'USC',1)")
c.execute("INSERT INTO trades (account_login,position_id,symbol,symbol_base,direction,status,open_time_msc,close_time_msc,duration_s,volume,open_price,close_price,sl_initial,net_profit,r_multiple,magic,deal_count,rebuilt_at) VALUES (0,1,'XAUUSDc','XAUUSD','buy','closed',1000,3600000,3599,0.1,4000.0,4010.0,NULL,250.0,NULL,NULL,2,1)")
c.commit()
PY
JOURNAL_DB=$DB uv run journal serve & sleep 2
echo "-- /api/trades:"; curl -s http://localhost:8000/api/trades
echo; echo "-- /api/trades/1:"; curl -s http://localhost:8000/api/trades/1
echo; echo "-- /api/trades/999 (expect 404):"; curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/trades/999
kill %1
```
Expected: `/api/trades` shows one trade with `r_multiple:null`, `max_abs_net:250.0`, `symbols:["XAUUSD"]`; `/api/trades/1` shows `trade.sl_initial:null`, `annotation:null`, `tags:[]`; `/api/trades/999` returns `404`. If the server can't run here, drive `api.trades_payload`/`api.trade_detail_payload` against the seeded DB directly instead — do NOT fabricate output.

- [ ] **Step 4: Commit**

```bash
git add src/journal/web/app.py
git commit -m "feat(web): /api/trades + /api/trades/{id} read routes"
```

---

### Task 4: annotation + tag write routes (the write path) — INTEGRITY-CRITICAL

Add `POST /api/trades/{id}/annotate`, `POST /api/trades/{id}/tags`, and `POST /api/trades/{id}/tags/delete`, thin over `annotate.set_annotation`/`add_tag`/`remove_tag` (already imported in `app.py`). This is the phase's first write path. It proves rule-4 (`null` ≠ `0`) survives HTTP→DB for annotation fields, that the orphan-guard and confidence CHECK still refuse bad input as a clean 400, and that an auto tag can't be deleted here.

**Files:**
- Modify: `src/journal/web/app.py` (import `list_tags`; three POST routes)

**Interfaces:**
- Consumes: `set_annotation`, `add_tag`, `remove_tag`, `AnnotateError` (already imported); add `list_tags` to that import; `views.account_header` (for RuntimeError surfacing); `Body`.
- Produces:
  - `POST /api/trades/{position_id}/annotate` — JSON body `{setup?, confidence?, emotion?, followed_plan?, notes?}` (each nullable; `followed_plan` is `true|false|null`; `confidence` is `int|null`). Returns `{"ok": true, "annotation": {...}}` or 400 `{error}`. Absent/`null` fields store `NULL` (rule 4).
  - `POST /api/trades/{position_id}/tags` — body `{tag}`. Returns `{"ok": true, "tags": [[tag, source]…]}` or 400 `{error}`.
  - `POST /api/trades/{position_id}/tags/delete` — body `{tag}`. Returns `{"ok": true, "removed": int, "tags": [[tag, source]…]}` (deleting an auto/absent tag → `removed: 0`, never an error).

- [ ] **Step 1: Add `list_tags` to the annotate import**

In `src/journal/web/app.py`, change:
```python
from ..annotate import AnnotateError, add_tag, remove_tag, set_annotation
```
to
```python
from ..annotate import AnnotateError, add_tag, list_tags, remove_tag, set_annotation
```
(`Body` is already imported from `fastapi` for the Phase 2 preview/enqueue routes; if a merge dropped it, add it: `from fastapi import Body, Depends, FastAPI, Form, Request`.)

- [ ] **Step 2: Add the three POST routes**

After the `api_enqueue` route, add:
```python
    # --- trade human layer (M6 writes over JSON). Thin over annotate.py; all
    # validation (confidence 1-5, orphan-guard, manual-only delete) lives there.
    # rule 4: an absent field is null = "not recorded" → stored NULL, never 0.
    @app.post("/api/trades/{position_id}/annotate")
    def api_annotate(
        position_id: int,
        setup: str | None = Body(None),
        confidence: int | None = Body(None),
        emotion: str | None = Body(None),
        followed_plan: bool | None = Body(None),
        notes: str | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            ann = set_annotation(
                conn, position_id, setup=setup, confidence=confidence,
                emotion=emotion, followed_plan=followed_plan, notes=notes,
            )
        except (AnnotateError, RuntimeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "annotation": api.to_jsonable(ann)})

    @app.post("/api/trades/{position_id}/tags")
    def api_add_tag(
        position_id: int,
        tag: str = Body(..., embed=True),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            tags = add_tag(conn, position_id, tag)
        except (AnnotateError, RuntimeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "tags": api.to_jsonable(tags)})

    @app.post("/api/trades/{position_id}/tags/delete")
    def api_remove_tag(
        position_id: int,
        tag: str = Body(..., embed=True),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        # Only manual tags are removable; remove_tag's source='manual' filter makes
        # deleting an auto tag a no-op (removed=0), so no guard is needed here.
        try:
            removed = remove_tag(conn, position_id, tag)
            tags = list_tags(conn, position_id)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(
            {"ok": True, "removed": removed, "tags": api.to_jsonable(tags)}
        )
```
> Note on `embed=True`: a single `Body` scalar (`tag`) needs `embed=True` so the body is `{"tag": "..."}`. The annotate route has multiple `Body` params, which FastAPI already treats as an embedded object — no `embed` needed there.

- [ ] **Step 3: Run the suite to confirm no regression**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. Paste the summary.

- [ ] **Step 4: INTEGRITY end-to-end check against a temp seeded DB**

Prove: (a) an annotate with only `setup` leaves `confidence`/`followed_plan` as `NULL` (rule 4, not 0); (b) `confidence:9` is refused 400 and writes nothing; (c) an unknown `position_id` is refused 400 (orphan-guard); (d) a manual tag adds then deletes; (e) deleting an `auto` tag is a no-op (`removed:0`) and leaves it in place.
```bash
DB=/Users/reisa/.claude/jobs/86660f53/tmp/annotate.db
rm -f "$DB"
python3 - <<'PY'
from journal.store.db import connect
c = connect("/Users/reisa/.claude/jobs/86660f53/tmp/annotate.db")
c.execute("INSERT INTO accounts (login,currency,first_seen_at) VALUES (0,'USC',1)")
c.execute("INSERT INTO trades (account_login,position_id,symbol,symbol_base,direction,status,open_time_msc,close_time_msc,duration_s,volume,open_price,close_price,sl_initial,net_profit,r_multiple,magic,deal_count,rebuilt_at) VALUES (0,1,'XAUUSDc','XAUUSD','buy','closed',1000,3600000,3599,0.1,4000.0,4010.0,NULL,250.0,NULL,NULL,2,1)")
c.execute("INSERT INTO tags (account_login,position_id,segment,tag,source) VALUES (0,1,0,'auto-win','auto')")
c.commit()
PY
JOURNAL_DB=$DB uv run journal serve & sleep 2
echo "-- annotate setup only (confidence/followed_plan must stay NULL):"; curl -s -X POST http://localhost:8000/api/trades/1/annotate -H 'Content-Type: application/json' -d '{"setup":"breakout"}'
echo; sqlite3 "$DB" "SELECT setup, confidence, followed_plan FROM annotations WHERE position_id=1;"
echo "-- bad confidence 9 (expect 400, no change):"; curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/api/trades/1/annotate -H 'Content-Type: application/json' -d '{"confidence":9}'
echo "-- unknown trade 999 (expect 400 orphan-guard):"; curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/api/trades/999/annotate -H 'Content-Type: application/json' -d '{"setup":"x"}'
echo "-- add manual tag:"; curl -s -X POST http://localhost:8000/api/trades/1/tags -H 'Content-Type: application/json' -d '{"tag":"revenge"}'
echo; echo "-- delete manual tag (removed:1):"; curl -s -X POST http://localhost:8000/api/trades/1/tags/delete -H 'Content-Type: application/json' -d '{"tag":"revenge"}'
echo; echo "-- delete auto tag (removed:0, stays):"; curl -s -X POST http://localhost:8000/api/trades/1/tags/delete -H 'Content-Type: application/json' -d '{"tag":"auto-win"}'
echo; sqlite3 "$DB" "SELECT tag, source FROM tags WHERE position_id=1;"
kill %1
```
Expected: first annotate row prints `breakout||` (confidence + followed_plan empty = NULL); bad confidence → `400`; unknown trade → `400`; add-tag returns the manual tag; delete manual → `removed:1`; delete auto → `removed:0` and `auto-win|auto` still present. If the server can't run here, drive `set_annotation`/`add_tag`/`remove_tag` against the seeded DB directly and read the rows — do NOT fabricate output.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/app.py
git commit -m "feat(web): trade annotate + tag write routes (null≠0 preserved; manual-only delete)"
```

---

### Task 5: Trades list page — filters, sparkbar, tags

Types, a `TradeSparkbar` component, and the Trades page reading `/api/trades` with client-driven filter chips. Wire `/trades`.

**Files:**
- Modify: `frontend/src/lib/types.ts` (add `TradeRow`, `TradesData`)
- Create: `frontend/src/components/TradeSparkbar.tsx`
- Create: `frontend/src/pages/Trades.tsx`
- Modify: `frontend/src/App.tsx` (route `/trades` → `Trades`)

**Interfaces:**
- Consumes: `GET /api/trades` via `useApi`; `format.ts` (`money`, `rmult`, `wib`, `dur`).
- Produces: `TradeSparkbar` (props: `net: number | null`, `maxAbsNet: number`); `Trades` page. `TradeRow`/`TradesData` types used by the detail-linking `<Link>` and later phases.

- [ ] **Step 1: Extend `types.ts`**

Append at the end of `frontend/src/lib/types.ts`:
```ts
export interface TradeRow {
  position_id: number;
  symbol_base: string;
  direction: "buy" | "sell";
  status: "closed" | "open" | "partially_open";
  open_time_msc: number;
  close_time_msc: number | null;
  duration_s: number | null;
  net_profit: number | null;
  r_multiple: number | null;
  magic: number | null;
}
export interface TradesData {
  header: Header;
  trades: TradeRow[];
  tags: Record<string, [string, string][]>;  // keyed by String(position_id)
  symbols: string[];
  max_abs_net: number;
  filters: { symbol: string; status: string; source: string };
}
```

- [ ] **Step 2: Create `TradeSparkbar.tsx`**

Mirrors the Jinja twin-bar spark: a centre line with a left (loss) or right (win) bar whose width is `|net| / maxAbsNet`. A glance cue only; the number lives in its own cell.
```tsx
export default function TradeSparkbar({
  net, maxAbsNet,
}: { net: number | null; maxAbsNet: number }) {
  const w = net !== null && maxAbsNet > 0 ? Math.min(100, (Math.abs(net) / maxAbsNet) * 100) : 0;
  const win = net !== null && net > 0;
  const loss = net !== null && net < 0;
  return (
    <span className="inline-flex w-24 h-2 rounded bg-white/[0.06] overflow-hidden" aria-hidden="true">
      <span className="w-1/2 flex justify-end">
        <span className="h-full rounded-l bg-neg/70" style={{ width: `${loss ? w : 0}%` }} />
      </span>
      <span className="w-1/2 flex justify-start">
        <span className="h-full rounded-r bg-pos/70" style={{ width: `${win ? w : 0}%` }} />
      </span>
    </span>
  );
}
```

- [ ] **Step 3: Create `Trades.tsx`**

Filter chips are plain buttons that rebuild the `/api/trades?…` query; `useApi` refetches when the path changes. Each row links to `/trades/:id` (React Router, under `/app`).
```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/api";
import { TradesData } from "../lib/types";
import { money, rmult, wib, dur } from "../lib/format";
import TradeSparkbar from "../components/TradeSparkbar";

const STATUSES = ["closed", "open", "partially_open"];
const SOURCES: [string, string][] = [["ea", "EA"], ["disc", "Discretionary"]];

function qs(f: { symbol: string; status: string; source: string }): string {
  const p = new URLSearchParams();
  if (f.symbol) p.set("symbol", f.symbol);
  if (f.status) p.set("status", f.status);
  if (f.source) p.set("source", f.source);
  const s = p.toString();
  return s ? `/api/trades?${s}` : "/api/trades";
}

export default function Trades() {
  const [f, setF] = useState({ symbol: "", status: "", source: "" });
  const { data, error, loading } = useApi<TradesData>(qs(f));
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, trades, tags, symbols, max_abs_net } = data;

  const chip = (active: boolean) =>
    "px-2.5 py-1 rounded-full text-[11px] ring-1 " +
    (active ? "bg-violet/20 ring-violet/45 text-ink" : "bg-white/5 ring-panel-border text-muted");

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">
        Trades <span className="text-muted num text-[13px]">({trades.length})</span>
      </h1>
      <div className="text-[12px] text-muted mb-4">
        Net dalam {header.currency} (US cents) — bar hanya penanda arah. Waktu WIB (UTC+7).
      </div>

      <div className="flex flex-col gap-2 mb-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Symbol</span>
          <button className={chip(!f.symbol)} onClick={() => setF({ ...f, symbol: "" })}>semua</button>
          {symbols.map((s) => (
            <button key={s} className={chip(f.symbol === s)} onClick={() => setF({ ...f, symbol: s })}>{s}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Status</span>
          <button className={chip(!f.status)} onClick={() => setF({ ...f, status: "" })}>semua</button>
          {STATUSES.map((s) => (
            <button key={s} className={chip(f.status === s)} onClick={() => setF({ ...f, status: s })}>{s}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Source</span>
          <button className={chip(!f.source)} onClick={() => setF({ ...f, source: "" })}>semua</button>
          {SOURCES.map(([v, label]) => (
            <button key={v} className={chip(f.source === v)} onClick={() => setF({ ...f, source: v })}>{label}</button>
          ))}
        </div>
      </div>

      <div className="glass p-4 overflow-x-auto">
        {trades.length === 0 ? (
          <div className="text-muted text-sm py-6">Tidak ada trade untuk filter ini.</div>
        ) : (
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="text-muted text-left">
                {["Dibuka", "Symbol", "Arah", "Status", "Src", "Durasi", "Net", "", "R", "Tags"].map((h, i) => (
                  <th key={i} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const pnl = t.net_profit ?? 0;
                return (
                  <tr key={t.position_id} className="border-t border-white/5">
                    <td className="py-2 num whitespace-nowrap">
                      <Link className="text-cyan hover:underline" to={`/trades/${t.position_id}`}>
                        {wib(t.open_time_msc, header.offset_s)}
                      </Link>
                    </td>
                    <td className="py-2">{t.symbol_base}</td>
                    <td className="py-2 uppercase">{t.direction}</td>
                    <td className="py-2">{t.status}</td>
                    <td className="py-2">{t.magic ? "EA" : "Disc"}</td>
                    <td className="py-2 num whitespace-nowrap">{dur(t.duration_s)}</td>
                    <td className={"py-2 num whitespace-nowrap " + (pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : "")}>
                      {money(t.net_profit, header.currency, { sign: true })}
                    </td>
                    <td className="py-2"><TradeSparkbar net={t.net_profit} maxAbsNet={max_abs_net} /></td>
                    <td className="py-2 num">{t.r_multiple === null ? <span className="text-muted">n/a</span> : rmult(t.r_multiple)}</td>
                    <td className="py-2">
                      <span className="flex flex-wrap gap-1">
                        {(tags[String(t.position_id)] ?? []).map(([tag, source]) => (
                          <span key={tag} className={"px-1.5 py-0.5 rounded text-[10px] " +
                            (source === "manual" ? "bg-violet/15 text-violet" : "bg-white/6 text-muted")}>{tag}</span>
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the `/trades` route**

In `frontend/src/App.tsx` add `import Trades from "./pages/Trades";` and change `<Route path="/trades" element={<Placeholder name="Trades" />} />` to `<Route path="/trades" element={<Trades />} />`.

- [ ] **Step 5: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0 (no unused imports, no type errors); Vitest green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/components/TradeSparkbar.tsx \
  frontend/src/pages/Trades.tsx frontend/src/App.tsx
git commit -m "feat(web): Trades list — filters, sparkbar, tags"
```

---

### Task 6: Trade detail page — facts + chart (read-only)

Types for the detail payload, and the detail page showing NULL-aware facts and the reused chart PNG. Annotation form + tag editing land in Task 7. Wire `/trades/:id`.

**Files:**
- Modify: `frontend/src/lib/types.ts` (add `Annotation`, `TradeFull`, `TradeDetailData`)
- Create: `frontend/src/pages/TradeDetail.tsx`
- Modify: `frontend/src/App.tsx` (route `/trades/:id` → `TradeDetail`)

**Interfaces:**
- Consumes: `GET /api/trades/{id}` via `useApi` with a param path; `format.ts` (`money`, `rmult`, `price`, `wib`, `dur`); `useParams` from react-router.
- Produces: `Annotation`, `TradeFull`, `TradeDetailData` types (Task 7 reuses them); `TradeDetail` page rendering facts + chart.

- [ ] **Step 1: Extend `types.ts`**

Append at the end of `frontend/src/lib/types.ts`:
```ts
export interface Annotation {
  setup: string | null;
  confidence: number | null;
  emotion: string | null;
  followed_plan: number | null;  // 0 | 1 | null
  notes: string | null;
}
export interface TradeFull {
  position_id: number;
  symbol_base: string;
  direction: "buy" | "sell";
  status: "closed" | "open" | "partially_open";
  open_time_msc: number;
  close_time_msc: number | null;
  duration_s: number | null;
  volume: number;
  open_price: number | null;
  close_price: number | null;
  sl_initial: number | null;
  tp_initial: number | null;
  net_profit: number | null;
  r_multiple: number | null;
  mae_r: number | null;
  mfe_r: number | null;
  magic: number | null;
}
export interface TradeDetailData {
  header: Header;
  trade: TradeFull;
  annotation: Annotation | null;
  tags: [string, string][];
  session: string;
  is_ea: boolean;
  chartable: boolean;
}
```

- [ ] **Step 2: Create `TradeDetail.tsx`** (read-only; a `null` fact shows "unknown"/"n/a", NEVER 0)

```tsx
import { useParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { TradeDetailData } from "../lib/types";
import { money, rmult, price, wib, dur } from "../lib/format";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-white/5 text-[13px]">
      <span className="text-muted">{label}</span>
      <span className="num text-right">{children}</span>
    </div>
  );
}
const unknown = <span className="text-muted" title="tidak diketahui — rule 4: NULL ≠ 0">unknown</span>;
const na = <span className="text-muted" title="perlu SL awal diketahui untuk menghitung R">n/a</span>;

export default function TradeDetail() {
  const { id } = useParams();
  const { data, error, loading } = useApi<TradeDetailData>(`/api/trades/${id}`);
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, trade, session, is_ea, chartable } = data;
  const pnl = trade.net_profit ?? 0;

  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-4">
        {trade.symbol_base} <span className="uppercase">{trade.direction}</span>
        <span className="text-muted num text-[13px] ml-2">#{trade.position_id}</span>
      </h1>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Trade</h2>
          <Fact label="Status">{trade.status}</Fact>
          <Fact label="Source">{is_ea ? `EA (magic ${trade.magic})` : "Discretionary"}</Fact>
          <Fact label="Session">{session}</Fact>
          <Fact label="Dibuka">{wib(trade.open_time_msc, header.offset_s)}</Fact>
          <Fact label="Ditutup">{wib(trade.close_time_msc, header.offset_s)}</Fact>
          <Fact label="Durasi">{dur(trade.duration_s)}</Fact>
          <Fact label="Volume">{trade.volume}</Fact>
          <Fact label="Entry">{price(trade.open_price)}</Fact>
          <Fact label="Exit">{price(trade.close_price)}</Fact>
          <Fact label="SL awal">{trade.sl_initial === null ? unknown : price(trade.sl_initial)}</Fact>
          <Fact label="TP awal">{trade.tp_initial === null ? unknown : price(trade.tp_initial)}</Fact>
          <Fact label="Net">
            <span className={pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : ""}>
              {money(trade.net_profit, header.currency, { sign: true })}
            </span>
          </Fact>
          <Fact label="R-multiple">{trade.r_multiple === null ? na : rmult(trade.r_multiple)}</Fact>
          <Fact label="MAE (R)">{trade.mae_r === null ? na : rmult(trade.mae_r)}</Fact>
          <Fact label="MFE (R)">{trade.mfe_r === null ? na : rmult(trade.mfe_r)}</Fact>
        </div>

        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-2">Chart</h2>
          {chartable ? (
            <img className="w-full rounded" src={`/trades/${trade.position_id}/chart.png`}
              alt={`chart trade ${trade.position_id}`}
              onError={(e) => {
                const el = e.currentTarget;
                el.insertAdjacentHTML("afterend",
                  '<p class="text-[12px] text-muted">Chart belum tersedia — jalankan <code>uv run journal candles</code> lalu buka lagi.</p>');
                el.remove();
              }} />
          ) : (
            <p className="text-[12px] text-muted">Hanya trade closed yang bisa di-chart.</p>
          )}
        </div>
      </div>

      <p className="mt-4 text-[12px]"><a className="text-cyan hover:underline" href="/app/trades">← kembali ke daftar</a></p>
    </div>
  );
}
```

- [ ] **Step 3: Wire the `/trades/:id` route**

In `frontend/src/App.tsx` add `import TradeDetail from "./pages/TradeDetail";` and change `<Route path="/trades/:id" element={<Placeholder name="Trade detail" />} />` to `<Route path="/trades/:id" element={<TradeDetail />} />`.

- [ ] **Step 4: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0; Vitest green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/pages/TradeDetail.tsx frontend/src/App.tsx
git commit -m "feat(web): Trade detail — NULL-aware facts + reused chart PNG"
```

---

### Task 7: Trade detail — annotation form + tag add/remove (write path UI)

Wire the write path: a controlled annotation form POSTing to `…/annotate`, and manual tag add/remove. `""` in a form field means "not recorded" → send `null` (rule 4), never `0`/`""`; `followed_plan` is a real tri-state. On success, refetch the detail so the UI reflects the stored row.

**Files:**
- Create: `frontend/src/components/AnnotationForm.tsx`
- Create: `frontend/src/components/TagEditor.tsx`
- Modify: `frontend/src/pages/TradeDetail.tsx` (add a refetchable read + render the two editors)
- Modify: `frontend/src/lib/api.ts` (add a `reload` to `useApi`)

**Interfaces:**
- Consumes: `postJson` (existing); `Annotation`, `TradeDetailData` (Task 6); `GET /api/trades/{id}` refetch.
- Produces: `useApi` returns an added `reload: () => void`; `AnnotationForm` (props: `positionId`, `annotation`, `onSaved`); `TagEditor` (props: `positionId`, `tags`, `onChanged`).

- [ ] **Step 1: Add `reload` to `useApi`**

In `frontend/src/lib/api.ts`, replace the `useApi` function with a version exposing a manual refetch (a `tick` state the effect depends on):
```ts
export function useApi<T>(path: string, intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(path);
        const body = await r.json();
        if (!alive) return;
        if (!r.ok) setError(body.error ?? `HTTP ${r.status}`);
        else { setData(body as T); setError(null); }
      } catch (e) {
        if (alive) setError(String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const id = intervalMs ? setInterval(load, intervalMs) : undefined;
    return () => { alive = false; if (id) clearInterval(id); };
  }, [path, intervalMs, tick]);

  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
```
(The `tick` dependency is the only change to the effect; every existing caller keeps working — `reload` is additive.)

- [ ] **Step 2: Create `AnnotationForm.tsx`**

`""` → `null` on submit (rule 4: unset ≠ 0/empty). `followed_plan` radios map `"yes"/"no"/""` → `true/false/null` only at submit.
```tsx
import { useState } from "react";
import { Annotation } from "../lib/types";
import { postJson } from "../lib/api";

export default function AnnotationForm({
  positionId, annotation, onSaved,
}: {
  positionId: number;
  annotation: Annotation | null;
  onSaved: () => void;
}) {
  const a = annotation;
  const [setup, setSetup] = useState(a?.setup ?? "");
  const [confidence, setConfidence] = useState(a?.confidence != null ? String(a.confidence) : "");
  const [emotion, setEmotion] = useState(a?.emotion ?? "");
  const [fp, setFp] = useState(a?.followed_plan === 1 ? "yes" : a?.followed_plan === 0 ? "no" : "");
  const [notes, setNotes] = useState(a?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  const submit = async () => {
    setSaving(true); setErr(null); setOk(false);
    const body = {
      setup: setup.trim() === "" ? null : setup.trim(),
      confidence: confidence.trim() === "" ? null : Number(confidence),
      emotion: emotion.trim() === "" ? null : emotion.trim(),
      followed_plan: fp === "yes" ? true : fp === "no" ? false : null,
      notes: notes.trim() === "" ? null : notes,
    };
    const r = await postJson(`/api/trades/${positionId}/annotate`, body);
    setSaving(false);
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    setOk(true); onSaved();
  };

  const field = "bg-white/5 rounded px-2 py-1 text-ink w-full";
  return (
    <div className="glass p-4">
      <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">Anotasi</h2>
      <div className="flex flex-col gap-3 text-[12px]">
        <label className="flex flex-col gap-1 text-muted">Setup
          <input className={field} value={setup} onChange={(e) => setSetup(e.target.value)} placeholder="mis. breakout" /></label>
        <label className="flex flex-col gap-1 text-muted">Confidence (1–5, kosong=belum dicatat)
          <input className={field + " num"} type="number" min={1} max={5} value={confidence}
            onChange={(e) => setConfidence(e.target.value)} /></label>
        <label className="flex flex-col gap-1 text-muted">Emosi
          <input className={field} value={emotion} onChange={(e) => setEmotion(e.target.value)} placeholder="mis. tenang / fomo" /></label>
        <div className="flex flex-col gap-1 text-muted">Ikut plan?
          <div className="flex gap-4 text-ink">
            {[["yes", "ya"], ["no", "tidak"], ["", "—"]].map(([v, label]) => (
              <label key={v} className="flex items-center gap-1">
                <input type="radio" name={`fp-${positionId}`} checked={fp === v} onChange={() => setFp(v)} /> {label}
              </label>
            ))}
          </div>
        </div>
        <label className="flex flex-col gap-1 text-muted">Catatan
          <textarea className={field + " min-h-[72px]"} value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="apa yang terjadi, pelajaran…" /></label>
        {err && <div className="text-neg">Ditolak: {err}</div>}
        {ok && !err && <div className="text-cyan">Tersimpan.</div>}
        <button className="self-start px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold"
          onClick={submit} disabled={saving}>{saving ? "Menyimpan…" : "Simpan anotasi"}</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `TagEditor.tsx`**

Manual tags carry an `×`; auto tags don't (they can't be deleted here). Add posts, delete posts; both refetch via `onChanged`.
```tsx
import { useState } from "react";
import { postJson } from "../lib/api";

export default function TagEditor({
  positionId, tags, onChanged,
}: {
  positionId: number;
  tags: [string, string][];
  onChanged: () => void;
}) {
  const [newTag, setNewTag] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const add = async () => {
    if (newTag.trim() === "") return;
    setErr(null);
    const r = await postJson(`/api/trades/${positionId}/tags`, { tag: newTag.trim() });
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    setNewTag(""); onChanged();
  };
  const del = async (tag: string) => {
    setErr(null);
    const r = await postJson(`/api/trades/${positionId}/tags/delete`, { tag });
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    onChanged();
  };

  return (
    <div className="glass p-4">
      <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">Tags</h2>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {tags.length === 0 && <span className="text-muted text-[12px]">(belum ada tag)</span>}
        {tags.map(([tag, source]) => (
          <span key={tag} className={"px-2 py-0.5 rounded text-[11px] flex items-center gap-1 " +
            (source === "manual" ? "bg-violet/15 text-violet" : "bg-white/6 text-muted")}>
            {tag}
            {source === "manual" && (
              <button className="text-muted hover:text-neg" title="hapus tag manual" onClick={() => del(tag)}>×</button>
            )}
          </span>
        ))}
      </div>
      <div className="flex gap-2 text-[12px]">
        <input className="bg-white/5 rounded px-2 py-1 text-ink flex-1" value={newTag}
          onChange={(e) => setNewTag(e.target.value)} placeholder="tag manual, mis. revenge-trade"
          onKeyDown={(e) => { if (e.key === "Enter") add(); }} />
        <button className="px-3 py-1.5 rounded bg-violet/20 ring-1 ring-violet/40 text-ink" onClick={add}>Tambah</button>
      </div>
      {err && <div className="text-neg text-[12px] mt-2">Ditolak: {err}</div>}
      <p className="text-[11px] text-muted mt-2">Tag <span className="px-1 rounded bg-white/6">auto</span> di-set oleh <code>rebuild</code> dan tak bisa dihapus di sini.</p>
    </div>
  );
}
```

- [ ] **Step 4: Render the editors in `TradeDetail.tsx`**

Add `reload` to the `useApi` destructure and imports; render the two editors in a right-hand column below the chart. Change the top of `TradeDetail.tsx`:
```tsx
import { useParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { TradeDetailData } from "../lib/types";
import { money, rmult, price, wib, dur } from "../lib/format";
import AnnotationForm from "../components/AnnotationForm";
import TagEditor from "../components/TagEditor";
```
Change the destructure line
```tsx
  const { data, error, loading } = useApi<TradeDetailData>(`/api/trades/${id}`);
```
to
```tsx
  const { data, error, loading, reload } = useApi<TradeDetailData>(`/api/trades/${id}`);
```
And add `annotation` + `tags` to the payload destructure:
```tsx
  const { header, trade, annotation, tags, session, is_ea, chartable } = data;
```
Then, immediately BEFORE the closing `</div>` that precedes the `← kembali` paragraph, insert the editors:
```tsx
      <div className="grid md:grid-cols-2 gap-4 mt-4">
        <AnnotationForm positionId={trade.position_id} annotation={annotation} onSaved={reload} />
        <TagEditor positionId={trade.position_id} tags={tags} onChanged={reload} />
      </div>
```

- [ ] **Step 5: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0 (no unused imports — `annotation`/`tags`/`reload` are now all consumed); Vitest green.

- [ ] **Step 6: Manual end-to-end (documented)**

Rebuild the frontend, serve against a DB with at least one trade, open `http://localhost:8000/app/trades`, click a row → detail. Confirm: facts render with `unknown`/`n/a` for NULL fields (never 0); saving an annotation with confidence left blank stores `NULL` (check `sqlite3 <db> "SELECT confidence,followed_plan FROM annotations"`); a manual tag adds and its `×` deletes; an auto tag shows no `×`; a confidence of 9 shows the refusal message and writes nothing. Do NOT fabricate — paste what you observe.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/AnnotationForm.tsx \
  frontend/src/components/TagEditor.tsx frontend/src/pages/TradeDetail.tsx
git commit -m "feat(web): Trade detail write path — annotation form + tag add/remove"
```

---

## Self-Review

- **Spec coverage (Phase 3, spec §6 row 3 "Trades list+filters & Trade detail (annotate/tags, chart) + APIs"):** `/trades` list + symbol/status/source filters + sparkbar + tags (Tasks 2,5) ✓; `/trades/:id` NULL-aware facts (Task 6) ✓; chart reused via `/trades/{id}/chart.png` (Task 6, no new endpoint) ✓; annotation write (setup/confidence/emotion/followed-plan/notes) (Tasks 4,7) ✓; tag add/remove, manual-only delete (Tasks 4,7) ✓; write-path parity — the review gate — proven end-to-end in Task 4 Step 4 (null≠0, orphan-guard 400, confidence CHECK 400, auto-tag undeletable) ✓; thin wrappers over `views`/`annotate`, no MT5 import, pure Python tests, no new dependency, legacy Jinja untouched ✓.
- **Placeholder scan:** no TBD/TODO; every code step carries complete code; the `_seed_trade` replacement, `reload` addition, and `TradeDetail` edits are shown in full.
- **Type consistency:** Python `trades_payload` shape matches TS `TradesData` (`trades`, `tags` as `Record<string,[string,string][]>`, `symbols`, `max_abs_net`, `filters`); `trade_detail_payload` matches `TradeDetailData` (`trade`→`TradeFull`, `annotation`→`Annotation|null`, `tags`→`[string,string][]`, `session`, `is_ea`, `chartable`); the write bodies (`setup/confidence/emotion/followed_plan/notes`; `{tag}`) match `set_annotation`/`add_tag`/`remove_tag` signatures; `useApi`'s added `reload` is consumed only where destructured (existing callers unaffected); `postJson` reused unchanged; route URL segments (`/api/trades`, `/annotate`, `/tags`, `/tags/delete`) match the frontend calls.
- **Rule-4 note for the executor:** Task 4 is the integrity gate — do not mark it complete without the Step-4 evidence that an annotate with only `setup` left `confidence`/`followed_plan` `NULL` (not 0), a bad confidence and an unknown trade each returned 400 writing nothing, and an `auto` tag survived a delete attempt (`removed:0`).
