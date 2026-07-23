# Frontend React SPA Rework — Phase 2 (Live) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React `/live` page — open positions with floating P&L, a staleness badge, ~2.5s polling — and the `/commands` audit log, including the **mandatory two-step trade-command confirm** (preview → confirm modal → enqueue), preserving the existing server-side validation and 1.00-lot cap exactly.

**Architecture:** New JSON endpoints (`/api/live`, `/api/commands`, `POST /api/live/{id}/{action}/preview`, `POST /api/live/{id}/{action}`) are thin wrappers over the EXISTING, tested `web/views.py` (`live_context`, `commands_context`, `preview_command`) and `execute.enqueue`. The web process still never touches the bridge: a preview writes nothing; an enqueue inserts one `pending` row that `journal live` later executes. React consumes JSON and does the two-step UX; all refusal logic stays in `domain/commands.py`.

**Tech Stack:** Python 3.12 / FastAPI (unchanged runtime); the existing `frontend/` React 18 + TS + Tailwind + Vitest.

## Global Constraints

- **Two-step confirm is mandatory and unchanged in meaning.** Step 1 `POST …/preview` calls `views.preview_command` which runs `build_request` (validates) and **writes nothing**. Step 2 `POST …/{action}` calls `execute.enqueue` which **re-validates** and inserts exactly one `pending` row. The React client CANNOT bypass validation — the server re-validates at enqueue. No client-only gating of dangerous actions.
- **Server validation is the sole authority.** `domain/commands.validate`/`build_request` enforce `MAX_LOT = 1.0` (hard per-command cap), the asymmetric close-only rules, `trade_mode`, and volume-step. A refusal raises `CommandError`; the route returns it as a 400 with the message, writing nothing.
- **The web layer never calls the bridge / never imports MT5** (CLAUDE.md rules 1, 12). `web/api.py` imports only `web/views`, and the routes reuse `execute.enqueue` (already imported in `app.py`); `execute` imports `adapter/base.py` dataclasses only, never `MetaTrader5`.
- **Rule 4 survives end-to-end as `null` ≠ `0`.** In the JSON body, `sl`/`tp`/`volume` absent-or-`null` = "leave unchanged" (`None`); `0` = "clear this level"; a number = set it. `enqueue` stores `None`→`NULL` and `0.0`→`0.0` verbatim. Never coerce.
- **Money is `accounts.currency` (USC), raw in JSON.** Floating P&L must be LABELED floating, never presented as realized. `observed_msc` is true UTC (wall clock) and must NOT be compared with `open_time_msc` (broker server time, Trap 7).
- **`/api/live` polls at ~2.5s.** Staleness comes from `live_context` (`stale`/`age_s`/`empty`), which is honest about the "no positions vs `journal live` never ran" ambiguity — surface both.
- **No new dependency.** Python tests are pure functions over a seeded DB (no httpx/TestClient). Frontend uses Vitest. Legacy Jinja `/live*` routes stay untouched.
- **Definition of done:** tests pass with pasted output; `uv run journal rebuild` still succeeds.

---

### Task 1: `price` client formatter

Mirror `web/format.py:price` in `format.ts` for live SL/TP/entry display: `null` → "unknown" (rule 4: never 0), a real number shown compactly.

**Files:**
- Modify: `frontend/src/lib/format.ts` (add `price`)
- Modify: `frontend/src/lib/format.test.ts` (add cases)

**Interfaces:**
- Produces: `price(x: number | null): string` — `null`/`undefined` → `"unknown"`; else the number as a compact string (integer shows no decimals: `4010`, `4010.5`, `0`).

- [ ] **Step 1: Add the failing test cases**

Append inside `frontend/src/lib/format.test.ts` (before the final closing `});` of the `describe`, add a new `it`):
```ts
  it("price: null is unknown (never 0), real numbers show compactly", () => {
    expect(price(null)).toBe("unknown");
    expect(price(4010)).toBe("4010");
    expect(price(4010.5)).toBe("4010.5");
    expect(price(0)).toBe("0");
  });
```
And add `price` to the import at the top of the file:
```ts
import { money, rmult, pct, wib, isGated, price } from "./format";
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test`
Expected: FAIL — `price` is not exported.

- [ ] **Step 3: Implement**

Append to `frontend/src/lib/format.ts`:
```ts
export function price(x: number | null): string {
  // rule 4: null = unknown, never 0. A genuine 0.0 ("none set") shows as "0".
  if (x === null || x === undefined) return "unknown";
  return String(x);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test`
