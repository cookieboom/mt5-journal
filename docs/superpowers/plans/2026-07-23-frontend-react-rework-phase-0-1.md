# Frontend React SPA Rework — Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a Vite + React SPA served by the existing FastAPI process, with a design-C app shell and a fully data-wired Dashboard page reading a new JSON API — without touching any Python domain logic.

**Architecture:** FastAPI gains a thin `/api/*` JSON layer (`web/api.py`) that wraps the existing, tested `web/views.py` context builders and serialises them JSON-safe. React (built to static assets) is served by FastAPI at `/app` during the transition; the legacy Jinja UI stays live at its current routes. The client fetches JSON and formats values itself.

**Tech Stack:** Python 3.12 / FastAPI / uvicorn (unchanged runtime); Vite + React 18 + TypeScript + Tailwind CSS + Recharts + Vitest (new, build-time only). Node 24 (nvm) already installed.

## Global Constraints

- **Web layer never imports the MT5 adapter or MT5 constants** (CLAUDE.md rules 1, 12). `web/api.py` imports only from `web/views.py`, `web/format` helpers, and stdlib.
- **Loopback-only bind** stays as-is; nothing new listens on the network.
- **Money unit is always `accounts.currency` (USC).** JSON carries raw numbers + a `currency` field; never a pre-formatted `$`.
- **`NULL` = unknown, `0` = none set** (rule 4). Preserve as JSON `null` vs `0`; never coerce.
- **Sample-size honesty (docs §8/§9):** averaged stats already arrive as `None` when `n < 20`; serialise `None` → JSON `null`. The client greys/omits null stats — never renders a fabricated 0.
- **Timestamps are epoch-ms UTC** (rule 3). JSON carries `*_msc` integers; convert to WIB only at display time in the client.
- **Python runtime dependencies unchanged.** No new entry in the Python deps. Node deps live only under `frontend/`.
- **Definition of done** (CLAUDE.md): tests pass with pasted output, and `uv run journal rebuild` still succeeds.

---

### Task 1: Scaffold the `frontend/` toolchain

Stand up Vite + React + TS + Tailwind with the design-C token system, building to `frontend/dist`. Node availability is a precondition — verify it first.

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/index.html`
- Create: `frontend/postcss.config.js`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/.gitignore`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/src/App.tsx` (temporary smoke content, replaced in Task 2)

**Interfaces:**
- Produces: a working `npm --prefix frontend run build` that emits `frontend/dist/index.html` + `frontend/dist/assets/*`. Vite `base` is `/app/` so asset URLs resolve under FastAPI's `/app` mount. A dev server on `:5173` proxies `/api` → `:8000`.

- [ ] **Step 1: Verify Node & npm are on PATH**

Run: `node -v && npm -v`
Expected: Node prints `v24.x` (or ≥18), npm prints a version. If `node` is not found, stop and tell the user to run `nvm use 24` (or install Node) — do not proceed.

- [ ] **Step 2: Create `frontend/package.json`**

```json
{
  "name": "mt5-journal-frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.2",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.13",
    "typescript": "^5.5.4",
    "vite": "^5.4.6",
    "vitest": "^2.1.1"
  }
}
```

- [ ] **Step 3: Create `frontend/vite.config.ts`**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI under /app during the transition, so assets resolve there.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

- [ ] **Step 4: Create `frontend/tsconfig.json` and `frontend/tsconfig.node.json`**

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`frontend/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 5: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="id" class="dark">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>mt5-journal</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/app/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Create `frontend/postcss.config.js` and `frontend/tailwind.config.js`**

`frontend/postcss.config.js`:
```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

`frontend/tailwind.config.js` — the design-C tokens live here:
```js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0a1a",
        panel: "rgba(255,255,255,0.045)",
        "panel-border": "rgba(255,255,255,0.09)",
        ink: "#e8e6ff",
        muted: "#9a97c4",
        violet: "#a78bfa",
        cyan: "#22d3ee",
        pos: "#34d399",
        neg: "#fb7185",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["'SF Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
```

- [ ] **Step 7: Create `frontend/.gitignore`**

```gitignore
node_modules/
dist/
```

- [ ] **Step 8: Create `frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root { color-scheme: dark; }

body {
  margin: 0;
  background:
    radial-gradient(130% 120% at 100% -10%, #1a1740 0%, #0b0a1a 52%) fixed;
  color: theme(colors.ink);
  font-family: theme(fontFamily.sans);
  -webkit-font-smoothing: antialiased;
}

/* data numerals: solid, high-contrast, tabular (never gradient) */
.num { font-variant-numeric: tabular-nums; }

