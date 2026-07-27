# Spec C — Realtime Monitor + Data Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TradingView-style live-updating forming bar to the normal chart, a liveness indicator, and data-completeness visibility (classify/see/backfill unfetched holes) — all without letting the web touch the bridge.

**Architecture:** `journal live` (the only bridge-toucher) writes a heartbeat every cycle and, for each demand-driven watch, fetches the last few bars — the forming bar goes to an overwrite-friendly `live_candles` table, closed bars are promoted into the append-only `candles` table. The web reads DB only: it upserts watches, polls the forming bar + liveness, and classifies coverage gaps for display. Backfill reuses the existing `candle_requests` queue.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), FastAPI, pytest; React SPA (TypeScript, Vite, vitest, testing-library).

## Global Constraints

- **Never `import MetaTrader5` outside `adapter/`; web/ never imports `candle_fill.py`.** (M9 boundary — Rules 1, 12.)
- **All timestamps are epoch-milliseconds, integer, UTC.** WIB is display-only. `time_msc` >= `10**12` or it is seconds leaking through (Trap 15) — never `×1000` outside the adapter boundary.
- **`candles` stays append-only closed bars, fully rebuildable.** The forming bar lives only in `live_candles`. The live tables are cache/ephemeral; `journal rebuild` must still succeed and never reads/writes them. (Rules 2, 6.)
- **Money/prices are `REAL`; compare with tolerance `abs(a-b) < 1e-9`, never `==`.** (Rule 5.)
- **`NULL` = unknown, `0` = none set.** (Rule 4.)
- **Symbols stored twice**: query MT5 with `symbol` (`XAUUSDc`); group by `symbol_base`. (Rule 11.)
- **Tests before implementation** for `domain/`/store logic; use fixtures, no live MT5. (Rule 7.)
- **Definition of done**: `uv run pytest` green (paste output), `npm run test`/`tsc`/`npm run build` green, `uv run journal rebuild` succeeds, `graphify update .` run. (docs/CLAUDE.md.)
- Migration files are auto-discovered by glob `[0-9]*_*.sql` and applied in order; the migration DDL must be **byte-identical in intent with `schema.sql`** (`tests/test_migrations.py::test_migrated_db_matches_a_fresh_db`).

---

# PHASE 1 — Heartbeat & liveness

## Task 1: Migration 007 (live tables) + schema.sql + version bump

Creates all three live tables in one additive migration (they are one schema unit; later phases fill them). Nothing reads them yet.

**Files:**
- Create: `src/journal/store/migrations/007_live_monitor.sql`
- Modify: `src/journal/store/schema.sql` (append live-monitor section)
- Modify: `src/journal/store/db.py:20` (`SCHEMA_VERSION = 6` → `7`)
- Test: `tests/test_migrations.py` (existing `test_migrated_db_matches_a_fresh_db` must still pass)

**Interfaces:**
- Produces: tables `live_heartbeat(id, beat_msc)`, `live_watches(symbol, timeframe, expires_msc, requested_msc)`, `live_candles(symbol, timeframe, time_msc, open, high, low, close, tick_volume, spread, real_volume, updated_msc)`.

- [ ] **Step 1: Write the migration file** `007_live_monitor.sql`

```sql
-- Migration 007 — live monitor tables (Spec C).
--
-- Brings a v6 database forward to v7. ADDITIVE only: three new tables, no
-- existing table touched. The same DDL lives in schema.sql for fresh databases;
-- the two must stay byte-identical (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- All three are CACHE / EPHEMERAL: `journal rebuild` never touches them and the
-- app is correct if they are empty (the forming bar is re-fetched next cycle).
-- All *_msc are epoch ms. beat_msc/updated_msc/expires_msc/requested_msc are true
-- UTC; live_candles.time_msc is broker server time (bar open), like `candles`.

-- Single-row liveness beacon. `journal live` overwrites beat_msc every cycle.
CREATE TABLE IF NOT EXISTS live_heartbeat (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    beat_msc INTEGER NOT NULL
);

-- Demand-driven watch registry. Web upserts (with a TTL); `journal live` reads
-- the still-active rows each cycle and fetches their forming bar.
CREATE TABLE IF NOT EXISTS live_watches (
    symbol        TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL,
    expires_msc   INTEGER NOT NULL,       -- active while expires_msc > now
    requested_msc INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

-- At most one FORMING bar per (symbol, timeframe). Overwritten freely — NOT part
-- of the candles append-only contract. Column types mirror `candles` exactly.
CREATE TABLE IF NOT EXISTS live_candles (
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    time_msc    INTEGER NOT NULL,         -- bar OPEN time (bucket start), server time
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    tick_volume INTEGER,
    spread      INTEGER,
    real_volume INTEGER,
    updated_msc INTEGER NOT NULL,         -- true UTC of last overwrite
    PRIMARY KEY (symbol, timeframe)
) WITHOUT ROWID;
```

- [ ] **Step 2: Append the identical DDL to `schema.sql`**

Add a `-- ---- live monitor (Spec C)` section at the end of `schema.sql` containing the three `CREATE TABLE` statements above verbatim (drop the migration-only comment header; keep the same column definitions).

- [ ] **Step 3: Bump the version** in `src/journal/store/db.py:20`

```python
SCHEMA_VERSION = 7
```

- [ ] **Step 4: Run the migration parity + fresh-DB tests**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS (fresh schema at v7 equals a v6 DB migrated through 007).

- [ ] **Step 5: Verify rebuild still works on a fresh DB**

Run: `uv run pytest -k "migrat or schema" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/store/migrations/007_live_monitor.sql src/journal/store/schema.sql src/journal/store/db.py
git commit -m "feat(store): migration 007 — live_heartbeat/live_watches/live_candles"
```

## Task 2: `live_store` heartbeat functions

**Files:**
- Create: `src/journal/store/live_store.py`
- Test: `tests/test_live_store.py`

**Interfaces:**
- Produces: `beat(conn, now_msc: int) -> None`; `read_heartbeat(conn) -> int | None`.

- [ ] **Step 1: Write the failing test** in `tests/test_live_store.py`

```python
import sqlite3
import pytest
from journal.store.db import connect
from journal.store import live_store as ls


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def test_read_heartbeat_none_when_never_beaten(conn):
    assert ls.read_heartbeat(conn) is None


def test_beat_then_read(conn):
    ls.beat(conn, 1_700_000_000_000)
    assert ls.read_heartbeat(conn) == 1_700_000_000_000


def test_beat_overwrites_single_row(conn):
    ls.beat(conn, 1_700_000_000_000)
    ls.beat(conn, 1_700_000_005_000)
    assert ls.read_heartbeat(conn) == 1_700_000_005_000
    assert conn.execute("SELECT COUNT(*) c FROM live_heartbeat").fetchone()["c"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_live_store.py -v`
Expected: FAIL (`ModuleNotFoundError: journal.store.live_store`).

- [ ] **Step 3: Implement `live_store.py` (heartbeat part)**

```python
"""Pure-DB live-monitor store: heartbeat, watch registry, and the single forming
bar per (symbol, timeframe). NO bridge, NO MT5 — safe to import from web/. The
bridge-touching fetch lives in ingest/live.py, exactly like candles_store vs
candle_fill.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import Candle

_MSC_FLOOR = 10**12  # below this, time_msc is seconds leaking through (Trap 15)


def beat(conn: sqlite3.Connection, now_msc: int) -> None:
    """Overwrite the single heartbeat row. Caller need not commit — we do."""
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET beat_msc = excluded.beat_msc",
        (now_msc,),
    )
    conn.commit()


def read_heartbeat(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT beat_msc FROM live_heartbeat WHERE id = 1").fetchone()
    return None if row is None else int(row["beat_msc"])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_live_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/live_store.py tests/test_live_store.py
git commit -m "feat(store): live_store heartbeat beat/read"
```

## Task 3: `journal live` writes the heartbeat every cycle