Expected: PASS (all format tests green).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/format.ts frontend/src/lib/format.test.ts
git commit -m "feat(web): price formatter mirroring web/format.py (null=unknown)"
```

---

### Task 2: `live_payload` + `commands_payload` (pure, tested)

Add the two read-side JSON payloads wrapping the existing builders, plus the live/command seed helpers to `tests/test_api.py`.

**Files:**
- Modify: `src/journal/web/api.py` (add `live_payload`, `commands_payload`)
- Modify: `tests/test_api.py` (add `now_ms` import, seed helpers, tests)

**Interfaces:**
- Consumes: `views.live_context(conn)`, `views.commands_context(conn)`, `views.account_header(conn)` (all existing).
- Produces:
  - `live_payload(conn) -> {"header": {...}, "live": {positions, count, total_floating, total_volume, age_s, stale, empty}}` — JSON-safe (positions are `sqlite3.Row` → dict).
  - `commands_payload(conn) -> {"header": {...}, "commands": [ {id, position_id, kind, status, sl, tp, volume, requested_msc, retcode, retcode_name, result_volume, result_price, broker_comment, error}, … ]}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`, add `now_ms` to the store import and append seed helpers + tests. Change the import line:
```python
from journal.store.db import connect, now_ms
```
Append at the end of the file:
```python
# --- live / commands seed helpers (mirror tests/test_web.py) ---------------

def _seed_spec(conn, symbol="XAUUSDc", *, trade_mode=4,
               volume_min=0.01, volume_max=100.0, volume_step=0.01):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, fetched_at, "
        "volume_min, volume_max, volume_step, trade_mode) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (symbol, symbol[:-1], volume_min, volume_max, volume_step, trade_mode),
    )
    conn.commit()


def _seed_position(conn, position_id, *, symbol="XAUUSDc", direction="buy",
                   volume=0.10, open_price=4000.0, price_current=4010.0,
                   sl=None, tp=None, profit=0.0, observed_msc=None):
    observed_msc = now_ms() if observed_msc is None else observed_msc
    conn.execute(
        "INSERT INTO open_positions (account_login, position_id, symbol, symbol_base, "
        "direction, volume, open_price, price_current, sl, tp, profit, swap, magic, "
        "open_time_msc, observed_msc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
        (_LOGIN, position_id, symbol, symbol[:-1], direction, volume, open_price,
         price_current, sl, tp, profit, _ms(9), observed_msc),
    )
    conn.commit()


def _seed_command(conn, *, position_id=1, kind="close", status="pending",
                  retcode=None, error=None, sl=None, tp=None, volume=None):
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, sl, tp, "
        "volume, requested_msc, status, retcode, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_LOGIN, position_id, kind, sl, tp, volume, now_ms(), status, retcode, error),
    )
    conn.commit()


def test_live_payload_shape_and_floating(conn):
    _seed_account(conn)
    _seed_position(conn, 1, profit=120.0, volume=0.10)
    _seed_position(conn, 2, profit=-30.0, volume=0.20)
    p = api.live_payload(conn)
    json.dumps(p)
    assert p["header"]["currency"] == "USC"
    assert p["live"]["count"] == 2
    assert abs(p["live"]["total_floating"] - 90.0) < 1e-9
    assert p["live"]["empty"] is False
    # positions carry the full field set the card renders
    pos = {r["position_id"]: r for r in p["live"]["positions"]}
    assert pos[1]["direction"] == "buy"
    assert pos[1]["symbol_base"] == "XAUUSD"
    assert "price_current" in pos[1] and "sl" in pos[1] and "observed_msc" in pos[1]


def test_live_payload_empty_is_honest(conn):
    _seed_account(conn)
    p = api.live_payload(conn)
    assert p["live"]["empty"] is True
    assert p["live"]["count"] == 0
    assert p["live"]["positions"] == []


def test_commands_payload_maps_retcode_name(conn):
    _seed_account(conn)
    _seed_command(conn, position_id=1, kind="close", status="done", retcode=10009)
    _seed_command(conn, position_id=2, kind="close", status="failed",
                  error="proses berhenti di tengah perintah")
    p = api.commands_payload(conn)
    json.dumps(p)
    by_pos = {c["position_id"]: c for c in p["commands"]}
    assert by_pos[1]["retcode_name"] == "DONE"      # name, not the int
    assert by_pos[2]["retcode_name"] is None         # nothing said yet
    assert by_pos[2]["error"] == "proses berhenti di tengah perintah"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k "live_payload or commands_payload" -v`
Expected: FAIL — `AttributeError: module 'journal.web.api' has no attribute 'live_payload'`.

- [ ] **Step 3: Implement**

Append to `src/journal/web/api.py`:
```python
def live_payload(conn: sqlite3.Connection) -> dict:
    """Header + the open-positions strip (floating P&L, staleness) for /api/live.
    Wraps `views.live_context`; adds no logic. `profit` is FLOATING (USC);
    `observed_msc` is true UTC (never compared with the server-time open_time_msc)."""
    return to_jsonable({
        "header": views.account_header(conn),
        "live": views.live_context(conn),
    })