/* glass panel */
.glass {
  background: theme(colors.panel);
  border: 1px solid theme(colors.panel-border);
  border-radius: 14px;
  backdrop-filter: blur(8px);
}
```

- [ ] **Step 9: Create `frontend/src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 10: Create a temporary `frontend/src/App.tsx` (smoke content)**

```tsx
export default function App() {
  return <div className="p-8 text-ink">mt5-journal — scaffold OK</div>;
}
```

- [ ] **Step 11: Install dependencies**

Run: `npm --prefix frontend install`
Expected: completes without error; `frontend/node_modules/` exists.

- [ ] **Step 12: Build and verify `dist/` is produced**

Run: `npm --prefix frontend run build`
Expected: exits 0; `frontend/dist/index.html` and `frontend/dist/assets/*.js` exist.
Verify: `ls frontend/dist/index.html frontend/dist/assets`

- [ ] **Step 13: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/tsconfig.json frontend/tsconfig.node.json frontend/index.html \
  frontend/postcss.config.js frontend/tailwind.config.js frontend/.gitignore \
  frontend/src/main.tsx frontend/src/index.css frontend/src/App.tsx
git commit -m "feat(web): scaffold Vite+React+TS+Tailwind frontend (design-C tokens)"
```

---

### Task 2: App shell — sidebar, routing, placeholder pages

Replace the smoke `App.tsx` with the real shell: a left sidebar (design C), React Router routes for every page, and placeholder pages for everything except Dashboard (built in Phase 1). The account chip is static text for now; it becomes live in Task 4.

**Files:**
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/pages/Placeholder.tsx`
- Modify: `frontend/src/App.tsx` (full replace)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `AppShell` (wraps routed content with the sidebar); route table in `App.tsx` mapping `/`, `/live`, `/trades`, `/trades/:id`, `/report`, `/weekly`, `/commands`. Dashboard route renders `Placeholder` until Task 7 swaps it.

- [ ] **Step 1: Create `frontend/src/components/Sidebar.tsx`**

```tsx
import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/live", label: "Live" },
  { to: "/trades", label: "Trades" },
  { to: "/report", label: "Report" },
  { to: "/weekly", label: "Weekly" },
  { to: "/commands", label: "Commands" },
];

export default function Sidebar() {
  return (
    <aside className="w-[186px] shrink-0 border-r border-panel-border p-4 hidden md:flex md:flex-col gap-1">
      <div className="flex items-center gap-2 mb-5 font-bold text-[14px]">
        <span className="w-6 h-6 rounded-lg bg-gradient-to-br from-violet to-cyan" />
        mt5-journal
      </div>
      {LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) =>
            "px-3 py-2 rounded-lg text-[13px] transition " +
            (isActive
              ? "text-white bg-gradient-to-r from-violet/25 to-cyan/5 ring-1 ring-inset ring-violet/35"
              : "text-muted hover:text-ink")
          }
        >
          {l.label}
        </NavLink>
      ))}
    </aside>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/AppShell.tsx`**

```tsx
import { ReactNode } from "react";
import Sidebar from "./Sidebar";

export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen grid grid-cols-1 md:grid-cols-[186px_1fr]">
      <Sidebar />
      <main className="p-5 md:p-6 overflow-hidden">{children}</main>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/pages/Placeholder.tsx`**

```tsx
export default function Placeholder({ name }: { name: string }) {
  return (
    <div className="glass p-6">
      <h1 className="text-lg font-bold mb-1">{name}</h1>
      <p className="text-muted text-sm">Halaman ini dibangun di fase berikutnya.</p>
    </div>
  );
}
```

- [ ] **Step 4: Replace `frontend/src/App.tsx`**

```tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppShell from "./components/AppShell";
import Placeholder from "./pages/Placeholder";

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <AppShell>
        <Routes>
          <Route path="/" element={<Placeholder name="Dashboard" />} />
          <Route path="/live" element={<Placeholder name="Live" />} />
          <Route path="/trades" element={<Placeholder name="Trades" />} />
          <Route path="/trades/:id" element={<Placeholder name="Trade detail" />} />
          <Route path="/report" element={<Placeholder name="Report" />} />
          <Route path="/weekly" element={<Placeholder name="Weekly" />} />
          <Route path="/commands" element={<Placeholder name="Commands" />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
```

- [ ] **Step 5: Build to verify it compiles**

