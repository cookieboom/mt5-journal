# Replay Config Prefs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the replay config popup's five inputs (symbol, timeframe, start date, history bars, speed) so the next replay opens pre-filled with the last-launched config.

**Architecture:** Mirror the existing chart-prefs stack. A new `app_prefs` key `"replay"` (no migration — the table and generic `get_pref`/`set_pref` already exist) is exposed by `get/set_replay_prefs` wrappers and `GET/PUT /api/replay/prefs` routes. On the frontend a new `replayPrefs.ts` lib (type + normalize + localStorage) and `useReplayPrefs` hook feed the modal's initial field values and save the form on submit.

**Tech Stack:** Python 3.12 + sqlite3 (stdlib), FastAPI (routes tested by calling the plain endpoint fn directly — no TestClient/httpx), pytest; React + TypeScript, vitest, Tailwind.

## Global Constraints

- **No new dependencies** (CLAUDE.md rule 8). Backend tests call route functions directly via the `_endpoint(app, name)` helper — no `TestClient`/httpx.
- **No MT5 import anywhere in this work** (CLAUDE.md rules 1, 12). `prefs_store` and the new routes are pure DB.
- **`app_prefs` is not derived from raw** — `journal rebuild` must never touch it; do NOT add it to any rebuild path.
- **Server stores the prefs blob verbatim** — no server-side shape validation; the client owns the schema (same as chart prefs).
- **Money/price rule is irrelevant here** (no money fields), but keep timestamps as integer epoch-ms if any are added (none are).
- localStorage key: `"mt5j.replay.config"`. app_prefs key: `"replay"`.

---

### Task 1: Backend prefs_store replay wrappers

**Files:**
- Modify: `src/journal/store/prefs_store.py`
- Test: `tests/test_prefs_store.py`

**Interfaces:**
- Consumes: existing `get_pref(conn, key)`, `set_pref(conn, key, value, updated_ms)`, `now_ms` (already in module).
- Produces:
  - `REPLAY_KEY: str = "replay"`
  - `get_replay_prefs(conn) -> Any | None` — parsed JSON stored under `REPLAY_KEY`, or `None`.
  - `set_replay_prefs(conn, prefs) -> int` — `json.dumps` the blob, upsert, return `updated_ms`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_prefs_store.py`:

```python
def test_replay_prefs_roundtrip_parses_json(conn):
    assert ps.get_replay_prefs(conn) is None
    ts = ps.set_replay_prefs(conn, {"version": 1, "symbol": "BTCUSDc", "speed": 7})
    assert isinstance(ts, int) and ts > 0
    assert ps.get_replay_prefs(conn) == {"version": 1, "symbol": "BTCUSDc", "speed": 7}
    assert ps.get_pref(conn, ps.REPLAY_KEY) is not None