def commands_payload(conn: sqlite3.Connection) -> dict:
    """Header + the trade-command audit log (newest first) for /api/commands.
    Wraps `views.commands_context`; the retcode NAME (never the bare int) and any
    error text arrive already mapped."""
    return to_jsonable({
        "header": views.account_header(conn),
        "commands": views.commands_context(conn)["commands"],
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v` then `uv run pytest -q`
Expected: the three new tests PASS; full suite passes with zero new failures.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/api.py tests/test_api.py
git commit -m "feat(web): live_payload + commands_payload (pure, no-HTTP tests)"
```

---

### Task 3: `/api/live` + `/api/commands` GET routes

Expose the two read payloads over HTTP.

**Files:**
- Modify: `src/journal/web/app.py` (two GET routes after `api_dashboard`)

**Interfaces:**
- Consumes: `api.live_payload`, `api.commands_payload`; `get_conn`.
- Produces: `GET /api/live` and `GET /api/commands` → JSON, 400 `{error}` on `RuntimeError`.

- [ ] **Step 1: Add the routes**

In `src/journal/web/app.py`, directly after the `api_dashboard` route, add:
```python
    @app.get("/api/live")
    def api_live(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.live_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/commands")
    def api_commands(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.commands_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

- [ ] **Step 2: Run the suite to confirm no regression**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. Paste the summary.

- [ ] **Step 3: Manual check (documented)**

Build if needed and start the server against a temp seeded DB (real DB has no open positions):
```bash
python3 - <<'PY'
from journal.store.db import connect, now_ms
c = connect("/Users/reisa/.claude/jobs/86660f53/tmp/live.db")
c.execute("INSERT INTO accounts (login, currency, first_seen_at) VALUES (0,'USC',1)")
c.execute("INSERT INTO open_positions (account_login,position_id,symbol,symbol_base,direction,volume,open_price,price_current,sl,tp,profit,swap,magic,open_time_msc,observed_msc) VALUES (0,1,'XAUUSDc','XAUUSD','buy',0.1,4000.0,4010.0,NULL,NULL,120.0,0,NULL,1,%d)" % now_ms())
c.commit()
PY
JOURNAL_DB=/Users/reisa/.claude/jobs/86660f53/tmp/live.db uv run journal serve &
sleep 2
curl -s http://localhost:8000/api/live
curl -s http://localhost:8000/api/commands
kill %1
```
Expected: `/api/live` shows `count:1`, `total_floating:120.0`, one position with `direction:"buy"`; `/api/commands` shows `commands:[]`.

- [ ] **Step 4: Commit**

```bash
git add src/journal/web/app.py
git commit -m "feat(web): /api/live + /api/commands read routes"
```

---

### Task 4: preview + enqueue POST routes (the two-step confirm) — SAFETY-CRITICAL

Add `POST /api/live/{position_id}/{action}/preview` (validates, writes nothing) and `POST /api/live/{position_id}/{action}` (enqueues one `pending` row). Reuse the existing `_ACTIONS` map, `views.preview_command`, and `execute.enqueue`. The refusal/validation logic is unchanged and already unit-tested (`tests/test_web.py`); this task wires HTTP and proves the write-once, writes-nothing, and rule-4 (`null`≠`0`) properties end-to-end.

**Files:**
- Modify: `src/journal/web/app.py` (import `Body`; two POST routes)

**Interfaces:**
- Consumes: `_ACTIONS` (existing), `views.account_header`, `views.preview_command`, `execute.enqueue` + `CommandError` (both already imported in app.py), `api.to_jsonable`.
- Produces:
  - `POST /api/live/{position_id}/{action}/preview` — JSON body `{sl?, tp?, volume?}` (each `number|null`). Returns the preview (`{intent, position_id, kind, symbol, fields}`) or 400 `{error}`. Writes nothing.
  - `POST /api/live/{position_id}/{action}` — same body. Returns `{"ok": true, "command_id": int}` or 400 `{error}`. Inserts exactly one `pending` row.
  - Unknown `action` → 404 `{error}`.

- [ ] **Step 1: Import `Body`**

In `src/journal/web/app.py`, change the fastapi import line
```python
from fastapi import Depends, FastAPI, Form, Request
```
to
```python
from fastapi import Body, Depends, FastAPI, Form, Request
```

- [ ] **Step 2: Add the two POST routes**

After the `api_commands` route (Task 3), add:
```python
    # --- two-step trade command (M9 safety: preview writes nothing; enqueue
    # inserts ONE pending row; `journal live` executes. Validation lives in
    # domain/commands via preview_command/enqueue and is re-run at enqueue.)
    @app.post("/api/live/{position_id}/{action}/preview")
    def api_preview(
        position_id: int,
        action: str,
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        volume: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        kind = _ACTIONS.get(action)
        if kind is None:
            return JSONResponse({"error": f"Aksi tidak dikenal: {action!r}."}, status_code=404)
        try:
            login = views.account_header(conn)["login"]
            preview = views.preview_command(
                conn, login, position_id, kind, sl=sl, tp=tp, volume=volume
            )
        except (RuntimeError, CommandError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(preview))

    @app.post("/api/live/{position_id}/{action}")
    def api_enqueue(
        position_id: int,
        action: str,
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        volume: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        kind = _ACTIONS.get(action)
        if kind is None:
            return JSONResponse({"error": f"Aksi tidak dikenal: {action!r}."}, status_code=404)
        try:
            login = views.account_header(conn)["login"]
            cmd_id = enqueue(conn, login, kind, position_id, sl=sl, tp=tp, volume=volume)
        except (RuntimeError, CommandError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "command_id": cmd_id})
```

- [ ] **Step 3: Run the suite to confirm no regression**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. Paste the summary.

- [ ] **Step 4: SAFETY end-to-end check against a temp seeded DB**

Prove: (a) a preview writes NOTHING, (b) an over-cap volume is refused with 400, (c) an enqueue writes exactly ONE pending row, (d) rule-4 `null` (leave unchanged) vs `0` (clear) reaches the DB verbatim.
```bash
DB=/Users/reisa/.claude/jobs/86660f53/tmp/confirm.db
rm -f "$DB"
python3 - <<'PY'
from journal.store.db import connect, now_ms
c = connect("/Users/reisa/.claude/jobs/86660f53/tmp/confirm.db")
c.execute("INSERT INTO accounts (login,currency,first_seen_at) VALUES (0,'USC',1)")
c.execute("INSERT INTO symbol_specs (symbol,symbol_base,fetched_at,volume_min,volume_max,volume_step,trade_mode) VALUES ('XAUUSDc','XAUUSD',1,0.01,100.0,0.01,4)")
c.execute("INSERT INTO open_positions (account_login,position_id,symbol,symbol_base,direction,volume,open_price,price_current,sl,tp,profit,swap,magic,open_time_msc,observed_msc) VALUES (0,1,'XAUUSDc','XAUUSD','buy',0.1,4000.0,4010.0,NULL,NULL,0.0,0,NULL,1,%d)" % now_ms())
c.commit()
PY
JOURNAL_DB=$DB uv run journal serve & sleep 2
echo "-- preview close (writes nothing):"; curl -s -X POST http://localhost:8000/api/live/1/close/preview -H 'Content-Type: application/json' -d '{}'
echo; echo "-- over-cap add-volume 5.0 (expect 400):"; curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/api/live/1/add-volume/preview -H 'Content-Type: application/json' -d '{"volume":5.0}'
echo "-- rows after previews (expect 0):"; sqlite3 "$DB" "SELECT count(*) FROM trade_commands;"
echo "-- enqueue modify sl=0 (clear), tp null (leave):"; curl -s -X POST http://localhost:8000/api/live/1/sltp -H 'Content-Type: application/json' -d '{"sl":0,"tp":null}'
echo; echo "-- rows after enqueue (expect 1 pending, sl=0.0, tp NULL):"; sqlite3 "$DB" "SELECT count(*), status, sl, tp FROM trade_commands;"
kill %1
```
Expected: preview returns an `intent`; over-cap returns `400`; rows after previews `0`; enqueue returns `{"ok":true,"command_id":1}`; final row shows `1|pending|0.0|` (tp NULL prints empty). If the server can't run here, seed a temp DB and drive `views.preview_command`/`execute.enqueue` directly instead — but do NOT fabricate output.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/app.py
git commit -m "feat(web): two-step trade command routes (preview writes nothing; enqueue one pending row)"
```

---

### Task 5: Live page — read-only (positions, floating total, staleness, polling)

Types, a POST helper, the position card (display only for now), a staleness badge, and the Live page polling `/api/live` every 2.5s. Wire `/live`.

**Files:**
- Modify: `frontend/src/lib/types.ts` (extend `LivePosition`; add `LiveData`, `CommandRow`, `CommandsData`, `PreviewResult`)
- Modify: `frontend/src/lib/api.ts` (add `postJson`)
- Create: `frontend/src/components/StalenessBadge.tsx`
- Create: `frontend/src/components/LivePositionCard.tsx`
- Create: `frontend/src/pages/Live.tsx`
- Modify: `frontend/src/App.tsx` (route `/live` → `Live`)

**Interfaces:**
- Consumes: `GET /api/live`; `format.ts` (`money`, `price`, `rmult`).
- Produces: `postJson<T>(path, body) -> {ok, data?, error?}`; `LivePositionCard` (renders one position + placeholder action slot via an `onAction` prop it will use in Task 6); `Live` page.

- [ ] **Step 1: Extend `types.ts`**

Replace the existing `LivePosition` and `Live` interfaces in `frontend/src/lib/types.ts` with:
```ts
export interface LivePosition {
  position_id: number;
  symbol: string;
  symbol_base: string;
  direction: "buy" | "sell";
  volume: number;
  open_price: number | null;
  price_current: number | null;
  sl: number | null;
  tp: number | null;
  profit: number | null;
  observed_msc: number;
}
export interface Live {
  positions: LivePosition[];
  count: number;
  total_floating: number;
  total_volume: number;
  age_s: number | null;
  stale: boolean;
  empty: boolean;
}
```
And append at the end of the file:
```ts
export interface LiveData { header: Header; live: Live; }

export interface CommandRow {
  id: number; position_id: number; kind: string; status: string;
  sl: number | null; tp: number | null; volume: number | null;
  requested_msc: number; retcode: number | null; retcode_name: string | null;
  result_volume: number | null; result_price: number | null;
  broker_comment: string | null; error: string | null;
}
export interface CommandsData { header: Header; commands: CommandRow[]; }

export interface PreviewResult {
  intent: string; position_id: number; kind: string; symbol: string;
  fields: { sl: number | null; tp: number | null; volume: number | null };
}

// A trade action: the URL segment and the command body it carries.
export type ActionKind = "sltp" | "close" | "close-partial" | "add-volume";
export interface CommandBody { sl?: number | null; tp?: number | null; volume?: number | null; }
```

- [ ] **Step 2: Add `postJson` to `api.ts`**

Append to `frontend/src/lib/api.ts`:
```ts
export async function postJson<T>(
  path: string,
  body: unknown,
): Promise<{ ok: boolean; data?: T; error?: string }> {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) return { ok: false, error: (j && j.error) ?? `HTTP ${r.status}` };
    return { ok: true, data: j as T };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}
```

- [ ] **Step 3: Create `StalenessBadge.tsx`**

```tsx
import { Live } from "../lib/types";

export default function StalenessBadge({ live }: { live: Live }) {
  if (live.empty)
    return <span className="text-[11px] text-muted px-2.5 py-1 rounded-full bg-white/5">
      tak ada posisi — atau <code>journal live</code> belum jalan
    </span>;
  const stale = live.stale;
  return (
    <span className={"text-[11px] px-2.5 py-1 rounded-full flex items-center gap-1.5 " +
      (stale ? "text-neg bg-neg/10 ring-1 ring-neg/25" : "text-cyan bg-cyan/10 ring-1 ring-cyan/25")}>
      <span className={"w-1.5 h-1.5 rounded-full " + (stale ? "bg-neg" : "bg-cyan shadow-[0_0_8px_#22d3ee]")} />
      {stale ? `basi · ${live.age_s}s — journal live mungkin mati` : `live · ${live.age_s}s`}
    </span>
  );
}
```

- [ ] **Step 4: Create `LivePositionCard.tsx`** (display + action hooks; the forms call `onAction`, wired to previews in Task 6)

```tsx
import { useState } from "react";
import { LivePosition, ActionKind, CommandBody } from "../lib/types";
import { money, price } from "../lib/format";

export default function LivePositionCard({
  pos, currency, onAction,
}: {
  pos: LivePosition;
  currency: string;
  onAction: (action: ActionKind, body: CommandBody) => void;
}) {
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [vol, setVol] = useState("");
  // "" = leave unchanged (null); a typed number (incl. 0) = that value.
  const opt = (s: string): number | null => (s.trim() === "" ? null : Number(s));
  const dirTone = pos.direction === "buy" ? "text-cyan" : "text-violet";
  const pnlTone = (pos.profit ?? 0) >= 0 ? "text-pos" : "text-neg";

  return (
    <div className="glass p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[14px] font-semibold">
          {pos.symbol_base} <span className={`uppercase ${dirTone}`}>{pos.direction}</span>
          <span className="text-muted num text-[12px] ml-2">#{pos.position_id}</span>
        </h3>
        <div className={`num text-[15px] font-bold ${pnlTone}`}>
          {money(pos.profit, currency, { sign: true })} <span className="text-[10px] text-muted font-normal">floating</span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-[12px] mb-3">
        <div><span className="text-muted">Vol </span><span className="num">{pos.volume}</span></div>
        <div><span className="text-muted">Buka </span><span className="num">{price(pos.open_price)}</span></div>
        <div><span className="text-muted">Now </span><span className="num">{price(pos.price_current)}</span></div>
        <div><span className="text-muted">SL/TP </span><span className="num">{price(pos.sl)} / {price(pos.tp)}</span></div>
      </div>

      <div className="flex flex-wrap gap-3 items-end text-[12px]">
        <div className="flex gap-2 items-end">
          <label className="flex flex-col text-muted text-[10px]">SL
            <input className="bg-white/5 rounded px-2 py-1 w-24 text-ink num" value={sl}
              onChange={(e) => setSl(e.target.value)} placeholder="kosong=tetap · 0=hapus" /></label>
          <label className="flex flex-col text-muted text-[10px]">TP
            <input className="bg-white/5 rounded px-2 py-1 w-24 text-ink num" value={tp}
              onChange={(e) => setTp(e.target.value)} placeholder="kosong=tetap · 0=hapus" /></label>
          <button className="px-3 py-1.5 rounded bg-violet/20 ring-1 ring-violet/40 text-ink"
            onClick={() => onAction("sltp", { sl: opt(sl), tp: opt(tp) })}>Ubah SL/TP…</button>
        </div>
        <button className="px-3 py-1.5 rounded bg-neg/15 ring-1 ring-neg/35 text-ink"
          onClick={() => onAction("close", {})}>Tutup {pos.volume} lot…</button>
        <div className="flex gap-2 items-end">
          <label className="flex flex-col text-muted text-[10px]">Vol sebagian
            <input className="bg-white/5 rounded px-2 py-1 w-20 text-ink num" value={vol}
              onChange={(e) => setVol(e.target.value)} placeholder="0.01" /></label>
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={() => onAction("close-partial", { volume: opt(vol) })}>Tutup sebagian…</button>
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={() => onAction("add-volume", { volume: opt(vol) })}>Tambah (posisi BARU)…</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create `Live.tsx`** (read-only wiring; `onAction` is a stub logging to console until Task 6)

```tsx
import { useApi } from "../lib/api";
import { LiveData, ActionKind, CommandBody } from "../lib/types";
import { money } from "../lib/format";
import StalenessBadge from "../components/StalenessBadge";
import LivePositionCard from "../components/LivePositionCard";

export default function Live() {
  const { data, error, loading } = useApi<LiveData>("/api/live", 2500);
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, live } = data;

  // Wired to the two-step confirm in Task 6.
  const onAction = (action: ActionKind, body: CommandBody) => {
    console.debug("action (wiring in Task 6)", action, body);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-[18px] font-bold tracking-tight">Live</h1>
          <div className="text-[12px] text-muted mt-0.5">
            {live.count} posisi · total floating{" "}
            <span className={(live.total_floating >= 0 ? "text-pos" : "text-neg") + " num"}>
              {money(live.total_floating, header.currency, { sign: true })}
            </span>
          </div>
        </div>
        <StalenessBadge live={live} />
      </div>

      {live.empty ? (
        <div className="glass p-6 text-muted text-sm">
          Tidak ada posisi terbuka — atau <code>journal live</code> belum pernah jalan.
          Tanpa heartbeat, keduanya tak bisa dibedakan dari sini.
        </div>
      ) : (
        live.positions.map((p) => (
          <LivePositionCard key={p.position_id} pos={p} currency={header.currency} onAction={onAction} />
        ))
      )}
    </div>
  );
}
```

- [ ] **Step 6: Wire the `/live` route**

In `frontend/src/App.tsx` add the import and swap the route:
```tsx
import Live from "./pages/Live";
```
Change `<Route path="/live" element={<Placeholder name="Live" />} />` to `<Route path="/live" element={<Live />} />`.

- [ ] **Step 7: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0; Vitest green.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts \
  frontend/src/components/StalenessBadge.tsx frontend/src/components/LivePositionCard.tsx \
  frontend/src/pages/Live.tsx frontend/src/App.tsx
git commit -m "feat(web): Live page — positions, floating total, staleness, 2.5s polling"
```

---

### Task 6: Two-step confirm modal (preview → enqueue)

Wire the position-card actions to the real flow: an action POSTs to `…/preview`, opens a modal showing the server's exact intent, and only the modal's confirm button POSTs to `…/{action}` (enqueue). A refusal at either step shows the message and writes nothing.

**Files:**
- Create: `frontend/src/components/ConfirmModal.tsx`
- Modify: `frontend/src/pages/Live.tsx` (replace the stub `onAction` with preview→modal→enqueue)

**Interfaces:**
- Consumes: `postJson`; `PreviewResult`, `ActionKind`, `CommandBody`; `GET /api/live` refetch.
- Produces: `ConfirmModal` (props: `preview: PreviewResult`, `submitting`, `error`, `onConfirm`, `onCancel`).

- [ ] **Step 1: Create `ConfirmModal.tsx`**

```tsx
import { PreviewResult } from "../lib/types";

export default function ConfirmModal({
  preview, submitting, error, onConfirm, onCancel,
}: {
  preview: PreviewResult;
  submitting: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         onClick={onCancel}>
      <div className="glass max-w-md w-full p-5" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-[15px] font-bold mb-1">Konfirmasi perintah</h2>
        <p className="text-[12px] text-muted mb-3">
          Perintah masuk antrean; <code>journal live</code> yang mengeksekusinya. Belum ada yang dikirim.
        </p>
        <div className="rounded-lg bg-white/5 p-3 text-[13px] mb-3">{preview.intent}</div>
        {error && <div className="text-neg text-[12px] mb-3">Ditolak: {error}</div>}
        <div className="flex justify-end gap-2">
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={onCancel} disabled={submitting}>Batal</button>
          <button className="px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold"
            onClick={onConfirm} disabled={submitting}>
            {submitting ? "Mengirim…" : "Konfirmasi & kirim"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Replace the `onAction` stub in `Live.tsx`**

Add these imports at the top of `frontend/src/pages/Live.tsx`:
```tsx
import { useState } from "react";
import { PreviewResult, ActionKind, CommandBody } from "../lib/types";
import { postJson } from "../lib/api";
import ConfirmModal from "../components/ConfirmModal";
```
(Keep the existing `useApi` import; remove the now-duplicate `ActionKind, CommandBody` from the existing `types` import line so they aren't imported twice — import them once.)

Inside the `Live` component, replace the stub `onAction` with this state + handlers, and render the modal + a toast. The component body becomes:
```tsx
export default function Live() {
  const { data, error, loading } = useApi<LiveData>("/api/live", 2500);
  const [pending, setPending] = useState<{ action: ActionKind; body: CommandBody } | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, live } = data;

  // Step 1: preview — writes nothing on the server; opens the confirm modal.
  const onAction = async (position_id: number, action: ActionKind, body: CommandBody) => {
    setActionError(null);
    const r = await postJson<PreviewResult>(`/api/live/${position_id}/${action}/preview`, body);
    if (!r.ok) { setToast(null); setActionError(r.error ?? "gagal"); setPreview(null); return; }
    setPending({ action, body });
    setPreview(r.data ?? null);
  };

  // Step 2: enqueue — the ONLY write. Server re-validates.
  const onConfirm = async () => {
    if (!preview || !pending) return;
    setSubmitting(true);
    const r = await postJson<{ ok: boolean; command_id: number }>(
      `/api/live/${preview.position_id}/${pending.action}`, pending.body);
    setSubmitting(false);
    if (!r.ok) { setActionError(r.error ?? "gagal"); return; }
    setPreview(null); setPending(null); setActionError(null);
    setToast(`Perintah #${r.data?.command_id} masuk antrean — journal live akan mengeksekusi.`);
  };

  const onCancel = () => { setPreview(null); setPending(null); setActionError(null); };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-[18px] font-bold tracking-tight">Live</h1>
          <div className="text-[12px] text-muted mt-0.5">
            {live.count} posisi · total floating{" "}
            <span className={(live.total_floating >= 0 ? "text-pos" : "text-neg") + " num"}>
              {money(live.total_floating, header.currency, { sign: true })}
            </span>
          </div>
        </div>
        <StalenessBadge live={live} />
      </div>

      {toast && <div className="glass p-3 mb-3 text-[12px] text-cyan">{toast}</div>}
      {actionError && !preview && <div className="glass p-3 mb-3 text-[12px] text-neg">Ditolak: {actionError}</div>}

      {live.empty ? (
        <div className="glass p-6 text-muted text-sm">
          Tidak ada posisi terbuka — atau <code>journal live</code> belum pernah jalan.
          Tanpa heartbeat, keduanya tak bisa dibedakan dari sini.
        </div>
      ) : (
        live.positions.map((p) => (
          <LivePositionCard key={p.position_id} pos={p} currency={header.currency}
            onAction={(action, body) => onAction(p.position_id, action, body)} />
        ))
      )}

      {preview && (
        <ConfirmModal preview={preview} submitting={submitting} error={actionError}
          onConfirm={onConfirm} onCancel={onCancel} />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0 (no unused imports, no type errors); Vitest green.

- [ ] **Step 4: Manual end-to-end (documented)**

Rebuild, serve against the temp seeded DB from Task 4, open `http://localhost:8000/app/live`. Confirm: a position card renders; clicking "Tutup … lot…" opens the modal with the intent sentence; "Batal" writes nothing; "Konfirmasi & kirim" shows the queued toast; an over-cap "Tambah" volume shows the refusal message and never opens/commits. Verify `sqlite3 <db> "SELECT count(*),status FROM trade_commands"` reflects exactly the confirmed commands.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConfirmModal.tsx frontend/src/pages/Live.tsx
git commit -m "feat(web): two-step confirm modal — preview then enqueue (no client-side bypass)"
```

---

### Task 7: Commands audit-log page

The trade-command log at `/commands` — newest first, showing intent fields, status, retcode NAME (never the bare int), and any error text.

**Files:**
- Create: `frontend/src/components/CommandsTable.tsx`
- Create: `frontend/src/pages/Commands.tsx`
- Modify: `frontend/src/App.tsx` (route `/commands` → `Commands`)

**Interfaces:**
- Consumes: `GET /api/commands`; `format.ts` (`wib`, `price`).
- Produces: `Commands` page.

- [ ] **Step 1: Create `CommandsTable.tsx`**

```tsx
import { CommandRow } from "../lib/types";
import { wib, price } from "../lib/format";

const STATUS_TONE: Record<string, string> = {
  done: "text-pos bg-pos/10", failed: "text-neg bg-neg/10",
  rejected: "text-neg bg-neg/10", pending: "text-muted bg-white/6",
};

export default function CommandsTable({ rows, offsetS }: { rows: CommandRow[]; offsetS: number }) {
  if (rows.length === 0) return <div className="text-muted text-sm py-6">Belum ada perintah.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="text-muted text-left">
            {["Waktu", "Posisi", "Jenis", "SL/TP/Vol", "Status", "Retcode", "Catatan"].map((h) => (
              <th key={h} className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-t border-white/5 align-top">
              <td className="py-2 num whitespace-nowrap">{wib(c.requested_msc, offsetS)}</td>
              <td className="py-2 num">#{c.position_id}</td>
              <td className="py-2">{c.kind}</td>
              <td className="py-2 num">{price(c.sl)} / {price(c.tp)} / {c.volume ?? "—"}</td>
              <td className="py-2"><span className={"px-2 py-0.5 rounded text-[10px] " + (STATUS_TONE[c.status] ?? "text-muted bg-white/6")}>{c.status}</span></td>
              <td className="py-2 num">{c.retcode_name ?? "—"}</td>
              <td className="py-2 text-muted max-w-[280px]">{c.error ?? c.broker_comment ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Create `Commands.tsx`**

```tsx
import { useApi } from "../lib/api";
import { CommandsData } from "../lib/types";
import CommandsTable from "../components/CommandsTable";

export default function Commands() {
  const { data, error, loading } = useApi<CommandsData>("/api/commands", 5000);
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Log perintah</h1>
      <div className="text-[12px] text-muted mb-4">audit — apa yang diminta vs apa yang terjadi</div>
      <div className="glass p-4">
        <CommandsTable rows={data.commands} offsetS={data.header.offset_s} />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire the `/commands` route**

In `frontend/src/App.tsx` add `import Commands from "./pages/Commands";` and change `<Route path="/commands" element={<Placeholder name="Commands" />} />` to `<Route path="/commands" element={<Commands />} />`.

- [ ] **Step 4: Build + test**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exit 0; Vitest green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CommandsTable.tsx frontend/src/pages/Commands.tsx frontend/src/App.tsx
git commit -m "feat(web): Commands audit-log page"
```

---

## Self-Review

- **Spec coverage (Phase 2):** `/api/live` + polling (Tasks 2,3,5) ✓; open positions + floating P&L + staleness (Tasks 2,5) ✓; two-step confirm preview→enqueue with unchanged server validation & 1.00-lot cap (Task 4 routes + Task 6 UI; verified writes-nothing / one-pending-row / over-cap-400 in Task 4 Step 4) ✓; rule-4 null≠0 through JSON (Task 4 body params + Step 4 check) ✓; Commands audit log (Tasks 2,7) ✓; money USC raw + floating labeled + observed_msc true-UTC (Tasks 2,5 card) ✓; no new dependency, pure Python tests, legacy Jinja untouched ✓.
- **Placeholder scan:** no TBD/TODO; every code step carries complete code.
- **Type consistency:** Python `live_payload`/`commands_payload` shapes match the TS `LiveData`/`Live`/`LivePosition`/`CommandsData`/`CommandRow`; `PreviewResult` matches `views.preview_command`'s return (`intent/position_id/kind/symbol/fields`); the `_ACTIONS` URL segments (`sltp`,`close`,`close-partial`,`add-volume`) match the TS `ActionKind` and the card's `onAction` calls; `postJson`/`useApi` names are used consistently across Tasks 5–7.
- **Safety note for the executor:** Task 4 is the safety-critical gate — do not mark it complete without the Step-4 evidence showing previews wrote 0 rows and an enqueue wrote exactly 1 `pending` row with `sl=0.0`/`tp=NULL` intact.