Run: `npm --prefix frontend run build`
Expected: exits 0 (TypeScript + Vite build clean).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/AppShell.tsx \
  frontend/src/components/Sidebar.tsx frontend/src/pages/Placeholder.tsx
git commit -m "feat(web): app shell — design-C sidebar + router + placeholders"
```

---

### Task 3: Python JSON serializer + `/api/account` payload (pure, tested)

Add `web/api.py` with a recursive `to_jsonable` (dataclass / `sqlite3.Row` / dict / list → JSON-safe) and `account_payload(conn)`. Tested directly against a seeded DB — no HTTP, no new dependency — matching `tests/test_web.py`.

**Files:**
- Create: `src/journal/web/api.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `web.views.account_header(conn) -> dict` (existing).
- Produces:
  - `to_jsonable(obj: Any) -> Any` — recursively converts dataclasses (via `dataclasses.fields`), `sqlite3.Row` (via `.keys()`), dict, list/tuple; passes through `None`/`bool`/`int`/`float`/`str`; raises `TypeError` on anything else.
  - `account_payload(conn) -> dict` — `{"login": int, "currency": str, "offset_s": int}`; raises `RuntimeError` on no-account / multi-account (propagated from `account_header`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`:
```python
"""The /api JSON layer (M-frontend). Tested like tests/test_web.py: pure
functions against a seeded DB, no HTTP/httpx. What must hold: the payload is
JSON-serialisable, money keeps its currency, NULL stays null (never 0), and the
§9 gate surfaces as JSON null."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from journal.store.db import connect
from journal.web import api

_LOGIN = 0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _seed_account(conn, currency="USC"):
    conn.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, ?, 1)",
        (_LOGIN, currency),
    )
    conn.commit()


def _ms(hour: int, day: int = 15) -> int:
    return int(datetime(2026, 1, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _seed_trade(conn, position_id, *, net_profit=0.0, r_multiple=None,
                close_time_msc=None):
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, volume, open_price, "
        "close_price, sl_initial, net_profit, r_multiple, magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, 'XAUUSDc', 'XAUUSD', 'buy', 'closed', ?, ?, 0.1, 4000.0, "
        "4001.0, NULL, ?, ?, NULL, 2, 1)",
        (_LOGIN, position_id, _ms(9), close_time_msc or _ms(10),
         net_profit, r_multiple),
    )
    conn.commit()


def test_to_jsonable_handles_dataclass_row_and_nesting(conn):
    _seed_account(conn)

    @dataclasses.dataclass
    class D:
        a: int
        b: float | None

    row = conn.execute("SELECT login, currency FROM accounts").fetchone()
    out = api.to_jsonable({"d": D(1, None), "rows": [row], "t": (1, 2)})
    assert out == {
        "d": {"a": 1, "b": None},
        "rows": [{"login": 0, "currency": "USC"}],
        "t": [1, 2],
    }
    json.dumps(out)  # must not raise


def test_to_jsonable_rejects_unknown_type():
    with pytest.raises(TypeError):
        api.to_jsonable(object())


def test_account_payload_shape(conn):
    _seed_account(conn)
    p = api.account_payload(conn)
    assert p == {"login": 0, "currency": "USC", "offset_s": 0}
    json.dumps(p)


def test_account_payload_raises_without_account(conn):
    with pytest.raises(RuntimeError):
        api.account_payload(conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError: module 'journal.web.api' has no attribute ...`.

- [ ] **Step 3: Write minimal implementation**

Create `src/journal/web/api.py`:
```python
"""The /api JSON layer over the M7 context builders.

A thin, testable seam: `to_jsonable` makes any builder's return value
JSON-safe, and each `*_payload` wraps exactly one `web/views.py` builder — no
business logic lives here. Never imports the MT5 adapter (CLAUDE.md rules 1, 12).
Money stays raw in `accounts.currency` (USC); the client formats. NULL stays
null (rule 4); the §9 gate arrives as null and is passed through untouched.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

from . import views


def to_jsonable(obj: Any) -> Any:
    """Recursively convert builder output to JSON-safe values.

    Handles dataclasses (field-by-field, so a Row nested in a dataclass is still
    converted), `sqlite3.Row`, dict, and list/tuple. Primitives pass through.
    Anything else raises `TypeError` rather than silently dropping data."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, sqlite3.Row):
        return {k: to_jsonable(obj[k]) for k in obj.keys()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def account_payload(conn: sqlite3.Connection) -> dict:
    """`{login, currency, offset_s}` — the header every page needs. Raises
    RuntimeError (no account / multi-account) up to the route."""
    return to_jsonable(views.account_header(conn))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/api.py tests/test_api.py
git commit -m "feat(web): /api serializer + account_payload (pure, no-HTTP tests)"
```

---

### Task 4: Wire `/api` routes + serve the SPA at `/app`

Mount the JSON routes and serve `frontend/dist` at `/app` from FastAPI. Legacy Jinja routes are untouched. Because the existing web suite avoids `httpx`, verification here is a manual curl against a running `journal serve` (documented), plus the Task-3 unit tests already covering the payload logic.

**Files:**
- Modify: `src/journal/web/app.py` (imports; add `/api/account` route; add SPA mount + catch-all)

**Interfaces:**
- Consumes: `api.account_payload`, `api.to_jsonable` (Task 3); `get_conn` dependency (existing, in `create_app`).
- Produces: `GET /api/account` → JSON; `GET /app` and `GET /app/{full_path}` → the SPA `index.html`; `/app/assets/*` static.

- [ ] **Step 1: Add imports and the frontend-dist path constant**

In `src/journal/web/app.py`, add to the imports near the top (after the existing `from . import views`):
```python
from fastapi.responses import JSONResponse
from . import api
```
And below `_CACHE_DIR = "cache"` add:
```python
# The built SPA (Vite → frontend/dist). Served at /app during the Jinja→React
# transition; absent until `npm --prefix frontend run build` has run.
_FRONTEND_DIST = _HERE.parent.parent.parent / "frontend" / "dist"
```

- [ ] **Step 2: Add the `/api/account` route**

Inside `create_app`, just after the `dashboard` route (before the `# ---- report` section), add:
```python
    # ------------------------------------------------------------------- api
    @app.get("/api/account")
    def api_account(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.account_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

- [ ] **Step 3: Serve the SPA at `/app` (guarded on the build existing)**

Immediately before `return app` at the end of `create_app`, add:
```python
    # --------------------------------------------------------------- SPA (/app)
    # React build served here during the transition; Jinja stays at its routes.
    if _FRONTEND_DIST.is_dir():
        app.mount(
            "/app/assets",
            StaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="spa-assets",
        )
        _index = (_FRONTEND_DIST / "index.html").read_text(encoding="utf-8")

        @app.get("/app", response_class=HTMLResponse)
        @app.get("/app/{full_path:path}", response_class=HTMLResponse)
        def spa(full_path: str = ""):
            # Any /app/* path returns index.html; React Router resolves the route.
            return HTMLResponse(_index)
```

- [ ] **Step 4: Run the full existing suite to prove no regression**

Run: `uv run pytest -q`
Expected: PASS — same count as before plus the 4 new `tests/test_api.py` tests; 0 failures. Paste the summary line.

- [ ] **Step 5: Manual end-to-end check (documented, run once)**

Ensure the build exists: `npm --prefix frontend run build`
Start the server: `uv run journal serve` (in a second terminal, or background).
Run:
```bash
curl -s http://localhost:8000/api/account
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/app
```
Expected: the first prints `{"login":0,"currency":"USC","offset_s":0}` (values per your DB); the second prints `200`. Opening `http://localhost:8000/app` in a browser shows the design-C shell.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/app.py
git commit -m "feat(web): serve React SPA at /app + /api/account route (Jinja untouched)"
```

---

### Task 5: TypeScript formatters (`format.ts`) with Vitest tests

Mirror `web/format.py` in the client: money-with-currency, R-multiple, percent, WIB time, and gated handling. Pure functions, unit-tested — this is where TDD lives on the frontend.

**Files:**
- Create: `frontend/src/lib/format.ts`
- Create: `frontend/src/lib/format.test.ts`

**Interfaces:**
- Produces:
  - `money(x: number | null, ccy: string, opts?: {sign?: boolean}): string` — `null` → `"n/a"`; always appends `ccy`; never a bare `$`.
  - `rmult(x: number | null): string` — `null` → `"n/a"`; else `"1.35R"`.
  - `pct(x: number | null): string` — rate in [0,1] → `"34.7%"`; `null` → `"n/a"`.
  - `wib(serverMsc: number | null, offsetS?: number): string` — epoch-ms server time → `"YYYY-MM-DD HH:MM WIB"`; `null` → `"—"`.
  - `isGated(n: number, avg: number | null): boolean` — `avg === null && n < 20`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/format.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { money, rmult, pct, wib, isGated } from "./format";

describe("format", () => {
  it("money carries currency, never a bare dollar, null is n/a not 0", () => {
    expect(money(1250, "USC")).toBe("1,250.00 USC");
    expect(money(-3.75, "USC", { sign: true })).toBe("-3.75 USC");
    expect(money(9.92, "USC")).not.toContain("$");
    expect(money(null, "USC")).toBe("n/a");
    expect(money(0, "USC")).toBe("0.00 USC");
  });

  it("rmult and pct", () => {
    expect(rmult(1.35)).toBe("1.35R");
    expect(rmult(null)).toBe("n/a");
    expect(pct(0.347)).toBe("34.7%");
    expect(pct(null)).toBe("n/a");
  });

  it("wib converts server ms to UTC+7 and shows — for null", () => {
    // 2026-01-15 03:00 UTC (offset 0) → 10:00 WIB
    const ms = Date.UTC(2026, 0, 15, 3, 0) ;
    expect(wib(ms, 0)).toBe("2026-01-15 10:00 WIB");
    expect(wib(null)).toBe("—");
  });

  it("isGated matches the §9 rule", () => {
    expect(isGated(5, null)).toBe(true);
    expect(isGated(25, null)).toBe(false);
    expect(isGated(5, 1.2)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test`
Expected: FAIL — cannot resolve `./format` / functions undefined.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/format.ts`:
```ts
// Client mirror of web/format.py. Money always carries its currency (USC);
// null is "n/a", never 0 (rule 4). *_msc are broker SERVER time; WIB = UTC+7 at
// display only (rule 3). R is unit-free.

export function money(
  x: number | null,
  ccy: string,
  opts: { sign?: boolean } = {},
): string {
  if (x === null || x === undefined) return "n/a";
  const s = x.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: opts.sign ? "always" : "auto",
  });
  return `${s} ${ccy}`.trim();
}

export function rmult(x: number | null): string {
  return x === null || x === undefined ? "n/a" : `${x.toFixed(2)}R`;
}

export function pct(x: number | null): string {
  return x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(1)}%`;
}

export function wib(serverMsc: number | null, offsetS = 0): string {
  if (serverMsc === null || serverMsc === undefined) return "—";
  // true UTC = server - offset; then shift +7h for WIB and read UTC fields.
  const wibMs = serverMsc - offsetS * 1000 + 7 * 3600 * 1000;
  const d = new Date(wibMs);
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())} WIB`
  );
}

export function isGated(n: number, avg: number | null): boolean {
  return (avg === null || avg === undefined) && n < 20;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test`
Expected: PASS (all format tests green).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/format.ts frontend/src/lib/format.test.ts
git commit -m "feat(web): client formatters mirroring web/format.py (Vitest)"
```

---

### Task 6: `dashboard_payload` + test

Add the JSON payload for the Dashboard, wrapping the existing `views.dashboard_context` and adding the header. Assert JSON-safety and that the §9 gate + currency survive.

**Files:**
- Modify: `src/journal/web/api.py` (add `dashboard_payload`)
- Modify: `tests/test_api.py` (add tests)

**Interfaces:**
- Consumes: `views.dashboard_context(conn) -> {"report", "live", "equity"}`; `views.account_header`.
- Produces: `dashboard_payload(conn) -> dict` with keys `header`, `report`, `live`, `equity` — all JSON-safe. `report.currency == "USC"`; `report.avg_r is None` when `n_with_r < 20`; `equity.n` = closed-trade count; `live.positions` a list of dicts.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:
```python
def test_dashboard_payload_is_jsonable_and_honest(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=120.0, r_multiple=1.5)
    _seed_trade(conn, 2, net_profit=-80.0, r_multiple=-1.0)

    p = api.dashboard_payload(conn)
    json.dumps(p)  # must not raise

    assert set(p.keys()) == {"header", "report", "live", "equity"}
    assert p["header"]["currency"] == "USC"
    assert p["report"]["n_closed"] == 2
    # §9 gate: with only 2 R-known trades, avg_r is withheld as null (never 0).
    assert p["report"]["avg_r"] is None
    assert p["equity"]["n"] == 2
    assert isinstance(p["live"]["positions"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_dashboard_payload_is_jsonable_and_honest -v`
Expected: FAIL — `AttributeError: module 'journal.web.api' has no attribute 'dashboard_payload'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/journal/web/api.py`:
```python
def dashboard_payload(conn: sqlite3.Connection) -> dict:
    """Header + the M5 report + live strip + equity/R tape — the Dashboard's
    single JSON read. Wraps `views.dashboard_context`; adds no logic. The §9
    gate and NULLs arrive as JSON null and pass through untouched."""
    ctx = views.dashboard_context(conn)
    return to_jsonable({
        "header": views.account_header(conn),
        "report": ctx["report"],
        "live": ctx["live"],
        "equity": ctx["equity"],
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (all tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/api.py tests/test_api.py
git commit -m "feat(web): dashboard_payload wrapping dashboard_context (JSON-safe)"
```

---

### Task 7: `/api/dashboard` route + the Dashboard page

Expose `dashboard_payload` over HTTP, then build the Dashboard page: types, a fetch hook, and the KPI row / equity chart / per-symbol bars / recent-trades components. Swap the Dashboard route from `Placeholder` to the real page.

**Files:**
- Modify: `src/journal/web/app.py` (add `/api/dashboard` route)
- Create: `frontend/src/lib/types.ts`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/KpiCard.tsx`
- Create: `frontend/src/components/EquityChart.tsx`
- Create: `frontend/src/components/SymbolBars.tsx`
- Create: `frontend/src/components/RecentTrades.tsx`
- Create: `frontend/src/pages/Dashboard.tsx`
- Modify: `frontend/src/App.tsx` (route `/` → `Dashboard`)

**Interfaces:**
- Consumes: `GET /api/dashboard` → the `dashboard_payload` shape; `format.ts` helpers.
- Produces: `useApi<T>(path)` hook (`{data, error, loading}`); typed `DashboardData`; a rendered Dashboard.

- [ ] **Step 1: Add the `/api/dashboard` route**

In `src/journal/web/app.py`, directly after the `api_account` route added in Task 4, add:
```python
    @app.get("/api/dashboard")
    def api_dashboard(conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.dashboard_payload(conn))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

- [ ] **Step 2: Run the suite to confirm no regression**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. Paste the summary line.

- [ ] **Step 3: Create `frontend/src/lib/types.ts`**

```ts
// Shapes mirror web/api.py payloads. Money numbers are raw USC; null = unknown.
export interface Header { login: number; currency: string; offset_s: number; }

export interface Report {
  currency: string;
  n_total: number; n_closed: number;
  n_wins: number; n_losses: number; n_breakeven: number;
  win_rate: number | null;
  expectancy: number | null;
  avg_r: number | null; n_with_r: number;
  by_symbol: { label: string; n: number; win_rate: number | null; avg_r: number | null }[];
}

export interface EquitySvg {
  empty: boolean; viewbox: string; points: string; area: string; baseline_y: number;
}
export interface Equity {
  n: number; n_with_r: number;
  equity_last: number | null; r_last: number | null;
  series: { close_time_msc: number; equity: number }[];
  equity_svg: EquitySvg; r_svg: EquitySvg;
}

export interface LivePosition {
  position_id: number; symbol: string; volume: number; profit: number | null;
}
export interface Live {
  positions: LivePosition[]; count: number;
  total_floating: number; total_volume: number;
  age_s: number | null; stale: boolean; empty: boolean;
}

export interface DashboardData {
  header: Header; report: Report; live: Live; equity: Equity;
}
```

- [ ] **Step 4: Create `frontend/src/lib/api.ts`**

```ts
import { useEffect, useState } from "react";

export function useApi<T>(path: string, intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
  }, [path, intervalMs]);

  return { data, error, loading };
}
```

- [ ] **Step 5: Create `frontend/src/components/KpiCard.tsx`**

```tsx
export default function KpiCard({
  label, value, sub, tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg";
}) {
  const color = tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-ink";
  return (
    <div className="glass p-3.5">
      <div className="text-[9.5px] tracking-[0.09em] uppercase text-muted">{label}</div>
      <div className={`num text-[23px] font-bold tracking-tight mt-1 ${color}`}>{value}</div>
      {sub && <div className="text-[10.5px] text-muted mt-0.5">{sub}</div>}
    </div>
  );
}
```

- [ ] **Step 6: Create `frontend/src/components/EquityChart.tsx`**

Reuse the server-computed SVG geometry from `equity.r_svg` (already unit-tested in `views.equity_curve`) so client and CLI never disagree on the curve.
```tsx
import { EquitySvg } from "../lib/types";

export default function EquityChart({ svg, label }: { svg: EquitySvg; label: string }) {
  if (svg.empty) {
    return <div className="text-muted text-sm py-10 text-center">Belum ada data {label}.</div>;
  }
  return (
    <svg viewBox={svg.viewbox} preserveAspectRatio="none" className="w-full h-[150px]">
      <defs>
        <linearGradient id="eqArea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="eqLine" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#a78bfa" />
          <stop offset="100%" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <polygon points={svg.area} fill="url(#eqArea)" />
      <polyline points={svg.points} fill="none" stroke="url(#eqLine)" strokeWidth="2.5" />
      <line x1="0" y1={svg.baseline_y} x2="720" y2={svg.baseline_y}
            stroke="rgba(255,255,255,0.18)" strokeWidth="1" strokeDasharray="4 4" />
    </svg>
  );
}
```

- [ ] **Step 7: Create `frontend/src/components/SymbolBars.tsx`**

```tsx
import { Report } from "../lib/types";
import { pct, rmult, isGated } from "../lib/format";

export default function SymbolBars({ report }: { report: Report }) {
  const rows = report.by_symbol;
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.avg_r ?? 0) * r.n));
  return (
    <div className="flex flex-col gap-3 mt-1">
      {rows.map((r) => {
        const gated = isGated(r.n, r.avg_r);
        const w = gated ? 0 : Math.min(100, (Math.abs((r.avg_r ?? 0) * r.n) / max) * 100);
        return (
          <div key={r.label}>
            <div className="flex justify-between text-[11.5px] mb-1.5">
              <b className="text-white font-semibold">{r.label}</b>
              <span className={gated ? "text-muted/60" : "text-muted"}>
                {gated ? `n=${r.n} (perlu ≥20)` : `${rmult(r.avg_r)} · ${pct(r.win_rate)}`}
              </span>
            </div>
            <div className="h-2 rounded bg-white/[0.06] overflow-hidden">
              <div className="h-full rounded bg-gradient-to-r from-violet to-cyan"
                   style={{ width: `${w}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 8: Create `frontend/src/components/RecentTrades.tsx`**

```tsx
import { Equity } from "../lib/types";
import { wib, money } from "../lib/format";

// The dashboard's recent strip reads the equity series (closed trades, ordered).
export default function RecentTrades({
  equity, currency, offsetS,
}: { equity: Equity; currency: string; offsetS: number }) {
  const rows = [...equity.series].slice(-5).reverse();
  if (rows.length === 0)
    return <div className="text-muted text-sm py-6">Belum ada trade tertutup.</div>;
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead>
        <tr className="text-muted text-left">
          <th className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider">Tutup</th>
          <th className="pb-2 font-semibold uppercase text-[9.5px] tracking-wider num text-right">Equity</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.close_time_msc} className="border-t border-white/5">
            <td className="py-2 num">{wib(r.close_time_msc, offsetS)}</td>
            <td className="py-2 num text-right">{money(r.equity, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 9: Create `frontend/src/pages/Dashboard.tsx`**

```tsx
import { useApi } from "../lib/api";
import { DashboardData } from "../lib/types";
import { money, pct, rmult } from "../lib/format";
import KpiCard from "../components/KpiCard";
import EquityChart from "../components/EquityChart";
import SymbolBars from "../components/SymbolBars";
import RecentTrades from "../components/RecentTrades";

export default function Dashboard() {
  const { data, error, loading } = useApi<DashboardData>("/api/dashboard", 5000);

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;

  const { header, report, live, equity } = data;
  const ccy = header.currency;
  const floatTone = live.total_floating >= 0 ? "pos" : "neg";

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-[18px] font-bold tracking-tight">Dashboard</h1>
          <div className="text-[12px] text-muted mt-0.5">{report.n_closed} trade tertutup</div>
        </div>
        <div className="text-[11px] text-cyan flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-cyan/10 ring-1 ring-cyan/25">
          <span className="w-1.5 h-1.5 rounded-full bg-cyan shadow-[0_0_8px_#22d3ee]" />
          {live.empty ? "live idle" : live.stale ? "stale" : `live · ${live.age_s}s`}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <KpiCard label="Net R" value={rmult(equity.r_last)} sub={`n=${equity.n_with_r}`}
                 tone={(equity.r_last ?? 0) >= 0 ? "pos" : "neg"} />
        <KpiCard label="Win rate" value={pct(report.win_rate)}
                 sub={`${report.n_wins}W · ${report.n_losses}L · ${report.n_breakeven}BE`} />
        <KpiCard label="Expectancy" value={money(report.expectancy, ccy)} sub="per trade" />
        <KpiCard label="Floating P&L" value={money(live.total_floating, ccy, { sign: true })}
                 sub={`${live.count} posisi open`} tone={floatTone} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr] gap-3.5 mb-3.5">
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold">Kurva R kumulatif</h2>
          <div className="text-[11px] text-muted mb-3">pertumbuhan R dari waktu ke waktu</div>
          <EquityChart svg={equity.r_svg} label="R" />
        </div>
        <div className="glass p-4">
          <h2 className="text-[13px] font-semibold">Per simbol</h2>
          <div className="text-[11px] text-muted mb-3">rata-rata R · win rate</div>
          <SymbolBars report={report} />
        </div>
      </div>

      <div className="glass p-4">
        <h2 className="text-[13px] font-semibold mb-3">Trade terakhir</h2>
        <RecentTrades equity={equity} currency={ccy} offsetS={header.offset_s} />
      </div>
    </div>
  );
}
```

- [ ] **Step 10: Route `/` to the Dashboard**

In `frontend/src/App.tsx`, add the import and swap the `/` route:
```tsx
import Dashboard from "./pages/Dashboard";
```
Change:
```tsx
<Route path="/" element={<Placeholder name="Dashboard" />} />
```
to:
```tsx
<Route path="/" element={<Dashboard />} />
```

- [ ] **Step 11: Build + run the format tests**

Run: `npm --prefix frontend run build && npm --prefix frontend run test`
Expected: build exits 0; Vitest all green.

- [ ] **Step 12: Manual end-to-end verification**

Rebuild the SPA and start the server:
```bash
npm --prefix frontend run build
uv run journal serve
```
Open `http://localhost:8000/app` in a browser. Confirm: the Dashboard renders the KPI row, the R curve, per-symbol bars, and the recent-trades table with real values from your DB; numbers are legible (solid, not gradient); a bucket with `n<20` shows its `n`, not a fabricated figure.

- [ ] **Step 13: Commit**

```bash
git add src/journal/web/app.py frontend/src/lib/types.ts frontend/src/lib/api.ts \
  frontend/src/components/KpiCard.tsx frontend/src/components/EquityChart.tsx \
  frontend/src/components/SymbolBars.tsx frontend/src/components/RecentTrades.tsx \
  frontend/src/pages/Dashboard.tsx frontend/src/App.tsx
git commit -m "feat(web): /api/dashboard + Dashboard page (KPIs, R curve, per-symbol, recent)"
```

---

## Remaining phases (planned at each review gate)

Per the approved incremental delivery (spec §6), these get their own bite-sized
plans when reached — their API shapes and components depend on the outcomes of
the phase before:

- **Phase 2 — Live:** `/api/live` (polling), open-position cards, floating P&L,
  staleness badge, and the **two-step confirm** modal (`preview` → `enqueue`),
  preserving the server-side validation and 1.00-lot cap exactly. Highest stakes;
  verified end-to-end before proceeding.
- **Phase 3 — Trades + Trade detail:** filterable list, detail with the chart
  PNG (reused), and the annotate/tag write forms.
- **Phase 4 — Report + Weekly + Commands:** analytics tables as charts, plus the
  daily P&L calendar, R histogram, and MAE/MFE scatter (existing data).
- **Phase 5 — Cutover:** serve the SPA at `/`, remove the Jinja templates and
  their plumbing, update `CLAUDE.md` and docs.

---

## Self-Review

- **Spec coverage (Phases 0–1):** Architecture / one-process serving (Tasks 1–4)
  ✓; JSON contract raw-numbers + `gated` null + NULL-vs-0 (Tasks 3, 6 + tests) ✓;
  visual system direction C (Tasks 1, 2, 7 tokens/components) ✓; Dashboard page &
  its data (Tasks 6, 7) ✓; polling groundwork (`useApi` interval, Task 7) ✓;
  dependencies confined to `frontend/` (Task 1) ✓; testing without new Python dep
  (pure `*_payload` tests, Vitest) ✓. Live/two-step, Trades, Report/Weekly,
  cutover are explicitly deferred to their phases.
- **Placeholder scan:** no TBD/TODO; every code step carries complete code.
- **Type consistency:** `to_jsonable`/`account_payload`/`dashboard_payload`
  (Python) and `DashboardData`/`Header`/`Report`/`Equity`/`Live` +
  `money`/`rmult`/`pct`/`wib`/`isGated` (TS) are used with the same names/shapes
  across Tasks 3–7. `EquitySvg` fields (`empty/viewbox/points/area/baseline_y`)
  match `views._svg_geometry`.
