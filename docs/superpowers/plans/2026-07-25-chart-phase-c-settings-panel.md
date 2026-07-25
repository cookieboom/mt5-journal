# Chart Phase C — Settings Panel + Preference Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase B's minimal chart settings popover with a full settings drawer whose preferences persist to a DB-backed, cross-browser store (localStorage mirror for instant render), and fold in the deferred in-memory candle cap.

**Architecture:** Backend gains a generic `app_prefs(key,value,updated_ms)` JSON-blob table (migration `005`) plus a pure-DB `prefs_store` module and two thin `/api/chart/prefs` routes. The frontend extends `ChartSettings` to v1 with a legacy migration, adds a `useChartPrefs` hook (instant localStorage load → DB reconcile → debounced write-through), a grouped right-side `ChartSettingsDrawer`, and wires the new knobs into `CandleChart`/`useChartData` via `applyOptions` (plus a series-recreate path for chart type) and a live-correct `maxBars` cap.

**Tech Stack:** Python 3.12 · sqlite3 (stdlib) · FastAPI · pytest — React 18 · TypeScript · Vite · lightweight-charts 5.2.0 · vitest · Tailwind.

## Global Constraints

- **No new dependencies** (backend or frontend). Color inputs use native `<input type="color">`.
- **Rule 1 / M9:** `web/` never touches the MT5 bridge. Prefs are pure DB.
- **Rule 3:** timestamps epoch-**ms**, integer, broker-server UTC; divide by 1000 only at the lightweight-charts boundary; WIB (UTC+7) display-only.
- **Rule 4:** `NULL`=unknown, `0`=none set (the volume-row fix depends on this).
- **Rule 6:** the prefs table is durable app config, not a chart cache; `journal rebuild` must not touch it (it only drops+rebuilds `trades`).
- **Schema changes go through a migration file**, never an in-place edit of `schema.sql` after data exists. The new table goes in **both** `schema.sql` (fresh path) and `migrations/005_*.sql`, and `SCHEMA_VERSION` bumps `4 → 5`; `tests/test_migrations.py` asserts fresh == migrated.
- **Money is USC** — never printed bare as "$" (existing `money()` handles it; unchanged here).
- **Definition of done:** `uv run pytest` green (output pasted), `npm --prefix frontend run build` 0 errors, `uv run journal rebuild` still succeeds, `graphify update .` run.

---

### Task 1: Backend — `app_prefs` table + `prefs_store` module

**Files:**
- Modify: `src/journal/store/schema.sql` (append `app_prefs` table)
- Create: `src/journal/store/migrations/005_app_prefs.sql`
- Modify: `src/journal/store/db.py` (`SCHEMA_VERSION = 5`)
- Create: `src/journal/store/prefs_store.py`
- Test: `tests/test_prefs_store.py`

**Interfaces:**
- Produces: `prefs_store.get_pref(conn, key: str) -> str | None`, `prefs_store.set_pref(conn, key: str, value: str, updated_ms: int | None = None) -> int`, `prefs_store.get_chart_prefs(conn) -> Any | None`, `prefs_store.set_chart_prefs(conn, prefs: Any) -> int`, constant `prefs_store.CHART_KEY = "chart"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prefs_store.py`:

```python
"""app_prefs store — pure DB, no bridge. Roundtrip, upsert, and the chart-JSON
convenience wrappers. Mirrors tests/test_candles_store.py: seeded tmp DB, no HTTP."""
from __future__ import annotations

from journal.store import prefs_store as ps
from journal.store.db import connect

import pytest


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def test_get_pref_unknown_key_is_none(conn):
    assert ps.get_pref(conn, "nope") is None


def test_set_then_get_roundtrips_raw_text(conn):
    ps.set_pref(conn, "k", '{"a":1}', updated_ms=111)
    assert ps.get_pref(conn, "k") == '{"a":1}'


def test_set_pref_upserts_same_key_and_bumps_updated_ms(conn):
    ps.set_pref(conn, "k", "v1", updated_ms=100)
    ts = ps.set_pref(conn, "k", "v2", updated_ms=200)
    assert ps.get_pref(conn, "k") == "v2"
    assert ts == 200
    row = conn.execute("SELECT COUNT(*) AS n FROM app_prefs WHERE key='k'").fetchone()
    assert row["n"] == 1  # upsert, not a second row


def test_chart_prefs_roundtrip_parses_json(conn):
    assert ps.get_chart_prefs(conn) is None
    ts = ps.set_chart_prefs(conn, {"version": 1, "theme": "light"})
    assert isinstance(ts, int) and ts > 0
    assert ps.get_chart_prefs(conn) == {"version": 1, "theme": "light"}
    # stored under the reserved chart key
    assert ps.get_pref(conn, ps.CHART_KEY) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prefs_store.py -q`
Expected: FAIL — `ModuleNotFoundError: journal.store.prefs_store` (and `no such table: app_prefs`).

- [ ] **Step 3: Add the table to `schema.sql`**

Append to the end of `src/journal/store/schema.sql`:

```sql
-- ---------------------------------------------------------------- app prefs

-- Single-value application preferences (chart settings live here under key
-- 'chart'). Durable app config, NOT a chart cache and NOT derived from raw, so
-- `journal rebuild` never touches it. Value is an opaque JSON blob owned by the
-- client; the store does not parse or validate it. updated_ms = true UTC.
CREATE TABLE IF NOT EXISTS app_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_ms INTEGER NOT NULL
);
```

- [ ] **Step 4: Create migration `005_app_prefs.sql`**

Create `src/journal/store/migrations/005_app_prefs.sql`:

```sql
-- Migration 005 — app_prefs (Chart Phase C preference persistence).
--
-- Brings a v4 database forward to v5. ADDITIVE only: one new table, no existing
-- table touched. The same DDL lives in schema.sql for fresh databases; the two
-- must stay in lockstep (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- Durable app config (chart settings under key 'chart'). NOT a chart cache and
-- NOT derived from raw — `journal rebuild` never touches it. No bridge.
CREATE TABLE IF NOT EXISTS app_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_ms INTEGER NOT NULL
);
```

- [ ] **Step 5: Bump `SCHEMA_VERSION` in `db.py`**

In `src/journal/store/db.py`, change:

```python
SCHEMA_VERSION = 4
```

to:

```python
SCHEMA_VERSION = 5
```

- [ ] **Step 6: Create `prefs_store.py`**

Create `src/journal/store/prefs_store.py`:

```python
"""app_prefs — single-value application preferences, pure DB. The web reads and
writes chart settings here so they survive across browsers. NOT a chart cache
and NOT derived from raw, so `journal rebuild` never touches it. No MT5 adapter
import — the M9 boundary holds here too (CLAUDE.md rules 1, 12).

Values are opaque JSON text owned by the client; this module does not validate
the shape. The chart convenience wrappers only json.dumps/loads around the
generic key/value core."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import now_ms

CHART_KEY = "chart"


def get_pref(conn: sqlite3.Connection, key: str) -> str | None:
    """Raw JSON text stored under `key`, or None if absent."""
    row = conn.execute("SELECT value FROM app_prefs WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_pref(conn: sqlite3.Connection, key: str, value: str,
             updated_ms: int | None = None) -> int:
    """Upsert `value` (raw JSON text) under `key`. Returns the updated_ms stamp."""
    ts = now_ms() if updated_ms is None else updated_ms
    conn.execute(
        "INSERT INTO app_prefs (key, value, updated_ms) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_ms = excluded.updated_ms",
        (key, value, ts),
    )
    conn.commit()
    return ts


def get_chart_prefs(conn: sqlite3.Connection) -> Any | None:
    """Parsed chart settings JSON, or None if never saved."""
    raw = get_pref(conn, CHART_KEY)
    return json.loads(raw) if raw is not None else None


def set_chart_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Persist chart settings (serialised to JSON). Returns the updated_ms stamp."""
    return set_pref(conn, CHART_KEY, json.dumps(prefs), now_ms())
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_prefs_store.py tests/test_migrations.py -q`
Expected: PASS — prefs roundtrip/upsert green, and `test_migrations` still green (fresh v5 == migrated v5).

- [ ] **Step 8: Commit**

```bash
git add src/journal/store/schema.sql src/journal/store/migrations/005_app_prefs.sql \
        src/journal/store/db.py src/journal/store/prefs_store.py tests/test_prefs_store.py
git commit -m "feat(chart-c): app_prefs table + prefs_store (migration 005, schema v5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Backend — `GET`/`PUT /api/chart/prefs` routes

**Files:**
- Modify: `src/journal/web/app.py` (import `prefs_store`; add two routes near `/api/candles`, ~line 160)
- Test: `tests/test_prefs_store.py` (add a payload-shape test using the store wrappers — routes are thin glue, tested like `api.candles_payload`, no HTTP/httpx)

**Interfaces:**
- Consumes: `prefs_store.get_chart_prefs`, `prefs_store.set_chart_prefs` (Task 1).
- Produces: `GET /api/chart/prefs` → `{"prefs": <json>|null}`; `PUT /api/chart/prefs` (JSON body) → `{"ok": true, "updated_ms": <int>}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prefs_store.py` (the route is glue; this pins the exact JSON envelope the client relies on):

```python
def test_chart_prefs_envelope_shape(conn):
    # GET envelope: {"prefs": null} before any save, {"prefs": {...}} after.
    assert {"prefs": ps.get_chart_prefs(conn)} == {"prefs": None}
    ps.set_chart_prefs(conn, {"version": 1, "theme": "dark", "grid": True})
    assert {"prefs": ps.get_chart_prefs(conn)} == {
        "prefs": {"version": 1, "theme": "dark", "grid": True}
    }
```

- [ ] **Step 2: Run test to verify it fails, then passes with Task 1 code**

Run: `uv run pytest tests/test_prefs_store.py::test_chart_prefs_envelope_shape -q`
Expected: PASS (Task 1 already provides the wrappers). If it fails, Task 1 is incomplete.

- [ ] **Step 3: Add the import in `app.py`**

In `src/journal/web/app.py`, add to the store imports (there is already `from ..store.db import connect`):

```python
from ..store import prefs_store
```

- [ ] **Step 4: Add the two routes**

In `src/journal/web/app.py`, immediately after the `@app.get("/api/candles")` handler block (before the `# --- two-step trade command` comment, ~line 180), insert:

```python
    @app.get("/api/chart/prefs")
    def api_get_chart_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        """Chart settings blob, cross-browser. `prefs` is null until first save;
        the client then falls back to its own defaults / localStorage. Pure DB —
        never talks to the bridge (M9 boundary)."""
        return JSONResponse({"prefs": prefs_store.get_chart_prefs(conn)})

    @app.put("/api/chart/prefs")
    def api_put_chart_prefs(
        prefs=Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Upsert the chart settings blob under key 'chart'. The server stamps
        updated_ms; the body is stored verbatim (the client owns the schema)."""
        ts = prefs_store.set_chart_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})
```

- [ ] **Step 5: Manual route verification (glue has no unit test, same as `/api/candles`)**

Run (uses a temp DB so nothing real is touched):

```bash
JOURNAL_DB="$CLAUDE_JOB_DIR/tmp/prefs.db" uv run journal serve --db "$CLAUDE_JOB_DIR/tmp/prefs.db" &
sleep 3
curl -s localhost:8000/api/chart/prefs                    # -> {"prefs":null}
curl -s -X PUT localhost:8000/api/chart/prefs \
     -H 'Content-Type: application/json' \
     -d '{"version":1,"theme":"light","grid":false}'      # -> {"ok":true,"updated_ms":...}
curl -s localhost:8000/api/chart/prefs                    # -> {"prefs":{"version":1,...}}
kill %1
```
Expected: the three responses above, in order.

- [ ] **Step 6: Run the full backend suite + commit**

Run: `uv run pytest -q`
Expected: all green.

```bash
git add src/journal/web/app.py tests/test_prefs_store.py
git commit -m "feat(chart-c): GET/PUT /api/chart/prefs routes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — `chartPrefs.ts` v1 schema, migration, clamp, reconcile

**Files:**
- Modify: `frontend/src/lib/chartPrefs.ts` (full extension)
- Test: `frontend/src/lib/chartPrefs.test.ts` (extend existing)

**Interfaces:**
- Consumes: `SYMBOLS`, `TIMEFRAMES`, `Sym`, `Timeframe` from `./candles`.
- Produces: `ChartSettings` (v1), `DEFAULT_SETTINGS`, `normalizeSettings(raw: unknown) -> ChartSettings`, `loadChartSettings(store?) -> ChartSettings`, `saveChartSettings(s, store?) -> void`, `reconcilePrefs(local: ChartSettings, dbParsed: unknown, localExists: boolean) -> { settings: ChartSettings; shouldImport: boolean }`, `parseSelection(params: URLSearchParams, defaults?: { symbol: Sym; tf: Timeframe }) -> { symbol: Sym; tf: Timeframe }`.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/lib/chartPrefs.test.ts` (import the new symbols at the top of the file):