**Files:**
- Modify: `src/journal/ingest/live.py` (`live_cycle`, near the end; import `live_store`)
- Test: `tests/test_live.py` (add a test)

**Interfaces:**
- Consumes: `live_store.beat`, `live_store.read_heartbeat`, `store.db.now_ms`.
- Produces: every `live_cycle` call writes `live_heartbeat.beat_msc = now_ms()`.

- [ ] **Step 1: Write the failing test** in `tests/test_live.py`

```python
def test_live_cycle_writes_heartbeat(conn):
    from journal.store import live_store as ls
    client = FakeLiveClient([[]])          # no positions is fine
    assert ls.read_heartbeat(conn) is None
    live_cycle(client, conn, _LOGIN)
    beat = ls.read_heartbeat(conn)
    assert beat is not None and beat >= _MSC_FLOOR  # real ms, always written
```

Add at the top of `tests/test_live.py` if absent: `_MSC_FLOOR = 10**12`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_live.py::test_live_cycle_writes_heartbeat -v`
Expected: FAIL (heartbeat is `None`).

- [ ] **Step 3: Implement** — in `ingest/live.py` add `from ..store import live_store` with the other store imports, then at the END of `live_cycle` (just before building `LiveReport`) add:

```python
    # (5) liveness beacon — ALWAYS, even with no positions/watches, so the web can
    # tell "journal live is running" from "data is just old". Empty open_positions
    # cannot serve as a heartbeat (no rows when nothing is open).
    live_store.beat(conn, now_ms())
```

(`now_ms` is already imported in this module for `observed_msc`; reuse it.)

- [ ] **Step 4: Run to verify pass + no regression**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS (new test + all existing live tests).

- [ ] **Step 5: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "feat(live): write live_heartbeat every cycle"
```

## Task 4: `GET /api/live-status` endpoint

**Files:**
- Modify: `src/journal/web/api.py` (add `live_status_payload`)
- Modify: `src/journal/web/app.py` (add route)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `live_store.read_heartbeat`, `store.db.now_ms`.
- Produces: `api.live_status_payload(conn, *, stale_ms=15_000, now_msc=None) -> {"live": bool, "beat_msc": int|None, "age_ms": int|None}`.

- [ ] **Step 1: Write the failing test** in `tests/test_api.py`

```python
def test_live_status_offline_when_no_heartbeat(conn):
    from journal.web import api
    p = api.live_status_payload(conn, now_msc=1_700_000_100_000)
    assert p == {"live": False, "beat_msc": None, "age_ms": None}


def test_live_status_live_when_recent(conn):
    from journal.web import api
    from journal.store import live_store as ls
    ls.beat(conn, 1_700_000_100_000)
    p = api.live_status_payload(conn, stale_ms=15_000, now_msc=1_700_000_105_000)
    assert p["live"] is True and p["age_ms"] == 5_000


def test_live_status_stale_when_old(conn):
    from journal.web import api
    from journal.store import live_store as ls
    ls.beat(conn, 1_700_000_100_000)
    p = api.live_status_payload(conn, stale_ms=15_000, now_msc=1_700_000_200_000)
    assert p["live"] is False and p["age_ms"] == 100_000
```

(Reuse `tests/test_api.py`'s existing `conn` fixture; if a test there needs seeded symbol_specs, follow the existing fixture — heartbeat needs none.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k live_status -v`
Expected: FAIL (`AttributeError: live_status_payload`).

- [ ] **Step 3: Implement** in `web/api.py`

```python
def live_status_payload(
    conn: sqlite3.Connection, *, stale_ms: int = 15_000, now_msc: int | None = None
) -> dict:
    """Is `journal live` running? `live` is True when the last heartbeat is newer
    than `stale_ms`. `now_msc` is injectable for tests; None = real clock."""
    from ..store import live_store
    from ..store.db import now_ms

    now = now_ms() if now_msc is None else now_msc
    beat = live_store.read_heartbeat(conn)
    if beat is None:
        return {"live": False, "beat_msc": None, "age_ms": None}
    age = now - beat
    return {"live": age < stale_ms, "beat_msc": beat, "age_ms": age}
```

- [ ] **Step 4: Add the route** in `web/app.py` (next to `api_live`)

```python
    @app.get("/api/live-status")
    def api_live_status(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.live_status_payload(conn))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api.py -k live_status -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py
git commit -m "feat(api): GET /api/live-status"
```

## Task 5: FE liveness indicator on the chart

**Files:**
- Create: `frontend/src/hooks/useLiveStatus.ts`
- Create: `frontend/src/components/LiveDot.tsx`
- Create: `frontend/src/components/LiveDot.test.tsx`
- Modify: `frontend/src/lib/types.ts` (add `LiveStatus` type)
- Modify: `frontend/src/pages/Chart.tsx` (render `<LiveDot>` in the toolbar area)

**Interfaces:**
- Consumes: `useApi` from `lib/api.ts`.
- Produces: `useLiveStatus(pollMs=5000) -> { status: LiveStatus | null }`; `LiveStatus = { live: boolean; beat_msc: number | null; age_ms: number | null }`.

- [ ] **Step 1: Add the type** to `frontend/src/lib/types.ts`

```ts
export type LiveStatus = { live: boolean; beat_msc: number | null; age_ms: number | null };
```

- [ ] **Step 2: Write the failing component test** in `frontend/src/components/LiveDot.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import LiveDot from "./LiveDot";

describe("LiveDot", () => {
  it("shows LIVE when live", () => {
    render(<LiveDot status={{ live: true, beat_msc: 1, age_ms: 3000 }} />);
    expect(screen.getByText(/live/i)).toBeInTheDocument();
  });
  it("shows an offline hint with the journal live command when offline", () => {
    render(<LiveDot status={{ live: false, beat_msc: null, age_ms: null }} />);
    expect(screen.getByText(/journal live/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/LiveDot.test.tsx`
Expected: FAIL (cannot resolve `./LiveDot`).

- [ ] **Step 4: Implement the hook** `frontend/src/hooks/useLiveStatus.ts`

```ts
import { useApi } from "../lib/api";
import type { LiveStatus } from "../lib/types";

export function useLiveStatus(pollMs = 5000) {
  const { data } = useApi<LiveStatus>("/api/live-status", pollMs);
  return { status: data };
}
```

- [ ] **Step 5: Implement the component** `frontend/src/components/LiveDot.tsx`

```tsx
import type { LiveStatus } from "../lib/types";

export default function LiveDot({ status }: { status: LiveStatus | null }) {
  const live = !!status?.live;
  const ageS = status?.age_ms != null ? Math.round(status.age_ms / 1000) : null;
  if (!live) {
    return (
      <span className="text-[11px] text-neg bg-neg/10 ring-1 ring-neg/25 px-2.5 py-1 rounded-full flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-neg" />
        tak live — jalankan <code>journal live</code>
      </span>
    );
  }
  return (
    <span className="text-[11px] text-cyan bg-cyan/10 ring-1 ring-cyan/25 px-2.5 py-1 rounded-full flex items-center gap-1.5">
      <span className="w-1.5 h-1.5 rounded-full bg-cyan shadow-[0_0_8px_#22d3ee]" />
      live{ageS != null ? ` · ${ageS}s` : ""}
    </span>
  );
}
```

- [ ] **Step 6: Wire into `Chart.tsx`** — import both, call `const { status: liveStatus } = useLiveStatus();` and render `<LiveDot status={liveStatus} />` in the toolbar row (near the symbol/tf controls). Keep it visible in all modes.

- [ ] **Step 7: Run tests + build**

Run: `cd frontend && npx vitest run src/components/LiveDot.test.tsx && npx tsc --noEmit && npm run build`
Expected: PASS, tsc 0, build 0.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/hooks/useLiveStatus.ts frontend/src/components/LiveDot.tsx frontend/src/components/LiveDot.test.tsx frontend/src/lib/types.ts frontend/src/pages/Chart.tsx
git commit -m "feat(fe): live/offline indicator on chart (useLiveStatus + LiveDot)"
```

---

# PHASE 2 — Realtime forming bar

## Task 6: `live_store` watch registry

**Files:**
- Modify: `src/journal/store/live_store.py`
- Test: `tests/test_live_store.py`

**Interfaces:**
- Produces: `upsert_watch(conn, symbol, tf, now_msc, ttl_ms) -> None`; `active_watches(conn, now_msc) -> list[tuple[str, str]]`; `prune_expired(conn, now_msc) -> int`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_live_store.py`)