def test_replay_and_chart_prefs_do_not_collide(conn):
    ps.set_chart_prefs(conn, {"version": 1, "theme": "dark"})
    ps.set_replay_prefs(conn, {"version": 1, "symbol": "EURUSDc"})
    assert ps.get_chart_prefs(conn) == {"version": 1, "theme": "dark"}
    assert ps.get_replay_prefs(conn) == {"version": 1, "symbol": "EURUSDc"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prefs_store.py -k replay -v`
Expected: FAIL — `AttributeError: module 'journal.store.prefs_store' has no attribute 'get_replay_prefs'`.

- [ ] **Step 3: Write minimal implementation**

In `src/journal/store/prefs_store.py`, add `REPLAY_KEY` next to `CHART_KEY` and the two wrappers at the end of the file:

```python
CHART_KEY = "chart"
REPLAY_KEY = "replay"
```

```python
def get_replay_prefs(conn: sqlite3.Connection) -> Any | None:
    """Parsed replay-config prefs JSON, or None if never saved."""
    raw = get_pref(conn, REPLAY_KEY)
    return json.loads(raw) if raw is not None else None


def set_replay_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Persist replay-config prefs (serialised to JSON). Returns updated_ms."""
    return set_pref(conn, REPLAY_KEY, json.dumps(prefs), now_ms())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prefs_store.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/prefs_store.py tests/test_prefs_store.py
git commit -m "feat(replay): app_prefs replay wrappers (get/set_replay_prefs)"
```

---

### Task 2: Backend GET/PUT /api/replay/prefs routes

**Files:**
- Modify: `src/journal/web/app.py` (add both routes right after the chart-prefs pair, ~line 196)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `prefs_store.get_replay_prefs`, `prefs_store.set_replay_prefs` (Task 1); existing `get_conn` dependency, `JSONResponse`, `Body`; test helpers `create_app`, `_endpoint(app, name)`, and the `conn` fixture already in `tests/test_web.py`.
- Produces: FastAPI routes named `api_get_replay_prefs` (`GET /api/replay/prefs` → `{"prefs": <blob|null>}`) and `api_put_replay_prefs` (`PUT /api/replay/prefs`, body verbatim → `{"ok": true, "updated_ms": ts}`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web.py` (near the other `_endpoint` route tests, after `test_api_candles_route_400_on_bad_timeframe`):

```python
def test_api_replay_prefs_get_null_then_put_then_get(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_replay_prefs")
    put_fn = _endpoint(app, "api_put_replay_prefs")

    resp = get_fn(conn=conn)
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"prefs": None}

    blob = {"version": 1, "symbol": "BTCUSDc", "timeframe": "M15",
            "startDate": "2026-01-02", "historyBars": 500, "speed": 7}
    put = put_fn(prefs=blob, conn=conn)
    put_body = json.loads(put.body)
    assert put_body["ok"] is True and isinstance(put_body["updated_ms"], int)

    resp2 = get_fn(conn=conn)
    assert json.loads(resp2.body) == {"prefs": blob}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web.py -k replay_prefs -v`
Expected: FAIL — `AssertionError: no route named 'api_get_replay_prefs'`.

- [ ] **Step 3: Write minimal implementation**

In `src/journal/web/app.py`, immediately after `api_put_chart_prefs` (the block ending `return JSONResponse({"ok": True, "updated_ms": ts})` around line 196), add:

```python
    @app.get("/api/replay/prefs")
    def api_get_replay_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        """Replay-config popup prefs, cross-browser. `prefs` is null until first
        save; the client then falls back to its own defaults / localStorage.
        Pure DB — never talks to the bridge (M9 boundary)."""
        return JSONResponse({"prefs": prefs_store.get_replay_prefs(conn)})

    @app.put("/api/replay/prefs")
    def api_put_replay_prefs(
        prefs=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the replay-config prefs blob under key 'replay'. The server
        stamps updated_ms; the body is stored verbatim (the client owns the
        schema)."""
        ts = prefs_store.set_replay_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_web.py -k replay_prefs -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/app.py tests/test_web.py
git commit -m "feat(replay): GET/PUT /api/replay/prefs routes (pure DB)"
```

---

### Task 3: Frontend replayPrefs lib

**Files:**
- Create: `frontend/src/lib/replayPrefs.ts`
- Test: `frontend/src/lib/replayPrefs.test.ts`

**Interfaces:**
- Consumes: `SYMBOLS`, `TIMEFRAMES`, `type Sym`, `type Timeframe` from `./candles`.
- Produces:
  - `interface ReplayFormPrefs { version: 1; symbol: Sym; timeframe: Timeframe; startDate: string; historyBars: number; speed: number }`
  - `DEFAULT_REPLAY_PREFS: ReplayFormPrefs`
  - `normalizeReplayPrefs(raw: unknown) => ReplayFormPrefs`
  - `loadReplayPrefs(store?: Storage) => ReplayFormPrefs`
  - `saveReplayPrefs(s: ReplayFormPrefs, store?: Storage) => void`
  - `reconcileReplayPrefs(local, dbParsed, localExists) => { settings: ReplayFormPrefs; shouldImport: boolean }`
  - `STORAGE_KEY: string` (= `"mt5j.replay.config"`)
  - Bounds consumed by the modal: `HISTORY_MIN = 100`, `HISTORY_MAX = 1000`, `SPEED_MIN = 1`, `SPEED_MAX = 10`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/replayPrefs.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  DEFAULT_REPLAY_PREFS, loadReplayPrefs, saveReplayPrefs,
  normalizeReplayPrefs, reconcileReplayPrefs, STORAGE_KEY,
} from "./replayPrefs";

function fakeStore(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() { return m.size; },
  } as Storage;
}

describe("replayPrefs", () => {
  it("loads defaults when nothing stored", () => {
    expect(loadReplayPrefs(fakeStore())).toEqual(DEFAULT_REPLAY_PREFS);
  });

  it("round-trips saved prefs", () => {
    const s = fakeStore();
    const p = { ...DEFAULT_REPLAY_PREFS, symbol: "BTCUSDc" as const, speed: 8 };
    saveReplayPrefs(p, s);
    expect(loadReplayPrefs(s)).toEqual(p);
  });

  it("falls back to defaults on corrupt json", () => {
    const s = fakeStore();
    s.setItem(STORAGE_KEY, "{not json");
    expect(loadReplayPrefs(s)).toEqual(DEFAULT_REPLAY_PREFS);
  });

  it("clamps out-of-range historyBars and speed", () => {
    const n = normalizeReplayPrefs({ historyBars: 99999, speed: 0 });
    expect(n.historyBars).toBe(1000);
    expect(n.speed).toBe(1);
  });

  it("rejects bad symbol / timeframe / startDate", () => {
    const n = normalizeReplayPrefs({ symbol: "NOPE", timeframe: "X9", startDate: "not-a-date" });
    expect(n.symbol).toBe(DEFAULT_REPLAY_PREFS.symbol);
    expect(n.timeframe).toBe(DEFAULT_REPLAY_PREFS.timeframe);
    expect(n.startDate).toBe("");
  });

  it("keeps a valid yyyy-mm-dd startDate", () => {
    expect(normalizeReplayPrefs({ startDate: "2026-01-02" }).startDate).toBe("2026-01-02");
  });

  it("reconcile: DB present wins; absent keeps local and imports when local existed", () => {
    const local = { ...DEFAULT_REPLAY_PREFS, speed: 9 };
    const fromDb = reconcileReplayPrefs(local, { version: 1, symbol: "EURUSDc" }, false);
    expect(fromDb.settings.symbol).toBe("EURUSDc");
    expect(fromDb.shouldImport).toBe(false);

    const noDb = reconcileReplayPrefs(local, null, true);
    expect(noDb.settings).toEqual(local);
    expect(noDb.shouldImport).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/replayPrefs.test.ts`
Expected: FAIL — cannot resolve `./replayPrefs`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/replayPrefs.ts`:

```ts
// Persisted replay-config popup inputs. Mirrors lib/chartPrefs.ts: versioned (v1)
// form values, normalized/clamped on load, localStorage read/write here, DB
// persistence + reconcile driven by hooks/useReplayPrefs.ts. These are the RAW
// modal inputs — cursor/range are still derived at submit time in the modal.
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "./candles";

export interface ReplayFormPrefs {
  version: 1;
  symbol: Sym;
  timeframe: Timeframe;
  startDate: string;   // "yyyy-mm-dd" or "" (kept only if valid pattern)
  historyBars: number; // clamped [HISTORY_MIN, HISTORY_MAX]
  speed: number;       // clamped [SPEED_MIN, SPEED_MAX]
}

export const HISTORY_MIN = 100, HISTORY_MAX = 1000;
export const SPEED_MIN = 1, SPEED_MAX = 10;

export const DEFAULT_REPLAY_PREFS: ReplayFormPrefs = {
  version: 1,
  symbol: "XAUUSDc",
  timeframe: "M15",
  startDate: "",
  historyBars: 300,
  speed: 4,
};

const KEY = "mt5j.replay.config";
export const STORAGE_KEY = KEY;

function clampInt(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : fallback;
  return Math.min(hi, Math.max(lo, n));
}
function oneOf<T extends string>(v: unknown, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(v as string) ? (v as T) : fallback;
}
function isoDate(v: unknown, fallback: string): string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : fallback;
}