```ts
import {
  DEFAULT_SETTINGS, normalizeSettings, loadChartSettings, saveChartSettings,
  reconcilePrefs, parseSelection,
} from "./chartPrefs";

// A throwaway Storage for load/save tests.
function mem(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    length: 0,
  } as Storage;
}

describe("chartPrefs v1", () => {
  it("migrates a legacy {theme,grid} object (no version) to full v1 defaults", () => {
    const s = normalizeSettings({ theme: "light", grid: false });
    expect(s.version).toBe(1);
    expect(s.theme).toBe("light");
    expect(s.grid).toBe(false);
    expect(s.colors).toEqual(DEFAULT_SETTINGS.colors);   // filled from defaults
    expect(s.chartType).toBe(DEFAULT_SETTINGS.chartType);
    expect(s.initialBars).toBe(DEFAULT_SETTINGS.initialBars);
  });

  it("clamps initialBars and maxBars into bounds", () => {
    const a = normalizeSettings({ version: 1, initialBars: 5, maxBars: 999999 });
    expect(a.initialBars).toBe(100);   // floor
    expect(a.maxBars).toBe(10000);     // ceil
    const b = normalizeSettings({ version: 1, initialBars: 900, maxBars: 200 });
    expect(b.maxBars).toBeGreaterThanOrEqual(b.initialBars); // maxBars >= initialBars
  });

  it("falls back to defaults for garbage input", () => {
    expect(normalizeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(normalizeSettings("nope")).toEqual(DEFAULT_SETTINGS);
  });

  it("load/save roundtrips through a Storage", () => {
    const store = mem();
    const custom = { ...DEFAULT_SETTINGS, theme: "light" as const };
    saveChartSettings(custom, store);
    expect(loadChartSettings(store)).toEqual(custom);
  });

  it("reconcile: DB present wins and is normalized", () => {
    const local = { ...DEFAULT_SETTINGS, theme: "light" as const };
    const r = reconcilePrefs(local, { version: 1, theme: "dark", initialBars: 5 }, true);
    expect(r.settings.theme).toBe("dark");
    expect(r.settings.initialBars).toBe(100);  // normalized/clamped
    expect(r.shouldImport).toBe(false);
  });

  it("reconcile: DB absent + local existed -> import local", () => {
    const local = { ...DEFAULT_SETTINGS, grid: false };
    const r = reconcilePrefs(local, null, true);
    expect(r.settings).toEqual(local);
    expect(r.shouldImport).toBe(true);
  });

  it("reconcile: DB absent + no local -> defaults, no import", () => {
    const r = reconcilePrefs(DEFAULT_SETTINGS, null, false);
    expect(r.shouldImport).toBe(false);
  });

  it("parseSelection: URL wins, else saved default, else hard default", () => {
    expect(parseSelection(new URLSearchParams("symbol=BTCUSDc&tf=H1")))
      .toEqual({ symbol: "BTCUSDc", tf: "H1" });
    expect(parseSelection(new URLSearchParams(""), { symbol: "EURUSDc", tf: "H4" }))
      .toEqual({ symbol: "EURUSDc", tf: "H4" });
    expect(parseSelection(new URLSearchParams("")))
      .toEqual({ symbol: "XAUUSDc", tf: "M5" });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix frontend test -- chartPrefs`
Expected: FAIL — `normalizeSettings`/`reconcilePrefs` not exported; `ChartSettings` has no `colors`.

- [ ] **Step 3: Rewrite `chartPrefs.ts`**

Replace the entire contents of `frontend/src/lib/chartPrefs.ts` with:

```ts
// URL-param selection + persisted chart appearance. Selection defaults to
// XAUUSDc / M5 (overridable by saved defaults). Settings are versioned (v1);
// a legacy Phase B object {theme,grid} (no version) migrates in place. DB
// persistence + localStorage mirror live in hooks/useChartPrefs.ts.
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "./candles";

export type ChartTheme = "dark" | "light";
export type ChartType = "candle" | "bar" | "line" | "area";
export type CrosshairStyle = "normal" | "magnet" | "hidden";
export type PriceScaleMode = "linear" | "log";

export interface ChartSettings {
  version: 1;
  theme: ChartTheme;
  grid: boolean;
  colors: { up: string; down: string; wick: string };
  chartType: ChartType;
  crosshair: CrosshairStyle;
  priceScale: PriceScaleMode;
  autoScale: boolean;
  lastPriceLine: boolean;
  liveOverlay: boolean;
  defaultSymbol: Sym;
  defaultTimeframe: Timeframe;
  initialBars: number;
  maxBars: number;
}

export const DEFAULT_SETTINGS: ChartSettings = {
  version: 1,
  theme: "dark",
  grid: true,
  colors: { up: "#34d399", down: "#fb7185", wick: "#9a97c4" },
  chartType: "candle",
  crosshair: "normal",
  priceScale: "linear",
  autoScale: true,
  lastPriceLine: true,
  liveOverlay: true,
  defaultSymbol: "XAUUSDc",
  defaultTimeframe: "M5",
  initialBars: 300,
  maxBars: 3000,
};

const KEY = "mt5j.chart.settings";

// Bounds — also the numbers shown next to the inputs in the drawer.
const INITIAL_MIN = 100, INITIAL_MAX = 1000;
const MAX_MIN = 500, MAX_MAX = 10000;

function clampInt(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : fallback;
  return Math.min(hi, Math.max(lo, n));
}
function hex(v: unknown, fallback: string): string {
  return typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v) ? v : fallback;
}
function oneOf<T extends string>(v: unknown, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(v as string) ? (v as T) : fallback;
}

// Coerce any stored/DB object (legacy or corrupt) into a valid v1 ChartSettings.
// Legacy Phase B objects lack `version`; their theme/grid are kept, everything
// else filled from defaults. Numeric fields are clamped; maxBars is raised to
// initialBars if smaller.
export function normalizeSettings(raw: unknown): ChartSettings {
  if (raw === null || typeof raw !== "object") return { ...DEFAULT_SETTINGS };
  const p = raw as Record<string, unknown>;
  const c = (p.colors ?? {}) as Record<string, unknown>;
  const D = DEFAULT_SETTINGS;
  const initialBars = clampInt(p.initialBars, INITIAL_MIN, INITIAL_MAX, D.initialBars);
  let maxBars = clampInt(p.maxBars, MAX_MIN, MAX_MAX, D.maxBars);
  if (maxBars < initialBars) maxBars = initialBars;
  return {
    version: 1,
    theme: oneOf(p.theme, ["dark", "light"] as const, D.theme),
    grid: typeof p.grid === "boolean" ? p.grid : D.grid,
    colors: {
      up: hex(c.up, D.colors.up),
      down: hex(c.down, D.colors.down),
      wick: hex(c.wick, D.colors.wick),
    },
    chartType: oneOf(p.chartType, ["candle", "bar", "line", "area"] as const, D.chartType),
    crosshair: oneOf(p.crosshair, ["normal", "magnet", "hidden"] as const, D.crosshair),
    priceScale: oneOf(p.priceScale, ["linear", "log"] as const, D.priceScale),
    autoScale: typeof p.autoScale === "boolean" ? p.autoScale : D.autoScale,
    lastPriceLine: typeof p.lastPriceLine === "boolean" ? p.lastPriceLine : D.lastPriceLine,
    liveOverlay: typeof p.liveOverlay === "boolean" ? p.liveOverlay : D.liveOverlay,
    defaultSymbol: oneOf(p.defaultSymbol, SYMBOLS, D.defaultSymbol),
    defaultTimeframe: oneOf(p.defaultTimeframe, TIMEFRAMES, D.defaultTimeframe),
    initialBars,
    maxBars,
  };
}

export function loadChartSettings(store: Storage = localStorage): ChartSettings {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveChartSettings(s: ChartSettings, store: Storage = localStorage): void {
  try {
    store.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota / private mode — appearance-only, safe to ignore */
  }
}

// DB is authoritative. Present -> DB wins (normalized). Absent -> keep local; if
// the browser actually had a stored row, seed the DB from it (shouldImport).
export function reconcilePrefs(
  local: ChartSettings, dbParsed: unknown, localExists: boolean,
): { settings: ChartSettings; shouldImport: boolean } {
  if (dbParsed !== null && dbParsed !== undefined) {
    return { settings: normalizeSettings(dbParsed), shouldImport: false };
  }
  return { settings: local, shouldImport: localExists };
}

export function parseSelection(
  params: URLSearchParams,
  defaults: { symbol: Sym; tf: Timeframe } = { symbol: "XAUUSDc", tf: "M5" },
): { symbol: Sym; tf: Timeframe } {
  const s = params.get("symbol");
  const t = params.get("tf");
  return {
    symbol: (SYMBOLS as string[]).includes(s ?? "") ? (s as Sym) : defaults.symbol,
    tf: (TIMEFRAMES as string[]).includes(t ?? "") ? (t as Timeframe) : defaults.tf,
  };
}

// The localStorage key, exported so useChartPrefs can probe existence for the
// import decision.
export const STORAGE_KEY = KEY;
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix frontend test -- chartPrefs`
Expected: PASS (all cases from Step 1).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/chartPrefs.ts frontend/src/lib/chartPrefs.test.ts
git commit -m "feat(chart-c): ChartSettings v1 schema, legacy migration, reconcile

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — `capCandles` + `fetchCandles` r.ok fix in `candles.ts`