```python
def test_active_watches_empty(conn):
    assert ls.active_watches(conn, 1_700_000_000_000) == []


def test_upsert_then_active(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    assert ls.active_watches(conn, 1_700_000_010_000) == [("XAUUSDc", "M5")]


def test_watch_expires(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    assert ls.active_watches(conn, 1_700_000_040_000) == []   # past expiry


def test_upsert_is_idempotent_per_pair(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_005_000, ttl_ms=30_000)
    assert conn.execute("SELECT COUNT(*) c FROM live_watches").fetchone()["c"] == 1
    assert ls.active_watches(conn, 1_700_000_034_000) == [("XAUUSDc", "M5")]  # refreshed


def test_prune_expired(conn):
    ls.upsert_watch(conn, "XAUUSDc", "M5", 1_700_000_000_000, ttl_ms=30_000)
    assert ls.prune_expired(conn, 1_700_000_040_000) == 1
    assert conn.execute("SELECT COUNT(*) c FROM live_watches").fetchone()["c"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_live_store.py -k "watch or prune" -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement** (append to `live_store.py`)

```python
def upsert_watch(conn: sqlite3.Connection, symbol: str, timeframe: str,
                 now_msc: int, ttl_ms: int) -> None:
    conn.execute(
        "INSERT INTO live_watches (symbol, timeframe, expires_msc, requested_msc) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(symbol, timeframe) DO UPDATE SET "
        "expires_msc = excluded.expires_msc, requested_msc = excluded.requested_msc",
        (symbol, timeframe, now_msc + ttl_ms, now_msc),
    )
    conn.commit()


def active_watches(conn: sqlite3.Connection, now_msc: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT symbol, timeframe FROM live_watches WHERE expires_msc > ? "
        "ORDER BY symbol, timeframe",
        (now_msc,),
    ).fetchall()
    return [(r["symbol"], r["timeframe"]) for r in rows]


def prune_expired(conn: sqlite3.Connection, now_msc: int) -> int:
    cur = conn.execute("DELETE FROM live_watches WHERE expires_msc <= ?", (now_msc,))
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_live_store.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/live_store.py tests/test_live_store.py
git commit -m "feat(store): live_store watch registry (upsert/active/prune)"
```

## Task 7: `live_store` forming bar

**Files:**
- Modify: `src/journal/store/live_store.py`
- Test: `tests/test_live_store.py`

**Interfaces:**
- Consumes: `adapter.base.Candle`.
- Produces: `upsert_forming(conn, symbol, tf, candle: Candle, now_msc) -> None`; `read_forming(conn, symbol, tf) -> Candle | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_live_store.py`)

```python
from journal.adapter.base import Candle

_BAR = Candle(time_msc=1_700_000_040_000, open=1.0, high=2.0, low=0.5, close=1.5,
              tick_volume=10, spread=3, real_volume=0)


def test_read_forming_none(conn):
    assert ls.read_forming(conn, "XAUUSDc", "M5") is None


def test_upsert_then_read_forming(conn):
    ls.upsert_forming(conn, "XAUUSDc", "M5", _BAR, 1_700_000_045_000)
    got = ls.read_forming(conn, "XAUUSDc", "M5")
    assert got == _BAR


def test_forming_overwrites(conn):
    ls.upsert_forming(conn, "XAUUSDc", "M5", _BAR, 1_700_000_045_000)
    newer = Candle(time_msc=1_700_000_040_000, open=1.0, high=9.0, low=0.5,
                   close=8.0, tick_volume=99, spread=3, real_volume=0)
    ls.upsert_forming(conn, "XAUUSDc", "M5", newer, 1_700_000_050_000)
    assert ls.read_forming(conn, "XAUUSDc", "M5") == newer
    assert conn.execute("SELECT COUNT(*) c FROM live_candles").fetchone()["c"] == 1


def test_upsert_forming_rejects_seconds(conn):
    import pytest
    bad = Candle(time_msc=1_700_000_040, open=1.0, high=2.0, low=0.5, close=1.5)
    with pytest.raises(ValueError):
        ls.upsert_forming(conn, "XAUUSDc", "M5", bad, 1_700_000_045_000)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_live_store.py -k forming -v`
Expected: FAIL.

- [ ] **Step 3: Implement** (append to `live_store.py`)

```python
def upsert_forming(conn: sqlite3.Connection, symbol: str, timeframe: str,
                   c: Candle, now_msc: int) -> None:
    if c.time_msc is None or c.time_msc < _MSC_FLOOR:
        raise ValueError(
            f"forming candle time_msc={c.time_msc!r} for {symbol} {timeframe} is "
            f"below {_MSC_FLOOR} — seconds leaked through (Trap 15). Fix the adapter "
            "boundary; never ×1000 here."
        )
    conn.execute(
        "INSERT INTO live_candles "
        "(symbol, timeframe, time_msc, open, high, low, close, tick_volume, spread, real_volume, updated_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol, timeframe) DO UPDATE SET "
        "time_msc=excluded.time_msc, open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, tick_volume=excluded.tick_volume, "
        "spread=excluded.spread, real_volume=excluded.real_volume, updated_msc=excluded.updated_msc",
        (symbol, timeframe, c.time_msc, c.open, c.high, c.low, c.close,
         c.tick_volume, c.spread, c.real_volume, now_msc),
    )
    conn.commit()