// Coerce any stored/DB/corrupt object into a valid v1 ReplayFormPrefs.
export function normalizeReplayPrefs(raw: unknown): ReplayFormPrefs {
  if (raw === null || typeof raw !== "object") return { ...DEFAULT_REPLAY_PREFS };
  const p = raw as Record<string, unknown>;
  const D = DEFAULT_REPLAY_PREFS;
  return {
    version: 1,
    symbol: oneOf(p.symbol, SYMBOLS, D.symbol),
    timeframe: oneOf(p.timeframe, TIMEFRAMES, D.timeframe),
    startDate: isoDate(p.startDate, D.startDate),
    historyBars: clampInt(p.historyBars, HISTORY_MIN, HISTORY_MAX, D.historyBars),
    speed: clampInt(p.speed, SPEED_MIN, SPEED_MAX, D.speed),
  };
}

export function loadReplayPrefs(store: Storage = localStorage): ReplayFormPrefs {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return { ...DEFAULT_REPLAY_PREFS };
    return normalizeReplayPrefs(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_REPLAY_PREFS };
  }
}

export function saveReplayPrefs(s: ReplayFormPrefs, store: Storage = localStorage): void {
  try {
    store.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota / private mode — safe to ignore */
  }
}

// DB is authoritative. Present -> DB wins (normalized). Absent -> keep local; if
// the browser actually had a stored row, seed the DB from it (shouldImport).
export function reconcileReplayPrefs(
  local: ReplayFormPrefs, dbParsed: unknown, localExists: boolean,
): { settings: ReplayFormPrefs; shouldImport: boolean } {
  if (dbParsed !== null && dbParsed !== undefined) {
    return { settings: normalizeReplayPrefs(dbParsed), shouldImport: false };
  }
  return { settings: local, shouldImport: localExists };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/replayPrefs.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/replayPrefs.ts frontend/src/lib/replayPrefs.test.ts
git commit -m "feat(replay): replayPrefs lib (normalize/clamp + localStorage + reconcile)"
```

---

### Task 4: Frontend useReplayPrefs hook

**Files:**
- Create: `frontend/src/hooks/useReplayPrefs.ts`

**Interfaces:**
- Consumes: `DEFAULT_REPLAY_PREFS`, `STORAGE_KEY`, `loadReplayPrefs`, `reconcileReplayPrefs`, `saveReplayPrefs`, `type ReplayFormPrefs` from `../lib/replayPrefs`.
- Produces: `useReplayPrefs(): { prefs: ReplayFormPrefs; save: (next: ReplayFormPrefs) => void }`.

No unit test: this repo does not unit-test fetch/effect hooks (there is no `useChartPrefs` test). It is verified by `npm run build` (Step 2) and the manual test in Task 5. This matches the existing pattern — do not add a test harness for it.

- [ ] **Step 1: Write the hook**

Create `frontend/src/hooks/useReplayPrefs.ts`:

```ts
import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_REPLAY_PREFS, STORAGE_KEY, loadReplayPrefs, reconcileReplayPrefs,
  saveReplayPrefs, type ReplayFormPrefs,
} from "../lib/replayPrefs";