**Files:**
- Modify: `frontend/src/lib/candles.ts` (add `capCandles`; fix `fetchCandles`)
- Test: `frontend/src/lib/candles.test.ts` (extend existing)

**Interfaces:**
- Produces: `capCandles(candles: Candle[], maxBars: number) -> Candle[]` (keeps newest, drops oldest, preserves ascending order). `initialWindow(tf, nowMs, bars?)` already accepts a `bars` arg — reused as `initialBars` by Task 5, no signature change.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/lib/candles.test.ts`:

```ts
import { capCandles } from "./candles";
import type { Candle } from "./types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 0 });

describe("capCandles", () => {
  it("returns the array unchanged when at or under the cap", () => {
    const cs = [bar(1), bar(2), bar(3)];
    expect(capCandles(cs, 3)).toBe(cs);
    expect(capCandles(cs, 10)).toBe(cs);
  });
  it("drops the OLDEST bars beyond maxBars, keeping the newest and order", () => {
    const cs = [bar(1), bar(2), bar(3), bar(4), bar(5)];
    expect(capCandles(cs, 2).map((c) => c.time_msc)).toEqual([4, 5]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix frontend test -- candles`
Expected: FAIL — `capCandles` is not exported.

- [ ] **Step 3: Add `capCandles` and fix `fetchCandles`**

In `frontend/src/lib/candles.ts`, add after `mergeCandles`:

```ts
// Bound the in-memory array: keep the newest `maxBars` bars, drop the oldest.
// Live-correct — the now side is always retained. Assumes ascending order
// (mergeCandles guarantees it). Returns the same array when under the cap so
// callers can skip a state update.
export function capCandles(candles: Candle[], maxBars: number): Candle[] {
  if (candles.length <= maxBars) return candles;
  return candles.slice(candles.length - maxBars);
}
```

Then replace the body of `fetchCandles` (currently `const body = await r.json();` before the `r.ok` check) with an `r.ok`-first version:

```ts
export async function fetchCandles(
  symbol: string, tf: Timeframe, fromMs: number, toMs: number,
): Promise<CandlesResponse> {
  const q = new URLSearchParams({
    symbol, timeframe: tf, from: String(Math.floor(fromMs)), to: String(Math.floor(toMs)),
  });
  const r = await fetch(`/api/candles?${q}`);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const b = await r.json();
      if (b && typeof b.error === "string") msg = b.error;
    } catch {
      /* non-JSON error page — keep HTTP {status} */
    }
    throw new Error(msg);
  }
  return (await r.json()) as CandlesResponse;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix frontend test -- candles`
Expected: PASS (capCandles cases; existing candles tests still green).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/candles.ts frontend/src/lib/candles.test.ts
git commit -m "feat(chart-c): capCandles helper + fetchCandles r.ok-before-json fix

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — `useChartData` consumes `initialBars`/`maxBars`, caps + gates pan

**Files:**
- Modify: `frontend/src/hooks/useChartData.ts`

**Interfaces:**
- Consumes: `capCandles`, `initialWindow(tf, now, initialBars)` (Task 4).
- Produces: `useChartData(symbol, tf, initialBars: number, maxBars: number)` — same return shape as before (`{ candles, status, error, lastBarMs, retry, loadOlder }`); array capped at `maxBars`, `loadOlder` no-ops once capped.

- [ ] **Step 1: Update the hook signature and imports**

In `frontend/src/hooks/useChartData.ts`, change the import to include `capCandles`:

```ts
import { capCandles, fetchCandles, initialWindow, mergeCandles, olderWindow, type Timeframe } from "../lib/candles";
```

Change the signature:

```ts
export function useChartData(symbol: string, tf: Timeframe, initialBars: number, maxBars: number) {
```

Add a cap ref beside the existing refs (near `loadingOlderRef`):

```ts
  // True once the in-memory array has reached maxBars. loadOlder becomes a
  // no-op so a pan can't keep re-loading bars that capCandles would re-drop.
  // Reset on every symbol/tf reset (the effect below) and on retry.
  const atCapRef = useRef(false);
```

- [ ] **Step 2: Cap on every merge**

In `load`, replace the success merge line:

```ts
      setCandles((prev) => mergeCandles(prev, resp.candles));
```

with:

```ts
      setCandles((prev) => {
        const merged = capCandles(mergeCandles(prev, resp.candles), maxBars);
        atCapRef.current = merged.length >= maxBars;
        return merged;
      });
```

In `loadOlder`, replace its success merge line the same way:

```ts
      setCandles((prev) => {
        const merged = capCandles(mergeCandles(prev, resp.candles), maxBars);
        atCapRef.current = merged.length >= maxBars;
        return merged;
      });
```

- [ ] **Step 3: Gate `loadOlder` and thread `initialBars`**

At the very top of `loadOlder`, add the cap gate (before the `loadingOlderRef` guard):

```ts
    if (atCapRef.current) return;         // history bound reached — raise maxBars to go further
    if (loadingOlderRef.current) return;
```

In the symbol/tf reset effect, reset the cap flag and use `initialBars`:

```ts
    setCandles([]); setStatus("loading"); setError(null); pollRef.current = 0;
    atCapRef.current = false;
    const [from, to] = initialWindow(tf, Date.now(), initialBars);
```

In `retry`, use `initialBars` too and reset the flag:

```ts
  const retry = useCallback(() => {
    genRef.current += 1;
    clearTimer();
    pollRef.current = 0;
    atCapRef.current = false;
    setStatus("polling");
    const [, to] = initialWindow(tf, Date.now(), initialBars);
    load(fromRef.current, to);
  }, [tf, initialBars, load]);
```

Update the reset effect and `load`/`loadOlder` dependency arrays to include the new values: the effect deps become `[symbol, tf, initialBars, load]`, `load` deps become `[symbol, tf, maxBars]`, and `loadOlder` deps become `[symbol, tf, maxBars]`.

- [ ] **Step 4: Build to verify types**

Run: `npm --prefix frontend run build`
Expected: 0 errors (callers updated in Task 9; if the build flags `Chart.tsx` passing too few args, that is expected until Task 9 — run this step's build after Task 9, or temporarily verify with `npx tsc --noEmit` scoped is not needed). To keep this task independently green, defer the full build to Task 9 and instead verify the hook file has no local type error by eye; the vitest suite (unchanged pure helpers) still passes:

Run: `npm --prefix frontend test -- candles chartPrefs`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useChartData.ts
git commit -m "feat(chart-c): useChartData caps at maxBars, gates pan, uses initialBars

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — `useChartPrefs` hook (instant load → DB reconcile → debounced write-through)

**Files:**
- Create: `frontend/src/hooks/useChartPrefs.ts`

**Interfaces:**
- Consumes: `loadChartSettings`, `saveChartSettings`, `reconcilePrefs`, `DEFAULT_SETTINGS`, `STORAGE_KEY`, `ChartSettings` (Task 3).
- Produces: `useChartPrefs() -> { settings: ChartSettings; update: (next: ChartSettings) => void; reset: () => void }`.

- [ ] **Step 1: Create the hook**

Create `frontend/src/hooks/useChartPrefs.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_SETTINGS, STORAGE_KEY, loadChartSettings, reconcilePrefs,
  saveChartSettings, type ChartSettings,
} from "../lib/chartPrefs";

const DEBOUNCE_MS = 400;

function putPrefs(s: ChartSettings): void {
  // Fire-and-forget; a failed PUT leaves localStorage as the source of truth.
  void fetch("/api/chart/prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(s),
  }).catch(() => { /* offline / dev — appearance-only */ });
}

// Instant localStorage render, then reconcile with the DB (authoritative), then
// write-through (localStorage immediately + debounced PUT) on every change.
export function useChartPrefs(): {
  settings: ChartSettings;
  update: (next: ChartSettings) => void;
  reset: () => void;
} {
  const [settings, setSettings] = useState<ChartSettings>(() => loadChartSettings());
  const putTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // DB reconcile once on mount.
  useEffect(() => {
    let alive = true;
    const localExists = (() => {
      try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
    })();
    fetch("/api/chart/prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { prefs: unknown } | null) => {
        if (!alive || !body) return;
        const { settings: next, shouldImport } =
          reconcilePrefs(loadChartSettings(), body.prefs, localExists);
        setSettings(next);
        saveChartSettings(next);
        if (shouldImport) putPrefs(next);   // seed DB from this browser
      })
      .catch(() => { /* offline / dev — keep localStorage state */ });
    return () => { alive = false; };
  }, []);

  const update = useCallback((next: ChartSettings) => {
    setSettings(next);
    saveChartSettings(next);               // instant + local source of truth
    if (putTimer.current) clearTimeout(putTimer.current);
    putTimer.current = setTimeout(() => putPrefs(next), DEBOUNCE_MS);
  }, []);

  const reset = useCallback(() => update({ ...DEFAULT_SETTINGS }), [update]);

  // Flush a pending debounced PUT on unmount so a quick change isn't lost.
  useEffect(() => () => { if (putTimer.current) clearTimeout(putTimer.current); }, []);

  return { settings, update, reset };
}
```

- [ ] **Step 2: Build to verify types**

Run: `npm --prefix frontend run build`
Expected: 0 errors for this file (it is not yet imported; `Chart.tsx` wiring is Task 9). If the build otherwise fails only on `useChartData` arity in `Chart.tsx`, that resolves in Task 9.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useChartPrefs.ts
git commit -m "feat(chart-c): useChartPrefs — instant load, DB reconcile, write-through

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — `ChartSettingsDrawer` (replaces popover) + toolbar gear

**Files:**
- Create: `frontend/src/components/ChartSettingsDrawer.tsx`
- Delete: `frontend/src/components/ChartSettingsPopover.tsx`
- Modify: `frontend/src/components/ChartToolbar.tsx` (gear toggles the drawer; drop the popover import/props)

**Interfaces:**
- Consumes: `ChartSettings`, `DEFAULT_SETTINGS` (Task 3); `SYMBOLS`, `TIMEFRAMES` (candles).
- Produces: `<ChartSettingsDrawer settings onChange onReset onClose />`; `ChartToolbar` gains `onReset` in its props and renders the drawer instead of the popover.

- [ ] **Step 1: Create the drawer**

Create `frontend/src/components/ChartSettingsDrawer.tsx`:

```tsx
import { useEffect } from "react";
import { SYMBOLS, TIMEFRAMES } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";

// Number inputs advertise their clamp bounds; normalizeSettings enforces them.
const INITIAL_MIN = 100, INITIAL_MAX = 1000, MAX_MIN = 500, MAX_MAX = 10000;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="text-muted text-[11px] uppercase tracking-wide mb-2">{title}</div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center justify-between text-[12px] gap-2">
      <span className="text-muted">{label}</span>
      {children}
    </label>
  );
}