def read_forming(conn: sqlite3.Connection, symbol: str, timeframe: str) -> Candle | None:
    r = conn.execute(
        "SELECT time_msc, open, high, low, close, tick_volume, spread, real_volume "
        "FROM live_candles WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    if r is None:
        return None
    return Candle(time_msc=r["time_msc"], open=r["open"], high=r["high"], low=r["low"],
                  close=r["close"], tick_volume=r["tick_volume"], spread=r["spread"],
                  real_volume=r["real_volume"])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_live_store.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/live_store.py tests/test_live_store.py
git commit -m "feat(store): live_store forming bar upsert/read"
```

## Task 8: `journal live` serves watches (fetch, split forming/closed, promote)

**Files:**
- Create: `src/journal/ingest/live_candles.py` (the bridge-touching serve step — kept out of `candle_fill.py` but same layer; web must never import it)
- Modify: `src/journal/ingest/live.py` (`live_cycle` calls it)
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `MT5Client.copy_rates_range`, `live_store.active_watches/upsert_forming`, `candles_store.insert_candle/record_coverage`, `domain.resample.bucket_start/timeframe_ms`, `store.db.now_ms`.
- Produces: `serve_watches(client, conn, now_msc, *, lookback_bars=3) -> int` (number of forming bars written); called once per `live_cycle`.

The forming bar is the one whose bucket contains `now`: `bucket_start(now_msc, tf)`. Bars with `time_msc < that` are closed and final → promote to `candles`. `copy_rates_range` is asked for `[now - (lookback+1)·tf, now]`.

- [ ] **Step 1: Write the failing test** in `tests/test_live.py`

```python
def test_serve_watches_splits_forming_from_closed(conn):
    from journal.ingest.live_candles import serve_watches
    from journal.store import live_store as ls
    from journal.store import candles_store as cs
    from journal.adapter.base import Candle

    tf = "M5"; size = 300_000
    now = 1_700_000_000_000
    now = now - (now % size) + 120_000          # 2 min into the current bucket
    cur_bucket = now - (now % size)
    prev_bucket = cur_bucket - size
    closed = Candle(time_msc=prev_bucket, open=1, high=2, low=0.5, close=1.5,
                    tick_volume=5, spread=2, real_volume=0)
    forming = Candle(time_msc=cur_bucket, open=1.5, high=3, low=1.4, close=2.9,
                     tick_volume=7, spread=2, real_volume=0)

    class C(FakeLiveClient):
        def copy_rates_range(self, symbol, timeframe, date_from, date_to):
            return [closed, forming]

    client = C([[]])
    ls.upsert_watch(conn, "XAUUSDc", tf, now, ttl_ms=30_000)
    written = serve_watches(client, conn, now)

    assert written == 1
    # forming bar is in live_candles, NOT candles
    assert ls.read_forming(conn, "XAUUSDc", tf).time_msc == cur_bucket
    assert cs.read_candles(conn, "XAUUSDc", tf, cur_bucket, cur_bucket) == []
    # closed bar promoted to candles + coverage recorded
    rows = cs.read_candles(conn, "XAUUSDc", tf, prev_bucket, prev_bucket)
    assert len(rows) == 1
    assert cs.read_coverage(conn, "XAUUSDc", tf) != []


def test_serve_watches_noop_without_active_watch(conn):
    from journal.ingest.live_candles import serve_watches
    client = FakeLiveClientWithRates([[]], _bar=None)  # never asked
    assert serve_watches(client, conn, 1_700_000_000_000) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_live.py -k serve_watches -v`
Expected: FAIL (`ModuleNotFoundError: journal.ingest.live_candles`).

- [ ] **Step 3: Implement** `src/journal/ingest/live_candles.py`

```python
"""The bridge-touching realtime-candle serve step for `journal live`. Like
candle_fill.py it may call the bridge, so web/ must NEVER import it.

Each active watch: fetch the last few bars, keep the bar whose bucket contains
`now` as the forming bar (overwrite live_candles), and promote every older,
now-closed bar into the append-only `candles` table (+ coverage).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..adapter.base import MT5Client
from ..domain.resample import bucket_start, timeframe_ms
from ..store import candles_store as cs
from ..store import live_store as ls


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def serve_watches(client: MT5Client, conn: sqlite3.Connection, now_msc: int,
                  *, lookback_bars: int = 3) -> int:
    """Serve every active watch once. Returns how many forming bars were written."""
    written = 0
    for symbol, tf in ls.active_watches(conn, now_msc):
        size = timeframe_ms(tf)
        frm = now_msc - (lookback_bars + 1) * size
        bars = client.copy_rates_range(symbol, tf, _ms_to_dt(frm), _ms_to_dt(now_msc))
        cur_bucket = bucket_start(now_msc, tf)
        for c in bars:
            if c.time_msc is None:
                continue
            if c.time_msc >= cur_bucket:
                ls.upsert_forming(conn, symbol, tf, c, now_msc)   # forming
                written += 1
            else:
                cs.insert_candle(conn, symbol, tf, c)             # closed → promote
        # Record coverage over the CLOSED span we just fetched, so the store knows
        # these bars were fetched (a genuinely-empty closed slice is remembered too).
        if bars:
            cs.record_coverage(conn, symbol, tf, frm, cur_bucket - 1)
        conn.commit()
    return written
```

- [ ] **Step 4: Wire into `live_cycle`** — in `ingest/live.py`, import `from .live_candles import serve_watches`, and in `live_cycle` between the candle-request drain (step 4) and the heartbeat (step 5 from Task 3) add:

```python
    # (4b) serve realtime watches — forming bar + promote closed bars. Cheap
    # (one latest-bars fetch per active watch, ~1 given demand-driven watching).
    serve_watches(client, conn, observed_msc)
```

(Use `observed_msc` already computed at the top of the cycle so the forming/closed split and the heartbeat share one clock.)

- [ ] **Step 5: Run to verify pass + no regression**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS (new tests + all existing).

- [ ] **Step 6: Commit**

```bash
git add src/journal/ingest/live_candles.py src/journal/ingest/live.py tests/test_live.py
git commit -m "feat(live): serve_watches — forming bar + promote closed bars"
```

## Task 9: `POST /api/watch`

**Files:**
- Modify: `src/journal/web/api.py` (add `register_watch`)
- Modify: `src/journal/web/app.py` (add route)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `live_store.upsert_watch`, `store.db.now_ms`, `adapter.base.TIMEFRAMES`.
- Produces: `api.register_watch(conn, symbol, timeframe, *, ttl_ms=30_000, now_msc=None) -> {"ok": True}`; route `POST /api/watch`.

- [ ] **Step 1: Write the failing test** in `tests/test_api.py`

```python
def test_register_watch_makes_it_active(conn):
    from journal.web import api
    from journal.store import live_store as ls
    out = api.register_watch(conn, "XAUUSDc", "M5", ttl_ms=30_000, now_msc=1_700_000_000_000)
    assert out == {"ok": True}
    assert ls.active_watches(conn, 1_700_000_010_000) == [("XAUUSDc", "M5")]


def test_register_watch_rejects_bad_timeframe(conn):
    import pytest
    from journal.web import api
    with pytest.raises(ValueError):
        api.register_watch(conn, "XAUUSDc", "M7")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k register_watch -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `web/api.py`

```python
def register_watch(conn: sqlite3.Connection, symbol: str, timeframe: str, *,
                   ttl_ms: int = 30_000, now_msc: int | None = None) -> dict:
    """Web-side: upsert a demand-driven live watch. `journal live` serves it."""
    from ..adapter.base import TIMEFRAMES
    from ..store import live_store
    from ..store.db import now_ms

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")
    now = now_ms() if now_msc is None else now_msc
    live_store.upsert_watch(conn, symbol, timeframe, now, ttl_ms)
    return {"ok": True}
```

- [ ] **Step 4: Add the route** in `web/app.py`

```python
    @app.post("/api/watch")
    def api_watch(body=Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.register_watch(conn, body["symbol"], body["timeframe"]))
        except (KeyError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

(`Body` is already imported in `app.py` — it is used by the PNG-prefs PUT route.)

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api.py -k register_watch -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py
git commit -m "feat(api): POST /api/watch"
```

## Task 10: `GET /api/candles/live`

**Files:**
- Modify: `src/journal/web/api.py` (add `live_candle_payload`)
- Modify: `src/journal/web/app.py` (add route)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `live_store.read_forming`, `live_status_payload` (Task 4).
- Produces: `api.live_candle_payload(conn, symbol, timeframe, *, now_msc=None) -> {"forming": {...}|None, "beat_msc": int|None, "live": bool}`; route `GET /api/candles/live`.

- [ ] **Step 1: Write the failing test** in `tests/test_api.py`

```python
def test_live_candle_payload_null_when_no_forming(conn):
    from journal.web import api
    p = api.live_candle_payload(conn, "XAUUSDc", "M5", now_msc=1_700_000_000_000)
    assert p["forming"] is None and p["live"] is False


def test_live_candle_payload_returns_forming_and_liveness(conn):
    from journal.web import api
    from journal.store import live_store as ls
    from journal.adapter.base import Candle
    ls.beat(conn, 1_700_000_000_000)
    ls.upsert_forming(conn, "XAUUSDc", "M5",
                      Candle(time_msc=1_700_000_040_000, open=1, high=2, low=0.5,
                             close=1.5, tick_volume=9, spread=2, real_volume=0),
                      1_700_000_040_000)
    p = api.live_candle_payload(conn, "XAUUSDc", "M5", now_msc=1_700_000_003_000)
    assert p["live"] is True
    assert p["forming"] == {"time_msc": 1_700_000_040_000, "o": 1, "h": 2,
                            "l": 0.5, "c": 1.5, "v": 9}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k live_candle_payload -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `web/api.py`

```python
def live_candle_payload(conn: sqlite3.Connection, symbol: str, timeframe: str, *,
                        now_msc: int | None = None) -> dict:
    """The forming bar (or None) plus liveness — the FE poll for a live chart."""
    from ..store import live_store

    status = live_status_payload(conn, now_msc=now_msc)
    c = live_store.read_forming(conn, symbol, timeframe)
    forming = None if c is None else {
        "time_msc": c.time_msc, "o": c.open, "h": c.high, "l": c.low,
        "c": c.close, "v": c.tick_volume,
    }
    return {"forming": forming, "beat_msc": status["beat_msc"], "live": status["live"]}
```

- [ ] **Step 4: Add the route** in `web/app.py`

```python
    @app.get("/api/candles/live")
    def api_candles_live(
        symbol: str, timeframe: str, conn: sqlite3.Connection = Depends(get_conn)
    ):
        return JSONResponse(api.live_candle_payload(conn, symbol, timeframe))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api.py -k live_candle_payload -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py
git commit -m "feat(api): GET /api/candles/live (forming bar + liveness)"
```

## Task 11: FE `mergeForming` util + `useLiveForming` hook

**Files:**
- Modify: `frontend/src/lib/candles.ts` (add `mergeForming`)
- Create: `frontend/src/lib/liveForming.test.ts`
- Create: `frontend/src/hooks/useLiveForming.ts`
- Modify: `frontend/src/lib/types.ts` (add `LiveCandle` response type)

**Interfaces:**
- Consumes: `useApi`, `postJson`, `Candle` type.
- Produces: `mergeForming(candles: Candle[], forming: Candle | null): Candle[]` (replace last if same `time_msc`, append if newer, ignore if older); `useLiveForming(symbol, tf, enabled) -> { forming: Candle | null; live: boolean }`.

- [ ] **Step 1: Write the failing test** `frontend/src/lib/liveForming.test.ts`

```ts
import { describe, it, expect } from "vitest";
import { mergeForming } from "./candles";
import type { Candle } from "./types";

const bar = (t: number, c: number): Candle =>
  ({ time_msc: t, o: 1, h: 2, l: 0.5, c, v: 1 } as unknown as Candle);

describe("mergeForming", () => {
  it("returns candles unchanged when forming is null", () => {
    const cs = [bar(100, 1)];
    expect(mergeForming(cs, null)).toEqual(cs);
  });
  it("replaces the last bar when time_msc matches", () => {
    const out = mergeForming([bar(100, 1), bar(200, 2)], bar(200, 9));
    expect(out).toHaveLength(2);
    expect(out[1].c).toBe(9);
  });
  it("appends when forming is newer", () => {
    const out = mergeForming([bar(100, 1)], bar(200, 2));
    expect(out).toHaveLength(2);
    expect(out[1].time_msc).toBe(200);
  });
  it("ignores a forming bar older than the last", () => {
    const out = mergeForming([bar(200, 2)], bar(100, 1));
    expect(out).toEqual([bar(200, 2)]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/lib/liveForming.test.ts`
Expected: FAIL (`mergeForming` not exported).

- [ ] **Step 3: Add `LiveCandle` type** to `frontend/src/lib/types.ts`

```ts
export type LiveCandle = { forming: Candle | null; beat_msc: number | null; live: boolean };
```

- [ ] **Step 4: Implement `mergeForming`** in `frontend/src/lib/candles.ts`

```ts
// Merge the single realtime forming bar into a sorted candle array: replace the
// last bar if it shares time_msc, append if it is newer, ignore if older (a late
// poll must never rewrite history). Returns a new array; never mutates input.
export function mergeForming(candles: Candle[], forming: Candle | null): Candle[] {
  if (!forming) return candles;
  if (candles.length === 0) return [forming];
  const last = candles[candles.length - 1];
  if (forming.time_msc === last.time_msc) return [...candles.slice(0, -1), forming];
  if (forming.time_msc > last.time_msc) return [...candles, forming];
  return candles;
}
```

- [ ] **Step 5: Implement the hook** `frontend/src/hooks/useLiveForming.ts`

```ts
import { useEffect } from "react";
import { useApi, postJson } from "../lib/api";
import type { Candle, LiveCandle } from "../lib/types";
import type { Timeframe } from "../lib/candles";

const POLL_MS = 5000;
const WATCH_REFRESH_MS = 12_000;   // < server TTL (30s) so the watch never lapses

// Keeps a demand-driven watch alive and polls the forming bar while `enabled`
// (normal chart mode). Disabled (enabled=false) in replay/training — there is no
// live bar in the past. Passing an empty path to useApi when disabled stops both
// the poll and the watch upserts.
export function useLiveForming(symbol: string, tf: Timeframe, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const ping = () => { if (alive) postJson("/api/watch", { symbol, timeframe: tf }); };
    ping();
    const id = setInterval(ping, WATCH_REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, [symbol, tf, enabled]);

  const path = enabled
    ? `/api/candles/live?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`
    : "";
  const { data } = useApi<LiveCandle>(path, enabled ? POLL_MS : undefined);
  const forming: Candle | null = enabled && data ? data.forming : null;
  return { forming, live: !!data?.live };
}
```

Note: `useApi` fires on any non-empty path; when `enabled` is false pass `""` and it will fetch once harmlessly returning the SPA index — acceptable because `forming` is forced to `null`. (If a stricter guard is wanted, add an early `if (!path) return;` in a thin wrapper; not required for correctness here.)

- [ ] **Step 6: Run tests + build**

Run: `cd frontend && npx vitest run src/lib/liveForming.test.ts && npx tsc --noEmit`
Expected: PASS, tsc 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/candles.ts frontend/src/lib/liveForming.test.ts frontend/src/hooks/useLiveForming.ts frontend/src/lib/types.ts
git commit -m "feat(fe): mergeForming util + useLiveForming hook"
```

## Task 12: Wire realtime forming bar into the chart (normal mode only)

**Files:**
- Modify: `frontend/src/pages/Chart.tsx`

**Interfaces:**
- Consumes: `useLiveForming` (Task 11), `mergeForming` (Task 11), existing `shownCandles` / `replayOpen`.

- [ ] **Step 1: Add the hook call** in `Chart.tsx` (after `const data = useChartData(...)`)

```tsx
  // Realtime forming bar — normal mode only (never in replay/training, which is
  // historical). enabled flips the watch + poll off the instant replay opens.
  const liveEnabled = !replayOpen && !configOpen;
  const { forming } = useLiveForming(symbol, tf, liveEnabled);
```

- [ ] **Step 2: Merge the forming bar into the displayed candles** — replace the `shownCandles` computation so that in normal mode the forming bar is merged onto `data.candles`, while replay keeps its clipped array unchanged:

```tsx
  const shownCandles = replayOpen && cursor !== null
    ? clipToCursor(data.candles, cursor)
    : mergeForming(data.candles, forming);
```

(Import `mergeForming` from `../lib/candles`. Keep the existing `clipToCursor` import.)

- [ ] **Step 3: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: tsc 0, build 0.

- [ ] **Step 4: Run the whole FE test suite (no regressions)**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Chart.tsx
git commit -m "feat(fe): live forming bar on the normal chart (TradingView-style)"
```

---

# PHASE 3 — Completeness visuals

## Task 13: `classifyGaps` util (pure)

**Files:**
- Create: `frontend/src/lib/coverage.ts`
- Create: `frontend/src/lib/coverage.test.ts`

**Interfaces:**
- Consumes: `Candle` type, `Timeframe`, `timeframeMs`.
- Produces: `classifyGaps(bars: Candle[], missing: [number, number][], window: [number, number], tf: Timeframe): Segment[]` where `Segment = { from: number; to: number; kind: "covered" | "unfetched" | "closed" }`.

Classification over `window = [from, to]`: a span overlapping any `missing` range is `unfetched`; otherwise a span with no bar (gap larger than one bar) is `closed`; spans with bars are `covered`.

- [ ] **Step 1: Write the failing test** `frontend/src/lib/coverage.test.ts`

```ts
import { describe, it, expect } from "vitest";
import { classifyGaps, type Segment } from "./coverage";
import type { Candle } from "./types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 1 } as unknown as Candle);
const kinds = (s: Segment[]) => s.map((x) => x.kind);

describe("classifyGaps", () => {
  it("all covered when bars are contiguous and nothing missing", () => {
    const bars = [bar(0), bar(300_000), bar(600_000)];
    const segs = classifyGaps(bars, [], [0, 600_000], "M5");
    expect(kinds(segs)).toEqual(["covered"]);
  });
  it("marks an uncovered range as unfetched", () => {
    const bars = [bar(0)];
    const segs = classifyGaps(bars, [[300_000, 600_000]], [0, 600_000], "M5");
    expect(segs.some((s) => s.kind === "unfetched")).toBe(true);
  });
  it("marks a covered-but-empty gap as closed (market shut)", () => {
    // bars at 0 and 600_000, gap at 300_000 is inside coverage (not in missing)
    const bars = [bar(0), bar(600_000)];
    const segs = classifyGaps(bars, [], [0, 600_000], "M5");
    expect(segs.some((s) => s.kind === "closed")).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/lib/coverage.test.ts`
Expected: FAIL (cannot resolve `./coverage`).

- [ ] **Step 3: Implement** `frontend/src/lib/coverage.ts`

```ts
import type { Candle } from "./types";
import { timeframeMs, type Timeframe } from "./candles";

export type SegmentKind = "covered" | "unfetched" | "closed";
export type Segment = { from: number; to: number; kind: SegmentKind };

const overlapsMissing = (a: number, b: number, missing: [number, number][]) =>
  missing.some(([lo, hi]) => lo <= b && hi >= a);

// Walk the window in bar-sized steps, labelling each slot: unfetched if it
// overlaps a missing (uncovered) range, covered if a bar opens in it, else closed
// (inside coverage but no bar = market shut). Adjacent equal-kind slots merge.
export function classifyGaps(
  bars: Candle[], missing: [number, number][],
  window: [number, number], tf: Timeframe,
): Segment[] {
  const size = timeframeMs(tf);
  const [lo, hi] = window;
  const present = new Set(bars.map((b) => b.time_msc - (b.time_msc % size)));
  const out: Segment[] = [];
  for (let t = lo - (lo % size); t <= hi; t += size) {
    const end = t + size - 1;
    const kind: SegmentKind = overlapsMissing(t, end, missing)
      ? "unfetched"
      : present.has(t) ? "covered" : "closed";
    const last = out[out.length - 1];
    if (last && last.kind === kind && last.to + 1 === t) last.to = end;
    else out.push({ from: t, to: end, kind });
  }
  return out;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/lib/coverage.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/coverage.ts frontend/src/lib/coverage.test.ts
git commit -m "feat(fe): classifyGaps — covered/unfetched/closed segments"
```

## Task 14: `CoverageRibbon` + hole badge, wired under the chart

**Files:**
- Create: `frontend/src/components/CoverageRibbon.tsx`
- Create: `frontend/src/components/CoverageRibbon.test.tsx`
- Modify: `frontend/src/pages/Chart.tsx` (render it under the chart; pass `missing` from `useChartData`)
- Modify: `frontend/src/hooks/useChartData.ts` (expose `missing` from the last successful load)

**Interfaces:**
- Consumes: `classifyGaps` (Task 13), `Segment`.
- Produces: `<CoverageRibbon bars missing window tf onBackfill? />` — a full-width strip: green covered, red unfetched, grey closed; plus a compact badge "N lubang belum di-fetch" counting `unfetched` segments.

- [ ] **Step 1: Expose `missing` from `useChartData`** — add a ref `missingRef` updated in `load()` from `resp.missing`, and return `missing: missingRef.current` (as state so it re-renders). Minimal change:

```ts
  const [missing, setMissing] = useState<[number, number][]>([]);
  // inside load(), right after setCandles(...):
  setMissing(resp.missing as [number, number][]);
  // in the return object add: missing,
```

- [ ] **Step 2: Write the failing component test** `frontend/src/components/CoverageRibbon.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import CoverageRibbon from "./CoverageRibbon";
import type { Candle } from "../lib/types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 1 } as unknown as Candle);

describe("CoverageRibbon", () => {
  it("reports the count of unfetched holes in view", () => {
    render(
      <CoverageRibbon bars={[bar(0)]} missing={[[300_000, 600_000]]}
        window={[0, 600_000]} tf="M5" />,
    );
    expect(screen.getByText(/1 lubang belum di-fetch/i)).toBeInTheDocument();
  });
  it("shows no-hole state when fully covered", () => {
    render(
      <CoverageRibbon bars={[bar(0), bar(300_000), bar(600_000)]} missing={[]}
        window={[0, 600_000]} tf="M5" />,
    );
    expect(screen.getByText(/lengkap/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/CoverageRibbon.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Implement** `frontend/src/components/CoverageRibbon.tsx`

```tsx
import { useMemo } from "react";
import type { Candle } from "../lib/types";
import { classifyGaps } from "../lib/coverage";
import type { Timeframe } from "../lib/candles";

const COLOR = { covered: "#22d3ee", unfetched: "#fb7185", closed: "#3f3f52" };

export default function CoverageRibbon({
  bars, missing, window, tf, onBackfill,
}: {
  bars: Candle[]; missing: [number, number][];
  window: [number, number]; tf: Timeframe; onBackfill?: () => void;
}) {
  const segs = useMemo(() => classifyGaps(bars, missing, window, tf), [bars, missing, window, tf]);
  const span = Math.max(1, window[1] - window[0]);
  const holes = segs.filter((s) => s.kind === "unfetched").length;
  return (
    <div className="mt-1">
      <div className="flex h-1.5 w-full overflow-hidden rounded">
        {segs.map((s, i) => (
          <div key={i} title={s.kind}
            style={{ width: `${((s.to - s.from) / span) * 100}%`, background: COLOR[s.kind] }} />
        ))}
      </div>
      <div className="mt-1 flex items-center gap-2 text-[11px] text-muted">
        {holes > 0 ? (
          <>
            <span className="text-neg">{holes} lubang belum di-fetch di tampilan ini</span>
            {onBackfill && (
              <button onClick={onBackfill}
                className="px-2 py-0.5 rounded bg-white/5 hover:bg-white/10">Backfill</button>
            )}
          </>
        ) : (
          <span>data tampilan lengkap</span>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Wire into `Chart.tsx`** — render below `<CandleChart>` in normal mode (skip in replay):

```tsx
  {!replayOpen && (
    <CoverageRibbon
      bars={shownCandles}
      missing={data.missing}
      window={[shownCandles[0]?.time_msc ?? Date.now(), shownCandles[shownCandles.length - 1]?.time_msc ?? Date.now()]}
      tf={tf}
    />
  )}
```

(`onBackfill` is wired in Task 18.)

- [ ] **Step 6: Run tests + build**

Run: `cd frontend && npx vitest run src/components/CoverageRibbon.test.tsx && npx tsc --noEmit && npm run build`
Expected: PASS, tsc 0, build 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CoverageRibbon.tsx frontend/src/components/CoverageRibbon.test.tsx frontend/src/hooks/useChartData.ts frontend/src/pages/Chart.tsx
git commit -m "feat(fe): coverage ribbon + unfetched-hole badge under chart"
```

## Task 14b: On-chart shading overlay for unfetched holes

Spec §9 Phase 3 also asks for shading distinct from market-closed gaps directly on the canvas. Reuse the **sibling-overlay + time→x projection** pattern Spec B established in `MeasureOverlay.tsx` (read it first — it is prepend-stable, projecting by TIME not logical index, and re-projects on `subscribeVisibleLogicalRangeChange`/resize). Do not modify the lightweight-charts series.

**Files:**
- Create: `frontend/src/components/CoverageShadeOverlay.tsx`
- Modify: `frontend/src/components/CandleChart.tsx` (mount the overlay sibling, same slot as `MeasureOverlay`; pass `segments`)
- Test: `frontend/src/components/CoverageShadeOverlay.test.tsx`

**Interfaces:**
- Consumes: `Segment[]` (Task 13, from `classifyGaps`); the chart's `timeToCoordinate` (via the same `IChartApi`/timescale ref `MeasureOverlay` already receives).
- Produces: translucent red bands over `unfetched` segments and translucent grey bands over `closed` segments (covered segments unpainted), positioned by projecting `from`/`to` (ms→s) through the time scale — identical projection to `MeasureOverlay`.

- [ ] **Step 1: Read the existing overlay pattern**

Run: read `frontend/src/components/MeasureOverlay.tsx` and note how it (a) is rendered as a sibling of the chart div, (b) converts `time_msc`→coordinate, (c) re-projects on visible-range change and resize.

- [ ] **Step 2: Write the failing test** `frontend/src/components/CoverageShadeOverlay.test.tsx`

```tsx
import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import CoverageShadeOverlay from "./CoverageShadeOverlay";
import type { Segment } from "../lib/coverage";

// A fake projector maps ms→px linearly so the test is deterministic without a real chart.
const project = (ms: number) => ms / 1000;
const segs: Segment[] = [
  { from: 0, to: 300_000, kind: "unfetched" },
  { from: 300_001, to: 600_000, kind: "covered" },
];

describe("CoverageShadeOverlay", () => {
  it("renders one band per non-covered segment", () => {
    const { container } = render(
      <CoverageShadeOverlay segments={segs} project={project} height={200} />,
    );
    // only the unfetched segment is painted (covered is skipped)
    expect(container.querySelectorAll("[data-shade]").length).toBe(1);
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/CoverageShadeOverlay.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Implement** `frontend/src/components/CoverageShadeOverlay.tsx` (pure, projector-injected so it is testable without a live chart; `CandleChart` passes the real `timeToCoordinate`-backed projector)

```tsx
import type { Segment } from "../lib/coverage";

const FILL = { unfetched: "rgba(251,113,133,0.14)", closed: "rgba(63,63,82,0.28)", covered: "" };

// Absolute-positioned bands over the chart canvas. `project(ms)` returns the x
// pixel for a timestamp (CandleChart supplies one backed by the time scale's
// timeToCoordinate, same as MeasureOverlay); null when off-screen.
export default function CoverageShadeOverlay({
  segments, project, height,
}: {
  segments: Segment[]; project: (ms: number) => number | null; height: number;
}) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {segments.filter((s) => s.kind !== "covered").map((s, i) => {
        const x0 = project(s.from);
        const x1 = project(s.to);
        if (x0 == null || x1 == null || x1 <= x0) return null;
        return (
          <div key={i} data-shade={s.kind}
            style={{ position: "absolute", left: x0, width: x1 - x0, top: 0,
                     height, background: FILL[s.kind] }} />
        );
      })}
    </div>
  );
}
```

- [ ] **Step 5: Mount in `CandleChart.tsx`** as a sibling of the chart container (same wrapper that holds `MeasureOverlay`), building `segments` from `classifyGaps` on the chart's bars+missing+window and passing a `project` closure over the time scale's `timeToCoordinate` (mirror how `MeasureOverlay` obtains and uses it, including the re-projection subscriptions). Gate rendering to normal mode (skip when a replay clip is active).

- [ ] **Step 6: Run tests + build**

Run: `cd frontend && npx vitest run src/components/CoverageShadeOverlay.test.tsx && npx tsc --noEmit && npm run build`
Expected: PASS, tsc 0, build 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CoverageShadeOverlay.tsx frontend/src/components/CoverageShadeOverlay.test.tsx frontend/src/components/CandleChart.tsx
git commit -m "feat(fe): on-chart shading for unfetched vs closed gaps"
```

---

# PHASE 4 — Data-health panel + backfill

## Task 15: `GET /api/coverage`

**Files:**
- Modify: `src/journal/web/api.py` (add `coverage_payload`)
- Modify: `src/journal/web/app.py` (add route)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `candles_store.read_coverage`, `candles_store.missing_ranges`.
- Produces: `api.coverage_payload(conn, symbol, timeframe, from_ms, to_ms) -> {"covered": [[lo,hi]...], "missing": [[lo,hi]...]}`; route `GET /api/coverage`.

- [ ] **Step 1: Write the failing test** in `tests/test_api.py`

```python
def test_coverage_payload_reports_covered_and_missing(conn):
    from journal.web import api
    from journal.store import candles_store as cs
    cs.record_coverage(conn, "XAUUSDc", "M5", 0, 300_000)
    conn.commit()
    p = api.coverage_payload(conn, "XAUUSDc", "M5", 0, 600_000)
    assert [0, 300_000] in p["covered"]
    assert [300_001, 600_000] in p["missing"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k coverage_payload -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `web/api.py`

```python
def coverage_payload(conn: sqlite3.Connection, symbol: str, timeframe: str,
                     from_ms: int, to_ms: int) -> dict:
    """Covered vs still-missing ranges for [from_ms, to_ms] — feeds the health
    panel/ribbon over an explicit period. Pure DB read, no bridge."""
    covered = cs.read_coverage(conn, symbol, timeframe)
    missing = cs.missing_ranges(covered, (from_ms, to_ms))
    return {
        "covered": [[lo, hi] for lo, hi in covered if hi >= from_ms and lo <= to_ms],
        "missing": [[lo, hi] for lo, hi in missing],
    }
```

- [ ] **Step 4: Add the route** in `web/app.py`

```python
    @app.get("/api/coverage")
    def api_coverage(
        symbol: str, timeframe: str, from_ms: int, to_ms: int,
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        return JSONResponse(api.coverage_payload(conn, symbol, timeframe, from_ms, to_ms))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api.py -k coverage_payload -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py
git commit -m "feat(api): GET /api/coverage"
```

## Task 16: `POST /api/backfill`

**Files:**
- Modify: `src/journal/web/api.py` (add `backfill`)
- Modify: `src/journal/web/app.py` (add route)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `candle_queue.request_candles`.
- Produces: `api.backfill(conn, symbol, timeframe, from_ms, to_ms) -> {"request_id": int, "queued": bool}`; route `POST /api/backfill`.

- [ ] **Step 1: Write the failing test** in `tests/test_api.py`

```python
def test_backfill_enqueues_a_request(conn):
    from journal.web import api
    out = api.backfill(conn, "XAUUSDc", "M5", 0, 600_000)
    assert out["queued"] is True and out["request_id"] > 0
    row = conn.execute(
        "SELECT status FROM candle_requests WHERE id = ?", (out["request_id"],)
    ).fetchone()
    assert row["status"] == "pending"


def test_backfill_noop_when_already_covered(conn):
    from journal.web import api
    from journal.store import candles_store as cs
    cs.record_coverage(conn, "XAUUSDc", "M5", 0, 600_000)
    conn.commit()
    out = api.backfill(conn, "XAUUSDc", "M5", 0, 600_000)
    assert out == {"request_id": 0, "queued": False}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k backfill -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `web/api.py` (`candle_queue` is already imported for `candles_payload`)

```python
def backfill(conn: sqlite3.Connection, symbol: str, timeframe: str,
             from_ms: int, to_ms: int) -> dict:
    """Explicit backfill: enqueue a fill for [from_ms, to_ms] (deduped, skips
    already-covered). `journal live` drains it. Returns 0/False when nothing was
    queued because the range is already covered."""
    rid = candle_queue.request_candles(conn, symbol, timeframe, from_ms, to_ms)
    return {"request_id": rid, "queued": rid > 0}
```

- [ ] **Step 4: Add the route** in `web/app.py`

```python
    @app.post("/api/backfill")
    def api_backfill(body=Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return JSONResponse(api.backfill(
                conn, body["symbol"], body["timeframe"],
                int(body["from_ms"]), int(body["to_ms"]),
            ))
        except (KeyError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api.py -k backfill -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py
git commit -m "feat(api): POST /api/backfill (reuses candle_requests queue)"
```

## Task 17: `DataHealthPanel` + backfill wiring

**Files:**
- Create: `frontend/src/components/DataHealthPanel.tsx`
- Create: `frontend/src/components/DataHealthPanel.test.tsx`
- Modify: `frontend/src/pages/Chart.tsx` (render the panel; pass an `onBackfill` to `CoverageRibbon`)

**Interfaces:**
- Consumes: `classifyGaps` (Task 13), `postJson` (backfill), `wib` (from `lib/format.ts`) for WIB hole times.
- Produces: `<DataHealthPanel bars missing window tf symbol onBackfilled? />` showing % covered + hole list (WIB) + a Backfill button that `POST /api/backfill` for the visible window.

- [ ] **Step 1: Write the failing component test** `frontend/src/components/DataHealthPanel.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import DataHealthPanel from "./DataHealthPanel";
import type { Candle } from "../lib/types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 1, l: 1, c: 1, v: 1 } as unknown as Candle);

describe("DataHealthPanel", () => {
  it("lists the number of unfetched holes and a backfill button", () => {
    render(
      <DataHealthPanel bars={[bar(0)]} missing={[[300_000, 600_000]]}
        window={[0, 600_000]} tf="M5" symbol="XAUUSDc" />,
    );
    expect(screen.getByRole("button", { name: /backfill/i })).toBeInTheDocument();
    expect(screen.getByText(/1/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/DataHealthPanel.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement** `frontend/src/components/DataHealthPanel.tsx`

```tsx
import { useMemo, useState } from "react";
import type { Candle } from "../lib/types";
import { classifyGaps } from "../lib/coverage";
import { postJson } from "../lib/api";
import { wib } from "../lib/format";
import type { Timeframe } from "../lib/candles";

export default function DataHealthPanel({
  bars, missing, window, tf, symbol, onBackfilled,
}: {
  bars: Candle[]; missing: [number, number][]; window: [number, number];
  tf: Timeframe; symbol: string; onBackfilled?: () => void;
}) {
  const segs = useMemo(() => classifyGaps(bars, missing, window, tf), [bars, missing, window, tf]);
  const holes = segs.filter((s) => s.kind === "unfetched");
  const coveredMs = segs.filter((s) => s.kind !== "unfetched").reduce((a, s) => a + (s.to - s.from), 0);
  const pct = Math.round((coveredMs / Math.max(1, window[1] - window[0])) * 100);
  const [busy, setBusy] = useState(false);

  const backfill = async () => {
    setBusy(true);
    await postJson("/api/backfill", { symbol, timeframe: tf, from_ms: window[0], to_ms: window[1] });
    setBusy(false);
    onBackfilled?.();
  };

  return (
    <div className="text-[12px] rounded-lg bg-white/5 p-3">
      <div className="flex items-center justify-between">
        <span className="font-medium">Data health · {symbol} {tf}</span>
        <span className={pct >= 100 ? "text-cyan" : "text-neg"}>{pct}% tercover</span>
      </div>
      <div className="mt-2 text-muted">
        {holes.length === 0 ? "Tak ada lubang belum di-fetch di tampilan ini."
          : `${holes.length} lubang belum di-fetch:`}
      </div>
      {holes.length > 0 && (
        <ul className="mt-1 max-h-28 overflow-auto text-muted">
          {holes.map((h, i) => <li key={i}>{wib(h.from)} — {wib(h.to)}</li>)}
        </ul>
      )}
      <button disabled={busy || holes.length === 0} onClick={backfill}
        className="mt-2 px-2.5 py-1 rounded bg-cyan/15 text-cyan disabled:opacity-40">
        {busy ? "Mengantrikan…" : "Backfill rentang terlihat"}
      </button>
    </div>
  );
}
```

(If `wib` in `lib/format.ts` takes ms and returns a string, use it directly; confirm its signature and adapt the call if it needs seconds.)

- [ ] **Step 4: Wire into `Chart.tsx`** — render `<DataHealthPanel>` in the right-hand info column in normal mode, and pass `onBackfill={() => data.retry()}` to `CoverageRibbon` (Task 14) so the ribbon button and panel both re-poll after enqueuing:

```tsx
  {!replayOpen && (
    <DataHealthPanel
      bars={shownCandles} missing={data.missing}
      window={[shownCandles[0]?.time_msc ?? Date.now(), shownCandles[shownCandles.length - 1]?.time_msc ?? Date.now()]}
      tf={tf} symbol={symbol} onBackfilled={() => data.retry()}
    />
  )}
```

- [ ] **Step 5: Run tests + build**

Run: `cd frontend && npx vitest run src/components/DataHealthPanel.test.tsx && npx tsc --noEmit && npm run build`
Expected: PASS, tsc 0, build 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/DataHealthPanel.tsx frontend/src/components/DataHealthPanel.test.tsx frontend/src/pages/Chart.tsx
git commit -m "feat(fe): data-health panel + visible-range backfill"
```

## Task 18: Full-suite gate + graphify + rebuild

**Files:** none (verification task).

- [ ] **Step 1: Backend suite**

Run: `uv run pytest`
Expected: PASS (paste the summary line into the completion report — Definition of Done).

- [ ] **Step 2: Frontend suite + typecheck + build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: PASS, tsc 0, build 0.

- [ ] **Step 3: Rebuild safety**

Run: `uv run journal rebuild`
Expected: succeeds (live tables untouched, `trades` rebuilt).

- [ ] **Step 4: Update the graph**

Run: `graphify update .`
Expected: completes.

- [ ] **Step 5: Update the roadmap** — in `docs/ROADMAP-trade-chart-features.md`, set Spec C status to done (branch, gate counts).

- [ ] **Step 6: Commit**

```bash
git add docs/ROADMAP-trade-chart-features.md graphify-out
git commit -m "docs(roadmap): Spec C — realtime monitor + completeness DONE (pending human visual pass)"
```

---

## Self-review notes (author check against spec)

- **Spec §4 Phase 1 (heartbeat/liveness):** Tasks 1–5. ✅
- **Spec §4 Phase 2 (realtime forming bar):** Tasks 1 (tables), 6–12. ✅
- **Spec §4 Phase 3 (completeness visuals: ribbon/shading/badge):** Tasks 13, 14 (ribbon + badge), 14b (on-chart shading, reusing Spec B's MeasureOverlay projection pattern). Ribbon and shading share the one `classifyGaps` classifier by design. ✅
- **Spec §4 Phase 4 (panel + backfill):** Tasks 15–17. ✅
- **Spec §5 migration 007 / SCHEMA_VERSION 7 / rebuild-safe:** Task 1 + Task 18 step 3. ✅
- **Spec §6 live_store:** Tasks 2, 6, 7. ✅
- **Spec §7 live_cycle additions (heartbeat always; serve watches):** Tasks 3, 8. ✅
- **Spec §8 endpoints (live-status, watch, candles/live, coverage, backfill):** Tasks 4, 9, 10, 15, 16. ✅
- **Spec §9 FE (indicator, forming, ribbon, panel):** Tasks 5, 11–14, 17. ✅
- **Spec §10 testing / §11 DoD:** each task is TDD; Task 18 is the DoD gate. ✅
- **Type consistency:** `Segment`/`SegmentKind` (Task 13) reused verbatim in Tasks 14 & 17; `LiveStatus` (Task 5) reused in Task 4 payload shape; `LiveCandle` (Task 11) matches `live_candle_payload` (Task 10); `mergeForming`/`classifyGaps` signatures identical across producer and consumer tasks. ✅