function putPrefs(s: ReplayFormPrefs): void {
  // Fire-and-forget; a failed PUT leaves localStorage as the source of truth.
  void fetch("/api/replay/prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(s),
  }).catch(() => { /* offline / dev — persistence is best-effort */ });
}

// Instant localStorage render, then reconcile with the DB (authoritative) once
// on mount. `save` (called on modal submit) writes localStorage immediately and
// PUTs — no debounce, since it fires once per launch rather than per keystroke.
export function useReplayPrefs(): {
  prefs: ReplayFormPrefs;
  save: (next: ReplayFormPrefs) => void;
} {
  const [prefs, setPrefs] = useState<ReplayFormPrefs>(() => loadReplayPrefs());

  useEffect(() => {
    let alive = true;
    const localExists = (() => {
      try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
    })();
    fetch("/api/replay/prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { prefs: unknown } | null) => {
        if (!alive || !body) return;
        const { settings, shouldImport } =
          reconcileReplayPrefs(loadReplayPrefs(), body.prefs, localExists);
        setPrefs(settings);
        saveReplayPrefs(settings);
        if (shouldImport) putPrefs(settings);   // seed DB from this browser
      })
      .catch(() => { /* offline / dev — keep localStorage state */ });
    return () => { alive = false; };
  }, []);

  const save = useCallback((next: ReplayFormPrefs) => {
    setPrefs(next);
    saveReplayPrefs(next);   // instant + local source of truth
    putPrefs(next);
  }, []);

  return { prefs, save };
}