export default function ChartSettingsDrawer({
  settings, onChange, onReset, onClose,
}: {
  settings: ChartSettings;
  onChange: (s: ChartSettings) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const set = <K extends keyof ChartSettings>(k: K, v: ChartSettings[K]) =>
    onChange({ ...settings, [k]: v });
  const setColor = (k: keyof ChartSettings["colors"], v: string) =>
    onChange({ ...settings, colors: { ...settings.colors, [k]: v } });

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div
        className="glass fixed right-0 top-0 z-40 h-full w-[300px] p-4 overflow-y-auto
                   flex flex-col"
        role="dialog"
        aria-label="Pengaturan chart"
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-ink text-[13px]">Pengaturan chart</div>
          <button onClick={onClose} className="text-muted hover:text-ink" aria-label="tutup">✕</button>
        </div>

        <div className="flex-1">
          <Section title="Tampilan">
            <Field label="Tema">
              <div className="flex gap-1">
                {(["dark", "light"] as const).map((t) => (
                  <button key={t} onClick={() => set("theme", t)}
                    className={"px-2 py-1 rounded-md capitalize text-[12px] " +
                      (settings.theme === t
                        ? "bg-violet/25 ring-1 ring-inset ring-violet/35 text-ink"
                        : "text-muted hover:text-ink")}>{t}</button>
                ))}
              </div>
            </Field>
            <Field label="Garis grid">
              <input type="checkbox" checked={settings.grid}
                onChange={(e) => set("grid", e.target.checked)} />
            </Field>
            <Field label="Warna naik">
              <input type="color" value={settings.colors.up}
                onChange={(e) => setColor("up", e.target.value)} />
            </Field>
            <Field label="Warna turun">
              <input type="color" value={settings.colors.down}
                onChange={(e) => setColor("down", e.target.value)} />
            </Field>
            <Field label="Warna wick">
              <input type="color" value={settings.colors.wick}
                onChange={(e) => setColor("wick", e.target.value)} />
            </Field>
            <Field label="Tipe chart">
              <select value={settings.chartType} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("chartType", e.target.value as ChartSettings["chartType"])}>
                <option value="candle" className="bg-bg">Candle</option>
                <option value="bar" className="bg-bg">Bar</option>
                <option value="line" className="bg-bg">Line</option>
                <option value="area" className="bg-bg">Area</option>
              </select>
            </Field>
            <Field label="Crosshair">
              <select value={settings.crosshair} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("crosshair", e.target.value as ChartSettings["crosshair"])}>
                <option value="normal" className="bg-bg">Normal</option>
                <option value="magnet" className="bg-bg">Magnet</option>
                <option value="hidden" className="bg-bg">Hidden</option>
              </select>
            </Field>
          </Section>

          <Section title="Skala">
            <Field label="Mode harga">
              <div className="flex gap-1">
                {(["linear", "log"] as const).map((m) => (
                  <button key={m} onClick={() => set("priceScale", m)}
                    className={"px-2 py-1 rounded-md capitalize text-[12px] " +
                      (settings.priceScale === m
                        ? "bg-violet/25 ring-1 ring-inset ring-violet/35 text-ink"
                        : "text-muted hover:text-ink")}>{m}</button>
                ))}
              </div>
            </Field>
            <Field label="Auto-scale">
              <input type="checkbox" checked={settings.autoScale}
                onChange={(e) => set("autoScale", e.target.checked)} />
            </Field>
            <Field label="Garis harga terakhir">
              <input type="checkbox" checked={settings.lastPriceLine}
                onChange={(e) => set("lastPriceLine", e.target.checked)} />
            </Field>
          </Section>

          <Section title="Data">
            <Field label={`Bar awal (${INITIAL_MIN}–${INITIAL_MAX})`}>
              <input type="number" min={INITIAL_MIN} max={INITIAL_MAX} value={settings.initialBars}
                className="glass bg-transparent w-20 px-1 py-0.5 num text-right"
                onChange={(e) => set("initialBars", Number(e.target.value))} />
            </Field>
            <Field label={`Maks bar (${MAX_MIN}–${MAX_MAX})`}>
              <input type="number" min={MAX_MIN} max={MAX_MAX} value={settings.maxBars}
                className="glass bg-transparent w-20 px-1 py-0.5 num text-right"
                onChange={(e) => set("maxBars", Number(e.target.value))} />
            </Field>
          </Section>

          <Section title="Perilaku">
            <Field label="Overlay live (SL/TP/entry)">
              <input type="checkbox" checked={settings.liveOverlay}
                onChange={(e) => set("liveOverlay", e.target.checked)} />
            </Field>
            <Field label="Symbol default">
              <select value={settings.defaultSymbol} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("defaultSymbol", e.target.value as ChartSettings["defaultSymbol"])}>
                {SYMBOLS.map((s) => <option key={s} value={s} className="bg-bg">{s}</option>)}
              </select>
            </Field>
            <Field label="Timeframe default">
              <select value={settings.defaultTimeframe} className="glass bg-transparent px-1 py-0.5"
                onChange={(e) => set("defaultTimeframe", e.target.value as ChartSettings["defaultTimeframe"])}>
                {TIMEFRAMES.map((t) => <option key={t} value={t} className="bg-bg">{t}</option>)}
              </select>
            </Field>
          </Section>
        </div>

        <button onClick={onReset}
          className="glass mt-2 px-3 py-1.5 text-[12px] text-muted hover:text-ink self-start">
          Reset ke default
        </button>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Rewire `ChartToolbar.tsx`**