export { DEFAULT_REPLAY_PREFS };
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds, no TypeScript errors. (The hook is not yet imported anywhere; this only proves it type-checks.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useReplayPrefs.ts
git commit -m "feat(replay): useReplayPrefs hook (localStorage + DB reconcile, save-on-submit)"
```

---

### Task 5: Wire prefs into the modal and Chart

**Files:**
- Modify: `frontend/src/components/ReplayConfigModal.tsx`
- Modify: `frontend/src/pages/Chart.tsx` (import + `onStart` at ~L70, modal render at ~L98)

**Interfaces:**
- Consumes: `useReplayPrefs` (Task 4); `type ReplayFormPrefs`, bounds `HISTORY_MIN/HISTORY_MAX/SPEED_MIN/SPEED_MAX` from `../lib/replayPrefs`; existing `ReplayConfig`, `replay.start`.
- Produces: `ReplayConfigModal` now takes `initial: ReplayFormPrefs` and calls `onStart(cfg: ReplayConfig, form: ReplayFormPrefs)`.

- [ ] **Step 1: Update ReplayConfigModal to seed from `initial` and emit the form snapshot**

Replace the top of `frontend/src/components/ReplayConfigModal.tsx` (imports, props, state, submit) with:

```tsx
import { useState } from "react";
import { SYMBOLS, TIMEFRAMES, timeframeMs, type Sym, type Timeframe } from "../lib/candles";
import {
  HISTORY_MIN, HISTORY_MAX, SPEED_MIN, SPEED_MAX, type ReplayFormPrefs,
} from "../lib/replayPrefs";
import type { ReplayConfig } from "../hooks/useReplaySession";

// Config for a new replay: symbol, timeframe, a start date (the reveal cursor),
// how many bars of history to show before it, and playback speed. range_start is
// cursor - historyBars*tf; range_end is "now" (reveal target). Fields are seeded
// from `initial` (last-launched prefs) and the raw form is handed back on submit.
export default function ReplayConfigModal(props: {
  initial: ReplayFormPrefs;
  onStart: (cfg: ReplayConfig, form: ReplayFormPrefs) => void;
  onCancel: () => void;
}) {
  const [symbol, setSymbol] = useState<Sym>(props.initial.symbol);
  const [tf, setTf] = useState<Timeframe>(props.initial.timeframe);
  const [startDate, setStartDate] = useState<string>(props.initial.startDate);
  const [historyBars, setHistoryBars] = useState(props.initial.historyBars);
  const [speed, setSpeed] = useState(props.initial.speed);

  const submit = () => {
    const cursor = startDate ? new Date(startDate + "T00:00:00Z").getTime() : Date.now() - timeframeMs(tf) * 100;
    const range_start_msc = cursor - timeframeMs(tf) * historyBars;
    props.onStart(
      {
        symbol, timeframe: tf,
        range_start_msc, range_end_msc: Date.now(),
        cursor_start_msc: cursor, speed,
      },
      { version: 1, symbol, timeframe: tf, startDate, historyBars, speed },
    );
  };
```

Then update the two range inputs to use the shared bounds (so the plan's single source of truth is honoured):

- History bars slider: `min={HISTORY_MIN} max={HISTORY_MAX}` (leave `step={50}`).
- Speed slider: `min={SPEED_MIN} max={SPEED_MAX}`.

The rest of the JSX (labels, buttons) is unchanged.

- [ ] **Step 2: Wire the hook into Chart.tsx**

In `frontend/src/pages/Chart.tsx`:

Add the import near the other lib imports:

```tsx
import { useReplayPrefs } from "../hooks/useReplayPrefs";
```

Inside the component, alongside the other hooks (e.g. right after the `useReplaySession()` line), add:

```tsx
  const replayPrefs = useReplayPrefs();
```

Change `onStart` (currently at ~L70) to accept the form snapshot and persist it:

```tsx
  const onStart = (cfg: ReplayConfig, form: ReplayFormPrefs) => {
    setConfigOpen(false); setReplayOpen(true);
    // Point the chart at the replay symbol/tf so CandleChart fetches the right series.
    setParams(new URLSearchParams({ symbol: cfg.symbol, tf: cfg.timeframe }), { replace: true });
    replayPrefs.save(form);   // remember these specs for next time
    replay.start(cfg);
  };
```

Add the `ReplayFormPrefs` type to the existing replay-lib import in Chart.tsx (or add a new import line):

```tsx
import type { ReplayFormPrefs } from "../lib/replayPrefs";
```

Pass `initial` to the modal (render at ~L98):

```tsx
      {configOpen && <ReplayConfigModal initial={replayPrefs.prefs} onStart={onStart} onCancel={exitReplay} />}
```

- [ ] **Step 3: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds. If `onStart`'s new signature or the `initial` prop is missing anywhere, TypeScript fails here — fix until clean.

- [ ] **Step 4: Run the full frontend + backend test suites**

Run: `cd frontend && npx vitest run` then (from repo root) `uv run pytest`
Expected: vitest all green; pytest all green. Paste both outputs.

- [ ] **Step 5: Manual verification**

Start the server (`uv run journal serve`), open `/chart`, click into replay. Set non-default specs (e.g. `BTCUSDc` / `M5` / a start date / 700 bars / speed 8), launch, then exit and reopen the popup. Confirm every field is pre-filled with those specs. Confirm `uv run journal rebuild` still succeeds and does NOT wipe the prefs (reopen popup after rebuild — still pre-filled).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ReplayConfigModal.tsx frontend/src/pages/Chart.tsx
git commit -m "feat(replay): persist config popup prefs (seed from last launch, save on Mulai)"
```

---

## Post-plan

- [ ] Run `graphify update .` to refresh the code graph.
- [ ] Update `MEMORY.md` / `chart-segment-phases.md` memory note: Phase D now also persists replay config prefs (app_prefs key `"replay"`, no migration).
```