Replace the popover import and its render block. Change the import line:

```tsx
import ChartSettingsDrawer from "./ChartSettingsDrawer";
```

Add `onReset` to the props destructure and type:

```tsx
export default function ChartToolbar({
  symbol, tf, settings, onSymbol, onTf, onSettings, onReset, onJumpNow,
}: {
  symbol: Sym;
  tf: Timeframe;
  settings: ChartSettings;
  onSymbol: (s: Sym) => void;
  onTf: (t: Timeframe) => void;
  onSettings: (s: ChartSettings) => void;
  onReset: () => void;
  onJumpNow: () => void;
}) {
```

Replace the gear `<div className="relative ml-auto"> ... </div>` block with:

```tsx
      <div className="ml-auto">
        <button
          onClick={() => setGear((g) => !g)}
          className="glass px-2.5 py-1 text-[13px] text-muted hover:text-ink"
          aria-label="settings"
        >
          ⚙
        </button>
        {gear && (
          <ChartSettingsDrawer
            settings={settings}
            onChange={onSettings}
            onReset={onReset}
            onClose={() => setGear(false)}
          />
        )}
      </div>
```

- [ ] **Step 3: Delete the popover**

```bash
git rm frontend/src/components/ChartSettingsPopover.tsx
```

- [ ] **Step 4: Build**

Run: `npm --prefix frontend run build`
Expected: `ChartToolbar` will now require an `onReset` prop from `Chart.tsx`; that call site is fixed in Task 9. If the only errors are the missing `onReset` and `useChartData` arity in `Chart.tsx`, that is expected — proceed; Task 9 makes the build green. Confirm no errors originate inside `ChartSettingsDrawer.tsx` itself.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChartSettingsDrawer.tsx frontend/src/components/ChartToolbar.tsx
git commit -m "feat(chart-c): full settings drawer replaces the popover

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Frontend — apply new settings in `CandleChart` + info-panel fixes

**Files:**
- Modify: `frontend/src/components/CandleChart.tsx` (extend `applyOptions`; series-recreate for `chartType`; `liveOverlay` guard; line/area hover)
- Modify: `frontend/src/components/ChartInfoPanel.tsx` (volume `!= null` fix; single-price hover for line/area; accept `chartType`)

**Interfaces:**
- Consumes: `ChartSettings` (colors, crosshair, priceScale, autoScale, lastPriceLine, liveOverlay, chartType) from Task 3.
- Produces: `CandleChart` honours all appearance settings live; `ChartInfoPanel` gains a `chartType` prop.

- [ ] **Step 1: Extend imports and helpers in `CandleChart.tsx`**

Change the lightweight-charts import to add the series types and mode enums:

```tsx
import {
  createChart, CandlestickSeries, BarSeries, LineSeries, AreaSeries,
  ColorType, CrosshairMode, PriceScaleMode, LineStyle,
  type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp, type SeriesType,
} from "lightweight-charts";
```

Add these pure mapping helpers above the component (after the `DARK`/`LIGHT` consts):

```tsx
const CROSSHAIR = {
  normal: CrosshairMode.Normal, magnet: CrosshairMode.Magnet, hidden: CrosshairMode.Hidden,
} as const;

// Candle/bar carry OHLC; line/area carry a single value (close).
function isOHLC(t: ChartSettings["chartType"]): boolean {
  return t === "candle" || t === "bar";
}
function seriesData(candles: Candle[], t: ChartSettings["chartType"]) {
  return candles.map((c) =>
    isOHLC(t)
      ? { time: toSeconds(c.time_msc) as UTCTimestamp, open: c.o, high: c.h, low: c.l, close: c.c }
      : { time: toSeconds(c.time_msc) as UTCTimestamp, value: c.c },
  );
}
function addSeriesFor(chart: IChartApi, s: ChartSettings): ISeriesApi<SeriesType> {
  const { up, down, wick } = s.colors;
  switch (s.chartType) {
    case "bar":
      return chart.addSeries(BarSeries, { upColor: up, downColor: down });
    case "line":
      return chart.addSeries(LineSeries, { color: up, lineWidth: 2 });
    case "area":
      return chart.addSeries(AreaSeries, { lineColor: up, topColor: up, bottomColor: "transparent" });
    case "candle":
    default:
      return chart.addSeries(CandlestickSeries, {
        upColor: up, downColor: down, wickUpColor: wick, wickDownColor: wick,
        borderVisible: false,
      });
  }
}
```

- [ ] **Step 2: Use the settings in chart creation**

In the create-once `useEffect`, replace the crosshair/priceScale config and the series creation. The `createChart` options become:

```tsx
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: props.settings.grid },
        horzLines: { color: theme.grid, visible: props.settings.grid },
      },
      crosshair: { mode: CROSSHAIR[props.settings.crosshair] },
      rightPriceScale: {
        borderColor: theme.border,
        mode: props.settings.priceScale === "log" ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
        autoScale: props.settings.autoScale,
      },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (t: number) => wib((t as number) * 1000, 0).replace(" WIB", ""),
      },
      localization: { timeFormatter: (t: number) => wib((t as number) * 1000, 0) },
    });
    const s = addSeriesFor(c, props.settings);
    s.applyOptions({ priceLineVisible: props.settings.lastPriceLine });
```

Change the `series` ref type to `ISeriesApi<SeriesType>`:

```tsx
  const series = useRef<ISeriesApi<SeriesType> | null>(null);
```

In the crosshair-move subscription, make the hover bar work for both OHLC and single-value series:

```tsx
    c.subscribeCrosshairMove((param) => {
      const d = param.seriesData.get(s) as
        | { open: number; high: number; low: number; close: number }
        | { value: number } | undefined;
      if (!d || param.time === undefined) { cbs.current.onHover(null); return; }
      const single = "value" in d;
      const close = single ? d.value : d.close;
      cbs.current.onHover({
        time_msc: (param.time as number) * 1000,
        o: single ? close : d.open,
        h: single ? close : d.high,
        l: single ? close : d.low,
        c: close, v: 0,
      });
    });
```

- [ ] **Step 3: Extend the settings `applyOptions` effect**

Replace the "Re-apply theme/grid" effect body with one that covers every live-applied setting:

```tsx
  // Re-apply live-appliable settings when they change (no full re-create; chart
  // type is handled by its own recreate effect below).
  useEffect(() => {
    if (!chart.current || !series.current) return;
    const s = props.settings;
    const theme = s.theme === "light" ? LIGHT : DARK;
    chart.current.applyOptions({
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: s.grid },
        horzLines: { color: theme.grid, visible: s.grid },
      },
      crosshair: { mode: CROSSHAIR[s.crosshair] },
      rightPriceScale: {
        borderColor: theme.border,
        mode: s.priceScale === "log" ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
        autoScale: s.autoScale,
      },
    });
    // Colour options depend on series type; candle/bar use up/down, line/area a single colour.
    if (s.chartType === "candle") {
      series.current.applyOptions({
        upColor: s.colors.up, downColor: s.colors.down,
        wickUpColor: s.colors.wick, wickDownColor: s.colors.wick,
      });
    } else if (s.chartType === "bar") {
      series.current.applyOptions({ upColor: s.colors.up, downColor: s.colors.down });
    } else if (s.chartType === "line") {
      series.current.applyOptions({ color: s.colors.up });
    } else {
      series.current.applyOptions({ lineColor: s.colors.up, topColor: s.colors.up });
    }
    series.current.applyOptions({ priceLineVisible: s.lastPriceLine });
  }, [props.settings]);
```

- [ ] **Step 4: Add the chart-type recreate effect**

Add a new effect (after the settings effect, before the data effect). It recreates the series only, re-sets data, and clears price lines so the overlay effect redraws them:

```tsx
  // Chart type change: recreate the SERIES only (not the whole chart, so pan/
  // zoom and theme survive), re-set data, and drop price lines (the overlay
  // effect below redraws them — it depends on chartType).
  useEffect(() => {
    const c = chart.current;
    if (!c || !series.current) return;
    for (const pl of priceLines.current) series.current.removePriceLine(pl);
    priceLines.current = [];
    c.removeSeries(series.current);
    const s = addSeriesFor(c, props.settings);
    s.applyOptions({ priceLineVisible: props.settings.lastPriceLine });
    s.setData(seriesData(cbs.current.candles, props.settings.chartType));
    series.current = s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.settings.chartType]);
```

- [ ] **Step 5: Use `seriesData` in the data push effect and add `liveOverlay` guard**

Replace the data push effect body:

```tsx
  useEffect(() => {
    if (!series.current) return;
    series.current.setData(seriesData(props.candles, props.settings.chartType));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.candles]);
```

In the live-overlay effect, add the master toggle to the early-return and to the deps:

```tsx
    if (!props.settings.liveOverlay || !props.nowVisible || !props.live || props.live.live.empty) return;
```

and change that effect's dependency array to:

```tsx
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay]);
```

Also swap the hard-coded dashed style constant to the enum for clarity (optional but tidy): `lineStyle: LineStyle.Dashed` in the `createPriceLine` call (replaces `lineStyle: 2`).

- [ ] **Step 6: Info-panel — volume fix + single-price hover**

In `frontend/src/components/ChartInfoPanel.tsx`, add `chartType` to the props:

```tsx
import type { ChartSettings } from "../lib/chartPrefs";
```

```tsx
export default function ChartInfoPanel({
  symbol, tf, candles, hovered, live, currency, chartType,
}: {
  symbol: Sym;
  tf: Timeframe;
  candles: Candle[];
  hovered: HoverBar | null;
  live: LiveData | null;
  currency: string;
  chartType: ChartSettings["chartType"];
}) {
```

Replace the OHLC block (the `<Row k="O" ... />` through the volume row) with a type-aware version — line/area show a single **Harga** row; the volume row uses `!= null` (Rule 4):

```tsx
            <div className="text-[11px] text-muted mb-1">{wib(bar.time_msc, 0)}</div>
            {chartType === "line" || chartType === "area" ? (
              <Row k="Harga" v={price(bar.c)} />
            ) : (
              <>
                <Row k="O" v={price(bar.o)} />
                <Row k="H" v={price(bar.h)} />
                <Row k="L" v={price(bar.l)} />
                <Row k="C" v={price(bar.c)} />
                {bar.v != null ? <Row k="V" v={String(bar.v)} /> : null}
              </>
            )}
```

- [ ] **Step 7: Commit (build is verified end-to-end in Task 9)**

```bash
git add frontend/src/components/CandleChart.tsx frontend/src/components/ChartInfoPanel.tsx
git commit -m "feat(chart-c): apply colors/scale/crosshair/type/overlay live in CandleChart

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Frontend — wire `Chart.tsx` to `useChartPrefs`; end-to-end build green

**Files:**
- Modify: `frontend/src/pages/Chart.tsx`

**Interfaces:**
- Consumes: `useChartPrefs` (Task 6), `parseSelection(params, defaults)` (Task 3), `useChartData(symbol, tf, initialBars, maxBars)` (Task 5), `ChartToolbar` `onReset` (Task 7), `ChartInfoPanel` `chartType` (Task 8).

- [ ] **Step 1: Rewrite `Chart.tsx` wiring**

In `frontend/src/pages/Chart.tsx`, replace the settings/selection wiring. New imports and top-of-component state:

```tsx
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { parseSelection } from "../lib/chartPrefs";
import { useChartPrefs } from "../hooks/useChartPrefs";
import type { Sym, Timeframe } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import ChartToolbar from "../components/ChartToolbar";
import CandleChart from "../components/CandleChart";
import ChartInfoPanel from "../components/ChartInfoPanel";
import { useChartData } from "../hooks/useChartData";

export interface ChartHandle { jumpToNow: () => void }

export default function Chart() {
  const [params, setParams] = useSearchParams();
  const { settings, update, reset } = useChartPrefs();
  const { symbol, tf } = parseSelection(params, {
    symbol: settings.defaultSymbol, tf: settings.defaultTimeframe,
  });
  const [hovered, setHovered] = useState<HoverBar | null>(null);
  const [nowVisible, setNowVisible] = useState(false);
  const chartRef = useRef<ChartHandle>(null);

  const { data: live } = useApi<LiveData>("/api/live", 2500);
  const currency = live?.header.currency ?? "USC";

  const data = useChartData(symbol, tf, settings.initialBars, settings.maxBars);
  const hasBars = data.candles.length > 0;

  const setSelection = (next: { symbol?: Sym; tf?: Timeframe }) => {
    const p = new URLSearchParams(params);
    p.set("symbol", next.symbol ?? symbol);
    p.set("tf", next.tf ?? tf);
    setParams(p, { replace: true });
  };
```

Then remove the old `settings`/`applySettings`/`loadChartSettings`/`saveChartSettings` lines. Update the `ChartToolbar` usage to pass `onSettings={update}` and `onReset={reset}`:

```tsx
      <ChartToolbar
        symbol={symbol}
        tf={tf}
        settings={settings}
        onSymbol={(s) => setSelection({ symbol: s })}
        onTf={(t) => setSelection({ tf: t })}
        onSettings={update}
        onReset={reset}
        onJumpNow={() => chartRef.current?.jumpToNow()}
      />
```

Pass `chartType` to the info panel:

```tsx
          <ChartInfoPanel
            symbol={symbol}
            tf={tf}
            candles={data.candles}
            hovered={hovered}
            live={live ?? null}
            currency={currency}
            chartType={settings.chartType}
          />
```

(The `CandleChart` usage already passes `settings={settings}` — unchanged.)

- [ ] **Step 2: Full frontend build + tests**

Run: `npm --prefix frontend run build`
Expected: **0 errors** (all call sites now consistent).

Run: `npm --prefix frontend test`
Expected: all vitest green (chartPrefs + candles suites include the new cases).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Chart.tsx
git commit -m "feat(chart-c): wire Chart page to useChartPrefs + persisted defaults

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Verification — full suites, rebuild, browser visual pass, graphify

**Files:** none (verification + a manual browser pass; the Phase B visual defer is discharged here).

- [ ] **Step 1: Backend suite (paste the output — Definition of Done)**

Run: `uv run pytest -q`
Expected: all green. Paste the summary line.

- [ ] **Step 2: Frontend suites**

Run: `npm --prefix frontend test`
Run: `npm --prefix frontend run build`
Expected: vitest green; build 0 errors.

- [ ] **Step 3: Rebuild invariant (proves prefs table is untouched by rebuild)**

Run: `uv run journal rebuild`
Expected: succeeds (no error; trades rebuilt). The `app_prefs` row, if any, is untouched.

- [ ] **Step 4: Browser visual pass (both themes)**

Start the dev server against the real DB and open `/chart`:

```bash
uv run journal serve --db data/journal.db &
# then in another shell for the SPA proxy, if iterating on TS:
# npm --prefix frontend run dev
```

Verify by eye, in **both dark and light**:
- Drawer opens from the right (gear), Esc/click-outside closes; all four sections render.
- Live preview: toggling grid, changing up/down/wick colors, crosshair mode, price-scale linear↔log, auto-scale, last-price line — each updates the chart immediately.
- Chart type candle→bar→line→area recreates the series without losing pan/zoom; info panel shows O/H/L/C for candle/bar and a single **Harga** for line/area.
- Data: lower `maxBars`, pan left — history loading stops at the bound (no runaway growth); raise it, pan further works.
- Behaviour: toggle live overlay off → SL/TP/entry lines disappear; on → they draw when "now" is in view and the symbol has an open position (uses the already-proven `/api/live` path — this is a render check).
- Persist: change settings, reload the page → settings survive (localStorage instant), and open a fresh browser/incognito → the DB value applies (cross-browser).
- Default symbol/tf: set them in the drawer, open `/chart` with no `?symbol=&tf=` → the saved defaults load; with an explicit URL param → the URL wins.

- [ ] **Step 5: Update graphify + final commit if anything changed**

Run: `graphify update .`
If the visual pass required any code fix, commit it:

```bash
git add -A
git commit -m "fix(chart-c): visual-pass adjustments

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the integrator

- **Merge order:** Phase C is branched from Phase B (`chart-phase-b-interactive-chart`). **Phase B (PR #8) must merge to `main` before Phase C.** Phase A (PR #7) is already on `main`.
- **Update project memory** (`chart-segment-phases`) after merge: Phase C done; Phase D (training/replay) is the only remaining chart-segment phase, and all four "debatable" items were pulled into C.
