# Chart Phase B — Interactive Chart Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TradingView-style interactive `/chart` page to the React SPA that reads the Phase A `/api/candles` feed — candlestick with pan/zoom, symbol/timeframe switching, a live SL/TP/entry overlay, an info panel, and a minimal persisted settings gear — after finalizing the Phase A backend review items.

**Architecture:** The page is a thin React composition over a pure, unit-tested lib layer (`lib/candles.ts`, `lib/chartPrefs.ts`) and the `lightweight-charts` canvas. Selection lives in the URL query string; on-demand fill uses the Phase A queue model with bounded polling (never an infinite spinner); the web layer only reads the DB and enqueues fills — it never touches the MT5 bridge (rule 1 / M9 boundary).

**Tech Stack:** Python 3.12 + sqlite3 (stdlib) + FastAPI + typer (backend); React 18 + Vite + TypeScript + tailwind + react-router + vitest (frontend); **new frontend dep: `lightweight-charts` (TradingView, MIT)**.

## Global Constraints

- **Rule 1 / M9 boundary:** `web/` NEVER touches the MT5 bridge. `/api/candles` reads the DB and enqueues `candle_requests`; only `journal live` drains them. Do not import the adapter into any web/frontend path.
- **Rule 3 — time:** all timestamps are epoch **milliseconds, integer, broker-server = UTC** (`server_utc_offset_s = 0`). lightweight-charts wants **UNIX seconds** → divide `time_msc` by 1000 only when feeding the chart. Convert to **WIB (UTC+7) at display time only** — reuse the existing `wib()` in `frontend/src/lib/format.ts`.
- **Rule 4 — NULL vs 0:** `NULL` = unknown, `0.0` = none set. SL/TP price lines: draw ONLY real prices; skip both `null` and `0.0`.
- **Currency USC:** never print a bare `$`; money goes through `money(x, currency)` from `format.ts`.
- **Timeframes:** exactly `M1 M5 M15 H1 H4 D1`. **Symbols:** exactly `XAUUSDc BTCUSDc EURUSDc` (suffix `c` only). Defaults: `XAUUSDc` / `M5`.
- **New deps:** the ONLY new dependency permitted is `lightweight-charts` (frontend). No new backend deps, no other frontend deps (no testing-library) without asking.
- **Definition of done (every task that touches code):** `uv run pytest` green (paste output) where backend changed; `npm --prefix frontend test` green and `npm --prefix frontend run build` 0 errors where frontend changed; `uv run journal rebuild` still succeeds after Part 0; run `graphify update .` after code changes.
- **Commit footer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

**Backend (Part 0):**
- Create `src/journal/store/migrations/004_candle_requests_status_check.sql` — additive status CHECK via table rebuild.
- Modify `src/journal/store/schema.sql` — mirror the constrained `candle_requests` DDL.
- Modify `src/journal/store/db.py` — `SCHEMA_VERSION` 3 → 4.
- Modify `src/journal/store/candles_store.py` — `record_coverage` reverse-range guard.
- Modify `src/journal/cli.py` — F541 cosmetic on `candles-warm` banner.
- Modify `tests/test_migrations.py` — version + status-CHECK tests, equivalence still holds.
- Modify `tests/test_candles.py` (or nearest candles test) — coverage guard, ingest→coverage, `max_bars` truncation.

**Frontend (Parts 1–2):**
- Modify `frontend/package.json` / `package-lock.json` — add `lightweight-charts`.
- Create `frontend/src/lib/candles.ts` + `frontend/src/lib/candles.test.ts` — candle/window/overlay pure helpers + `fetchCandles`.
- Create `frontend/src/lib/chartPrefs.ts` + `frontend/src/lib/chartPrefs.test.ts` — selection parse + settings load/save.
- Modify `frontend/src/lib/types.ts` — `Candle`, `CandlesResponse`.
- Create `frontend/src/pages/Chart.tsx` — page composition + shared state.
- Create `frontend/src/components/ChartToolbar.tsx` — symbol/tf selectors, Jump-to-now, gear.
- Create `frontend/src/components/ChartSettingsPopover.tsx` — theme/grid.
- Create `frontend/src/components/CandleChart.tsx` — lightweight-charts canvas + live overlay (forwardRef).
- Create `frontend/src/components/ChartInfoPanel.tsx` — the four info blocks.
- Create `frontend/src/hooks/useChartData.ts` — fetch/merge/poll/lazy-load state.
- Modify `frontend/src/App.tsx` — `/chart` route.
- Modify `frontend/src/components/Sidebar.tsx` — "Chart" link.

---

# Part 0 — Backend cleanup (finalizes Phase A)

### Task 1: Migration 004 — `candle_requests.status` CHECK

**Files:**
- Create: `src/journal/store/migrations/004_candle_requests_status_check.sql`
- Modify: `src/journal/store/schema.sql:179-191` (the `candle_requests` table)
- Modify: `src/journal/store/db.py:20` (`SCHEMA_VERSION = 3` → `4`)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: existing migration runner `migrate(conn)` (applies `NNN_*.sql` in order, each stamped), `SCHEMA_VERSION`.
- Produces: a v4 schema where `candle_requests.status` is constrained to `('pending','claimed','done','failed')`, identical whether reached via `schema.sql` (fresh) or migration (existing).

- [ ] **Step 1: Update the version tests to expect 4 (write the failing test)**

In `tests/test_migrations.py`, change `test_schema_version_is_3` to:

```python
def test_schema_version_is_4():
    """Phase B adds the candle_requests.status CHECK (migration 004)."""
    assert SCHEMA_VERSION == 4
```

And update `test_migrate_reports_what_it_applied` to expect the new file:

```python
    applied = migrate(conn)
    assert applied == [2, 3, 4]
```

Add the new-surface tests (mirroring `test_trade_commands_rejects_an_unknown_status`):

```python
def test_candle_requests_rejects_an_unknown_status(tmp_path):
    """The CHECK is the guard that a typo'd status can't become a queue row
    journal live never recognises. Valid: pending|claimed|done|failed."""
    conn = connect(tmp_path / "j.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO candle_requests "
                "(symbol, timeframe, from_msc, to_msc, status, requested_msc) "
                "VALUES ('XAUUSDc', 'M5', 1, 2, 'nonsense', 3)"
            )
    finally:
        conn.close()


def test_candle_requests_accepts_each_valid_status(tmp_path):
    conn = connect(tmp_path / "j.db")
    try:
        for st in ("pending", "claimed", "done", "failed"):
            conn.execute(
                "INSERT INTO candle_requests "
                "(symbol, timeframe, from_msc, to_msc, status, requested_msc) "
                "VALUES ('XAUUSDc', 'M5', 1, 2, ?, 3)",
                (st,),
            )
        assert conn.execute("SELECT count(*) FROM candle_requests").fetchone()[0] == 4
    finally:
        conn.close()


def test_candle_requests_defaults_to_pending(tmp_path):
    conn = connect(tmp_path / "j.db")
    try:
        conn.execute(
            "INSERT INTO candle_requests "
            "(symbol, timeframe, from_msc, to_msc, requested_msc) "
            "VALUES ('XAUUSDc', 'M5', 1, 2, 3)"
        )
        assert conn.execute("SELECT status FROM candle_requests").fetchone()[0] == "pending"
    finally:
        conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: FAIL — `test_schema_version_is_4` fails (still 3); the reject test fails (no CHECK yet); `applied == [2, 3]`.

- [ ] **Step 3: Bump the schema version**

In `src/journal/store/db.py` line 20: `SCHEMA_VERSION = 4`.

- [ ] **Step 4: Mirror the constraint into `schema.sql`**

Replace the `status` line of the `candle_requests` table in `src/journal/store/schema.sql` (around line 185) so the table reads:

```sql
CREATE TABLE IF NOT EXISTS candle_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    from_msc      INTEGER NOT NULL,
    to_msc        INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                      ('pending','claimed','done','failed')),
    requested_msc INTEGER NOT NULL,
    claimed_msc   INTEGER,
    completed_msc INTEGER,
    bars_written  INTEGER,
    error         TEXT
);
```

- [ ] **Step 5: Write migration 004**

Create `src/journal/store/migrations/004_candle_requests_status_check.sql`. SQLite cannot add a CHECK to an existing column in place, so rebuild the table (create-copy-drop-rename). The runner wraps this file in its own `BEGIN`/`COMMIT`, so write bare statements (no transaction control here):

```sql
-- Migration 004 — Phase B: constrain candle_requests.status.
--
-- Brings a v3 database forward to v4. ADDITIVE in spirit (no data lost): adds a
-- CHECK enum to candle_requests.status, mirroring trade_commands. SQLite can't
-- ALTER a column to add a CHECK, so rebuild the table and copy rows across. The
-- same constrained DDL lives in schema.sql for fresh DBs; the two must stay in
-- lockstep (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- The runner supplies BEGIN/COMMIT around this file; do not add transaction
-- control here.

CREATE TABLE candle_requests_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    from_msc      INTEGER NOT NULL,
    to_msc        INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                      ('pending','claimed','done','failed')),
    requested_msc INTEGER NOT NULL,
    claimed_msc   INTEGER,
    completed_msc INTEGER,
    bars_written  INTEGER,
    error         TEXT
);

INSERT INTO candle_requests_new
    (id, symbol, timeframe, from_msc, to_msc, status, requested_msc,
     claimed_msc, completed_msc, bars_written, error)
SELECT
    id, symbol, timeframe, from_msc, to_msc, status, requested_msc,
    claimed_msc, completed_msc, bars_written, error
FROM candle_requests;

DROP TABLE candle_requests;
ALTER TABLE candle_requests_new RENAME TO candle_requests;
```

- [ ] **Step 6: Run the migration tests to verify they pass**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: PASS — including `test_migrated_db_matches_a_fresh_db` (fresh v4 schema == migrated v4 schema) and `test_migration_files_are_numbered_contiguously_from_2` (now `range(2, 5)`).

- [ ] **Step 7: Verify rebuild still works and full suite is green**

Run: `uv run journal rebuild --db "$CLAUDE_JOB_DIR/tmp/rebuild_check.db" 2>/dev/null || uv run journal rebuild`
Expected: succeeds (no schema error).
Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/journal/store/migrations/004_candle_requests_status_check.sql \
        src/journal/store/schema.sql src/journal/store/db.py tests/test_migrations.py
git commit -m "feat(store): migration 004 — candle_requests.status CHECK enum

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `record_coverage` reverse-range guard

**Files:**
- Modify: `src/journal/store/candles_store.py:77-86` (`record_coverage`)
- Test: `tests/test_candles.py`

**Interfaces:**
- Consumes: `record_coverage(conn, symbol, timeframe, from_ms, to_ms)`, `read_coverage(conn, symbol, timeframe)`.
- Produces: `record_coverage` is a no-op when `from_ms > to_ms` (mirrors `missing_ranges`' `lo > hi` guard), so a reversed range can never poison the stored disjoint set.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_candles.py` (import `candles_store as cs` if not already):

```python
def test_record_coverage_ignores_a_reversed_range(tmp_path):
    """from_ms > to_ms is nonsense; it must not enter the coverage set (mirror
    missing_ranges' lo>hi guard). A reversed call is a no-op."""
    from journal.store import candles_store as cs
    from journal.store.db import connect

    conn = connect(tmp_path / "j.db")
    try:
        cs.record_coverage(conn, "XAUUSDc", "M5", 2000, 1000)  # reversed
        assert cs.read_coverage(conn, "XAUUSDc", "M5") == []
        # a valid range still records normally
        cs.record_coverage(conn, "XAUUSDc", "M5", 1000, 2000)
        assert cs.read_coverage(conn, "XAUUSDc", "M5") == [(1000, 2000)]
    finally:
        conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_candles.py::test_record_coverage_ignores_a_reversed_range -q`
Expected: FAIL — without the guard the reversed range is stored, so `read_coverage` is not `[]`.

- [ ] **Step 3: Add the guard**

In `src/journal/store/candles_store.py`, at the top of `record_coverage` (after the docstring):

```python
def record_coverage(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int) -> None:
    """Merge [from_ms, to_ms] into stored coverage and rewrite the disjoint set.
    Caller commits (fill_range / ingest do one commit)."""
    if from_ms > to_ms:
        return                                       # reversed range: no-op (mirror missing_ranges)
    merged = _merge(read_coverage(conn, symbol, timeframe) + [(from_ms, to_ms)])
    conn.execute("DELETE FROM candle_coverage WHERE symbol = ? AND timeframe = ?", (symbol, timeframe))
    conn.executemany(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES (?, ?, ?, ?)",
        [(symbol, timeframe, a, b) for a, b in merged],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_candles.py::test_record_coverage_ignores_a_reversed_range -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/candles_store.py tests/test_candles.py
git commit -m "fix(candles): record_coverage no-ops on reversed range

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Phase A test backfill + F541 cosmetic

**Files:**
- Modify: `src/journal/cli.py:392` (F541: `f"== candles-warm =="` → plain string)
- Test: `tests/test_candles.py` (ingest→coverage cross-producer; `max_bars` truncation)

**Interfaces:**
- Consumes: `ingest.candles.sync_candles(client, conn)`, `web.api.candles_payload(conn, symbol, tf, from_ms, to_ms, *, max_bars=5000)`, `store.candles_store.read_coverage`, `adapter.fake.FakeMT5Client`.
- Produces: characterization tests proving (a) the legacy ingest path writes `candle_coverage`, (b) `candles_payload` truncates to `max_bars` (tail). No behaviour change beyond the cosmetic.

- [ ] **Step 1: Write the ingest→coverage characterization test**

The existing `sync_candles` already calls `record_coverage` (candles.py:77). Pin that contract. Look at how other tests in `tests/test_candles.py` build a `FakeMT5Client` with candles + a closed trade; reuse that exact fixture pattern. The test asserts coverage is non-empty for a symbol after a sync:

```python
def test_sync_candles_populates_coverage(tmp_path):
    """Cross-producer contract: the legacy per-trade ingest path must record
    candle_coverage too, so the store can tell 'fetched, empty' from 'never
    fetched' regardless of which producer filled it. Previously only true by
    inspection."""
    from journal.ingest.candles import sync_candles
    from journal.store import candles_store as cs
    from journal.store.db import connect
    # Reuse this module's existing fixture builders for a FakeMT5Client with
    # rates and a seeded CLOSED trade (see the other sync_candles tests above).
    conn = connect(tmp_path / "j.db")
    try:
        client = _seed_closed_trade_with_rates(conn)   # existing helper in this file
        report = sync_candles(client, conn)
        assert report.trades_seen >= 1
        for sym in report.symbols:
            # every symbol a window was fetched for now has coverage recorded
            has_any = any(
                cs.read_coverage(conn, sym, tf)
                for tf in ("M1", "M5", "M15", "H1", "H4", "D1")
            )
            assert has_any, f"no coverage recorded for {sym}"
    finally:
        conn.close()
```

If no reusable `_seed_closed_trade_with_rates` helper exists, build the client + closed trade inline exactly as the nearest existing `sync_candles` test does (do not invent a new fixture shape).

- [ ] **Step 2: Write the `max_bars` truncation test**

```python
def test_candles_payload_truncates_to_max_bars(tmp_path):
    """When more native bars are cached than max_bars, the payload returns the
    LAST max_bars (most recent), never the head. (The bucket-boundary
    aggregation bug is already fixed in Phase A — do not re-test it here.)"""
    from journal.web import api
    from journal.store import candles_store as cs
    from journal.store.db import connect
    from journal.adapter.base import Candle

    conn = connect(tmp_path / "j.db")
    try:
        base = 1_700_000_000_000
        step = 5 * 60_000
        n = 12
        for i in range(n):
            t = base + i * step
            cs.insert_candle(conn, "XAUUSDc", "M5",
                             Candle(time_msc=t, open=1.0, high=2.0, low=0.5, close=1.5, tick_volume=10))
        cs.record_coverage(conn, "XAUUSDc", "M5", base, base + (n - 1) * step)
        conn.commit()

        out = api.candles_payload(conn, "XAUUSDc", "M5", base, base + (n - 1) * step, max_bars=5)
        assert len(out["candles"]) == 5
        # kept the most recent 5 (tail), so first kept bar is the 8th (index 7)
        assert out["candles"][0]["time_msc"] == base + 7 * step
        assert out["candles"][-1]["time_msc"] == base + (n - 1) * step
    finally:
        conn.close()
```

Confirm the `Candle` field names against `src/journal/adapter/base.py:348` before running (adjust `tick_volume`/`open` etc. only if the dataclass differs).

- [ ] **Step 3: Run both tests to verify they pass (behaviour already exists)**

Run: `uv run pytest tests/test_candles.py -k "coverage or max_bars" -q`
Expected: PASS. If `test_candles_payload_truncates_to_max_bars` fails on a field name, fix the test to match the real `Candle`/`insert_candle` signature (not the source).

- [ ] **Step 4: Fix the F541 cosmetic**

In `src/journal/cli.py` line 392, change:

```python
    typer.echo(f"== candles-warm ==")
```
to:
```python
    typer.echo("== candles-warm ==")
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. Paste the summary line.

- [ ] **Step 6: Commit**

```bash
git add tests/test_candles.py src/journal/cli.py
git commit -m "test(candles): pin ingest→coverage + max_bars truncation; fix F541

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Part 1 — Frontend pure lib foundation

### Task 4: `lib/candles.ts` + `lib/chartPrefs.ts` + types (+ dep)

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json` (add `lightweight-charts`)
- Modify: `frontend/src/lib/types.ts` (add `Candle`, `CandlesResponse`)
- Create: `frontend/src/lib/candles.ts`, `frontend/src/lib/candles.test.ts`
- Create: `frontend/src/lib/chartPrefs.ts`, `frontend/src/lib/chartPrefs.test.ts`

**Interfaces:**
- Produces (imported by every later frontend task — exact signatures):
  - `type Timeframe = "M1"|"M5"|"M15"|"H1"|"H4"|"D1"`; `TIMEFRAMES: Timeframe[]`
  - `type Sym = "XAUUSDc"|"BTCUSDc"|"EURUSDc"`; `SYMBOLS: Sym[]`
  - `timeframeMs(tf: Timeframe): number`
  - `toSeconds(ms: number): number`
  - `initialWindow(tf: Timeframe, nowMs: number, bars?: number): [number, number]`
  - `olderWindow(currentFromMs: number, tf: Timeframe, bars?: number): [number, number]`
  - `mergeCandles(existing: Candle[], incoming: Candle[]): Candle[]`
  - `isNowVisible(lastBarMs: number|null, visibleToMs: number|null, tf: Timeframe): boolean`
  - `liveLines(pos: LivePosition): { price: number; color: string; title: string }[]`
  - `fetchCandles(symbol: string, tf: Timeframe, fromMs: number, toMs: number): Promise<CandlesResponse>`
  - `LINE_COLORS = { sl, tp, entry }`
  - From `chartPrefs.ts`: `type ChartTheme`, `interface ChartSettings { theme; grid }`, `DEFAULT_SETTINGS`, `loadChartSettings(store?)`, `saveChartSettings(s, store?)`, `parseSelection(params: URLSearchParams): { symbol: Sym; tf: Timeframe }`.

- [ ] **Step 1: Add the dependency (exact pin)**

Run: `npm --prefix frontend install --save-exact lightweight-charts@5`
Expected: `frontend/package.json` gains `"lightweight-charts": "5.x.y"` (exact, no caret) and `package-lock.json` updates. If `@5` does not resolve, use `npm --prefix frontend install --save-exact lightweight-charts@latest` and note the version.

- [ ] **Step 2: Add the candle types**

Append to `frontend/src/lib/types.ts`:

```typescript
// Phase B chart feed — mirrors web/api.candles_payload. time_msc is epoch ms,
// broker SERVER time (UTC). Divide by 1000 for lightweight-charts (UNIX seconds).
export interface Candle {
  time_msc: number;
  o: number; h: number; l: number; c: number; v: number;
}
export interface CandlesResponse {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  missing: [number, number][];  // [lo_ms, hi_ms] ranges NOT yet cached
  pending: boolean;             // a fill was enqueued for journal live to drain
}
```

- [ ] **Step 3: Write the failing tests for `candles.ts`**

Create `frontend/src/lib/candles.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import {
  timeframeMs, toSeconds, initialWindow, olderWindow, mergeCandles,
  isNowVisible, liveLines, LINE_COLORS,
} from "./candles";
import type { Candle } from "./types";
import type { LivePosition } from "./types";

const M1 = 60_000;
const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 2, l: 0.5, c: 1.5, v: 3 });

describe("candles helpers", () => {
  it("timeframeMs maps each frame to ms", () => {
    expect(timeframeMs("M1")).toBe(M1);
    expect(timeframeMs("M5")).toBe(5 * M1);
    expect(timeframeMs("M15")).toBe(15 * M1);
    expect(timeframeMs("H1")).toBe(60 * M1);
    expect(timeframeMs("H4")).toBe(240 * M1);
    expect(timeframeMs("D1")).toBe(1440 * M1);
  });

  it("toSeconds floors ms to unix seconds", () => {
    expect(toSeconds(1_700_000_000_500)).toBe(1_700_000_000);
  });

  it("initialWindow spans `bars` bars ending at now", () => {
    const now = 1_700_000_000_000;
    expect(initialWindow("M5", now, 300)).toEqual([now - 300 * 5 * M1, now]);
  });

  it("olderWindow extends left of the current oldest, non-overlapping", () => {
    const from = 1_700_000_000_000;
    expect(olderWindow(from, "M5", 300)).toEqual([from - 300 * 5 * M1, from - 1]);
  });

  it("mergeCandles dedupes by time, incoming wins, sorted ascending", () => {
    const a = [bar(3000), bar(1000)];
    const b = [bar(2000), { ...bar(1000), c: 9 }];
    const out = mergeCandles(a, b);
    expect(out.map((c) => c.time_msc)).toEqual([1000, 2000, 3000]);
    expect(out[0].c).toBe(9); // incoming overwrote existing at t=1000
  });

  it("isNowVisible true only when the right edge reaches the last bar", () => {
    const last = 1_700_000_000_000;
    expect(isNowVisible(last, last, "M5")).toBe(true);
    expect(isNowVisible(last, last - 5 * M1, "M5")).toBe(true);   // within one bar
    expect(isNowVisible(last, last - 6 * M1, "M5")).toBe(false);  // panned away
    expect(isNowVisible(null, last, "M5")).toBe(false);
    expect(isNowVisible(last, null, "M5")).toBe(false);
  });

  it("liveLines draws real prices only — skips null and 0.0 (rule 4)", () => {
    const base: LivePosition = {
      position_id: 7, symbol: "XAUUSDc", symbol_base: "XAUUSD",
      direction: "buy", volume: 0.1, open_price: 2405, price_current: 2410,
      sl: 0, tp: null, profit: 100, observed_msc: 1,
    };
    const lines = liveLines(base);
    // entry drawn (2405); SL skipped (0 = none set); TP skipped (null = unknown)
    expect(lines.map((l) => l.price)).toEqual([2405]);
    expect(lines[0].color).toBe(LINE_COLORS.entry);

    const full = liveLines({ ...base, sl: 2398, tp: 2412 });
    expect(full.map((l) => l.price).sort()).toEqual([2398, 2405, 2412]);
    const byTitle = Object.fromEntries(full.map((l) => [l.title.split(" ")[0], l.color]));
    expect(byTitle.SL).toBe(LINE_COLORS.sl);
    expect(byTitle.TP).toBe(LINE_COLORS.tp);
  });
});
```

- [ ] **Step 4: Run to verify failure**

Run: `npm --prefix frontend test -- candles`
Expected: FAIL — `./candles` module not found.

- [ ] **Step 5: Implement `candles.ts`**

Create `frontend/src/lib/candles.ts`:

```typescript
// Pure helpers for the Phase B chart. Time stays epoch-ms (broker SERVER = UTC);
// divide by 1000 only when feeding lightweight-charts. Rule 4: liveLines draws
// real prices only (skips null = unknown and 0.0 = none set).
import type { Candle, CandlesResponse, LivePosition } from "./types";

export type Timeframe = "M1" | "M5" | "M15" | "H1" | "H4" | "D1";
export const TIMEFRAMES: Timeframe[] = ["M1", "M5", "M15", "H1", "H4", "D1"];

export type Sym = "XAUUSDc" | "BTCUSDc" | "EURUSDc";
export const SYMBOLS: Sym[] = ["XAUUSDc", "BTCUSDc", "EURUSDc"];

const MIN = 60_000;
const TF_MS: Record<Timeframe, number> = {
  M1: 1 * MIN, M5: 5 * MIN, M15: 15 * MIN, H1: 60 * MIN, H4: 240 * MIN, D1: 1440 * MIN,
};

export function timeframeMs(tf: Timeframe): number {
  return TF_MS[tf];
}

export function toSeconds(ms: number): number {
  return Math.floor(ms / 1000);
}

export function initialWindow(tf: Timeframe, nowMs: number, bars = 300): [number, number] {
  return [nowMs - timeframeMs(tf) * bars, nowMs];
}

export function olderWindow(currentFromMs: number, tf: Timeframe, bars = 300): [number, number] {
  return [currentFromMs - timeframeMs(tf) * bars, currentFromMs - 1];
}

export function mergeCandles(existing: Candle[], incoming: Candle[]): Candle[] {
  const m = new Map<number, Candle>();
  for (const c of existing) m.set(c.time_msc, c);
  for (const c of incoming) m.set(c.time_msc, c); // incoming wins on collision
  return [...m.values()].sort((a, b) => a.time_msc - b.time_msc);
}

export function isNowVisible(
  lastBarMs: number | null, visibleToMs: number | null, tf: Timeframe,
): boolean {
  if (lastBarMs === null || visibleToMs === null) return false;
  return visibleToMs >= lastBarMs - timeframeMs(tf); // right edge within one bar of last
}

export const LINE_COLORS = { sl: "#fb7185", tp: "#34d399", entry: "#9a97c4" };

export function liveLines(pos: LivePosition): { price: number; color: string; title: string }[] {
  const out: { price: number; color: string; title: string }[] = [];
  const add = (v: number | null, color: string, title: string) => {
    if (v !== null && v !== undefined && Math.abs(v) > 1e-9) out.push({ price: v, color, title });
  };
  add(pos.open_price, LINE_COLORS.entry, `entry #${pos.position_id}`);
  add(pos.sl, LINE_COLORS.sl, `SL #${pos.position_id}`);
  add(pos.tp, LINE_COLORS.tp, `TP #${pos.position_id}`);
  return out;
}

export async function fetchCandles(
  symbol: string, tf: Timeframe, fromMs: number, toMs: number,
): Promise<CandlesResponse> {
  const q = new URLSearchParams({
    symbol, timeframe: tf, from: String(Math.floor(fromMs)), to: String(Math.floor(toMs)),
  });
  const r = await fetch(`/api/candles?${q}`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
  return body as CandlesResponse;
}
```

- [ ] **Step 6: Run to verify pass**

Run: `npm --prefix frontend test -- candles`
Expected: PASS.

- [ ] **Step 7: Write the failing tests for `chartPrefs.ts`**

Create `frontend/src/lib/chartPrefs.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import {
  DEFAULT_SETTINGS, loadChartSettings, saveChartSettings, parseSelection,
} from "./chartPrefs";

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

describe("chartPrefs", () => {
  it("loads defaults when nothing stored", () => {
    expect(loadChartSettings(fakeStore())).toEqual(DEFAULT_SETTINGS);
  });

  it("round-trips saved settings", () => {
    const s = fakeStore();
    saveChartSettings({ theme: "light", grid: false }, s);
    expect(loadChartSettings(s)).toEqual({ theme: "light", grid: false });
  });

  it("falls back to defaults on corrupt json", () => {
    const s = fakeStore();
    s.setItem("mt5j.chart.settings", "{not json");
    expect(loadChartSettings(s)).toEqual(DEFAULT_SETTINGS);
  });

  it("parseSelection returns defaults for absent/invalid params", () => {
    expect(parseSelection(new URLSearchParams(""))).toEqual({ symbol: "XAUUSDc", tf: "M5" });
    expect(parseSelection(new URLSearchParams("symbol=NOPE&tf=X9")))
      .toEqual({ symbol: "XAUUSDc", tf: "M5" });
  });

  it("parseSelection honours valid params", () => {
    expect(parseSelection(new URLSearchParams("symbol=BTCUSDc&tf=H1")))
      .toEqual({ symbol: "BTCUSDc", tf: "H1" });
  });
});
```

- [ ] **Step 8: Run to verify failure**

Run: `npm --prefix frontend test -- chartPrefs`
Expected: FAIL — module not found.

- [ ] **Step 9: Implement `chartPrefs.ts`**

Create `frontend/src/lib/chartPrefs.ts`:

```typescript
// URL-param selection + persisted chart appearance. Selection defaults to
// XAUUSDc / M5. Settings persist in localStorage (Phase B scope: theme + grid;
// the full settings panel + broader preferences are Phase C).
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "./candles";

export type ChartTheme = "dark" | "light";
export interface ChartSettings {
  theme: ChartTheme;
  grid: boolean;
}
export const DEFAULT_SETTINGS: ChartSettings = { theme: "dark", grid: true };
const KEY = "mt5j.chart.settings";

export function loadChartSettings(store: Storage = localStorage): ChartSettings {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const p = JSON.parse(raw) as Partial<ChartSettings>;
    return {
      theme: p.theme === "light" ? "light" : "dark",
      grid: typeof p.grid === "boolean" ? p.grid : true,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveChartSettings(s: ChartSettings, store: Storage = localStorage): void {
  try {
    store.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota / private mode — appearance-only, safe to ignore */
  }
}

export function parseSelection(params: URLSearchParams): { symbol: Sym; tf: Timeframe } {
  const s = params.get("symbol");
  const t = params.get("tf");
  return {
    symbol: (SYMBOLS as string[]).includes(s ?? "") ? (s as Sym) : "XAUUSDc",
    tf: (TIMEFRAMES as string[]).includes(t ?? "") ? (t as Timeframe) : "M5",
  };
}
```

- [ ] **Step 10: Run the full frontend suite + build**

Run: `npm --prefix frontend test`
Expected: all pass (existing 19 + new).
Run: `npm --prefix frontend run build`
Expected: 0 errors.

- [ ] **Step 11: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/
git commit -m "feat(chart): pure candle/prefs lib + lightweight-charts dep

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Part 2 — Frontend UI

### Task 5: Chart page shell — route, sidebar, toolbar, settings gear

**Files:**
- Create: `frontend/src/pages/Chart.tsx`
- Create: `frontend/src/components/ChartToolbar.tsx`
- Create: `frontend/src/components/ChartSettingsPopover.tsx`
- Modify: `frontend/src/App.tsx` (route), `frontend/src/components/Sidebar.tsx` (link)

**Interfaces:**
- Consumes: `parseSelection`, `loadChartSettings`, `saveChartSettings`, `SYMBOLS`, `TIMEFRAMES`, types.
- Produces (the composition contract later tasks fill in):
  - `Chart.tsx` owns shared state: `{ symbol, tf }` (URL), `settings: ChartSettings`, `hovered: HoverBar | null`, `nowVisible: boolean`, and a `chartRef` exposing `{ jumpToNow(): void }`.
  - `CandleChart` prop contract (built in Task 6): `{ symbol: Sym; tf: Timeframe; settings: ChartSettings; live: LiveData | null; onHover(b: HoverBar | null): void; onNowVisibleChange(v: boolean): void; ref → { jumpToNow() } }`.
  - `ChartInfoPanel` prop contract (built in Task 8): `{ symbol: Sym; tf: Timeframe; candles: Candle[]; hovered: HoverBar | null; live: LiveData | null; currency: string }`.
  - `type HoverBar = { time_msc: number; o: number; h: number; l: number; c: number; v: number }` (define in `types.ts` in this task).

- [ ] **Step 1: Add `HoverBar` to types**

Append to `frontend/src/lib/types.ts`:

```typescript
// A candle the crosshair is hovering (or the latest bar when idle). Same shape
// as Candle; named separately so the info panel's intent reads clearly.
export type HoverBar = Candle;
```

- [ ] **Step 2: Build the settings popover**

Create `frontend/src/components/ChartSettingsPopover.tsx`:

```typescript
import type { ChartSettings } from "../lib/chartPrefs";

export default function ChartSettingsPopover({
  settings, onChange, onClose,
}: {
  settings: ChartSettings;
  onChange: (s: ChartSettings) => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div className="glass absolute right-0 top-9 z-20 w-56 p-3 text-[12px]">
        <div className="mb-3">
          <div className="text-muted mb-1">Tema chart</div>
          <div className="flex gap-1">
            {(["dark", "light"] as const).map((t) => (
              <button
                key={t}
                onClick={() => onChange({ ...settings, theme: t })}
                className={
                  "px-2 py-1 rounded-md capitalize " +
                  (settings.theme === t
                    ? "bg-violet/25 ring-1 ring-inset ring-violet/35 text-ink"
                    : "text-muted hover:text-ink")
                }
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <label className="flex items-center justify-between">
          <span className="text-muted">Garis grid</span>
          <input
            type="checkbox"
            checked={settings.grid}
            onChange={(e) => onChange({ ...settings, grid: e.target.checked })}
          />
        </label>
      </div>
    </>
  );
}
```

- [ ] **Step 3: Build the toolbar**

Create `frontend/src/components/ChartToolbar.tsx`:

```typescript
import { useState } from "react";
import { SYMBOLS, TIMEFRAMES, type Sym, type Timeframe } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import ChartSettingsPopover from "./ChartSettingsPopover";

export default function ChartToolbar({
  symbol, tf, settings, onSymbol, onTf, onSettings, onJumpNow,
}: {
  symbol: Sym;
  tf: Timeframe;
  settings: ChartSettings;
  onSymbol: (s: Sym) => void;
  onTf: (t: Timeframe) => void;
  onSettings: (s: ChartSettings) => void;
  onJumpNow: () => void;
}) {
  const [gear, setGear] = useState(false);
  return (
    <div className="flex items-center gap-2 mb-3">
      <select
        value={symbol}
        onChange={(e) => onSymbol(e.target.value as Sym)}
        className="glass px-2 py-1 text-[13px] bg-transparent"
        aria-label="symbol"
      >
        {SYMBOLS.map((s) => (
          <option key={s} value={s} className="bg-bg">{s}</option>
        ))}
      </select>

      <div className="glass flex overflow-hidden text-[12px]">
        {TIMEFRAMES.map((t) => (
          <button
            key={t}
            onClick={() => onTf(t)}
            className={
              "px-2.5 py-1 " +
              (t === tf ? "bg-violet/25 text-ink" : "text-muted hover:text-ink")
            }
          >
            {t}
          </button>
        ))}
      </div>

      <button
        onClick={onJumpNow}
        className="glass px-2.5 py-1 text-[12px] text-muted hover:text-ink"
      >
        Ke sekarang
      </button>

      <div className="relative ml-auto">
        <button
          onClick={() => setGear((g) => !g)}
          className="glass px-2.5 py-1 text-[13px] text-muted hover:text-ink"
          aria-label="settings"
        >
          ⚙
        </button>
        {gear && (
          <ChartSettingsPopover
            settings={settings}
            onChange={onSettings}
            onClose={() => setGear(false)}
          />
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Build the page shell**

Create `frontend/src/pages/Chart.tsx`. Uses placeholder `<div>`s where `CandleChart` (Task 6) and `ChartInfoPanel` (Task 8) plug in, so the page compiles and routes now:

```typescript
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { parseSelection, loadChartSettings, saveChartSettings, type ChartSettings } from "../lib/chartPrefs";
import type { Sym, Timeframe } from "../lib/candles";
import type { HoverBar, LiveData } from "../lib/types";
import ChartToolbar from "../components/ChartToolbar";

export interface ChartHandle { jumpToNow: () => void }

export default function Chart() {
  const [params, setParams] = useSearchParams();
  const { symbol, tf } = parseSelection(params);
  const [settings, setSettings] = useState<ChartSettings>(() => loadChartSettings());
  const [hovered, setHovered] = useState<HoverBar | null>(null);
  const [, setNowVisible] = useState(false);
  const chartRef = useRef<ChartHandle>(null);

  const { data: live } = useApi<LiveData>("/api/live", 2500);
  const currency = live?.header.currency ?? "USC";

  const setSelection = (next: { symbol?: Sym; tf?: Timeframe }) => {
    const p = new URLSearchParams(params);
    p.set("symbol", next.symbol ?? symbol);
    p.set("tf", next.tf ?? tf);
    setParams(p, { replace: true });
  };
  const applySettings = (s: ChartSettings) => { setSettings(s); saveChartSettings(s); };

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <ChartToolbar
        symbol={symbol}
        tf={tf}
        settings={settings}
        onSymbol={(s) => setSelection({ symbol: s })}
        onTf={(t) => setSelection({ tf: t })}
        onSettings={applySettings}
        onJumpNow={() => chartRef.current?.jumpToNow()}
      />
      <div className="flex gap-3 flex-1 min-h-0">
        {/* CandleChart (Task 6) mounts here */}
        <div className="glass flex-1 min-h-0 flex items-center justify-center text-muted text-sm">
          chart — {symbol} {tf}
        </div>
        {/* ChartInfoPanel (Task 8) mounts here */}
        <aside className="glass w-[240px] shrink-0 p-3 hidden lg:block">
          <div className="text-muted text-[12px]">info panel</div>
        </aside>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Register the route and sidebar link**

In `frontend/src/App.tsx`, add the import and route:

```typescript
import Chart from "./pages/Chart";
```
```typescript
          <Route path="/chart" element={<Chart />} />
```
(place the `<Route>` after the `/live` route).

In `frontend/src/components/Sidebar.tsx`, add to `LINKS` after the Live entry:

```typescript
  { to: "/chart", label: "Chart" },
```

- [ ] **Step 6: Build and eyeball the route**

Run: `npm --prefix frontend run build`
Expected: 0 errors.
Run (manual, optional): `npm --prefix frontend run dev` in one shell and `uv run journal serve --db data/journal.db` in another; open `http://localhost:5173/chart` — the toolbar, symbol/tf selectors, gear popover, and placeholders render; changing symbol/tf updates the URL; reload restores it.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Chart.tsx frontend/src/components/ChartToolbar.tsx \
        frontend/src/components/ChartSettingsPopover.tsx frontend/src/App.tsx \
        frontend/src/components/Sidebar.tsx frontend/src/lib/types.ts
git commit -m "feat(chart): /chart page shell — route, toolbar, settings gear

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: CandleChart + `useChartData` (render, pan/zoom lazy-load, fill-poll UX)

**Files:**
- Create: `frontend/src/hooks/useChartData.ts`
- Create: `frontend/src/components/CandleChart.tsx`
- Modify: `frontend/src/pages/Chart.tsx` (mount `CandleChart`, render fill-poll UX)

**Interfaces:**
- Consumes: `fetchCandles`, `mergeCandles`, `initialWindow`, `olderWindow`, `toSeconds`, `timeframeMs`, `isNowVisible`, `liveLines`, `LINE_COLORS`, `wib` (format.ts), types, `ChartHandle`.
- Produces:
  - `useChartData(symbol, tf): { candles: Candle[]; status: ChartStatus; error: string | null; lastBarMs: number | null; retry(): void; loadOlder(): void }` where `type ChartStatus = "loading"|"polling"|"ready"|"gaveup"|"error"`.
  - `CandleChart` forwardRef component exposing `{ jumpToNow() }` and calling `onHover` / `onNowVisibleChange`. (Live overlay added in Task 7.)

- [ ] **Step 1: Implement `useChartData` (fetch / merge / bounded-poll / lazy-load)**

Create `frontend/src/hooks/useChartData.ts`:

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCandles, initialWindow, mergeCandles, olderWindow, type Timeframe } from "../lib/candles";
import type { Candle } from "../lib/types";

export type ChartStatus = "loading" | "polling" | "ready" | "gaveup" | "error";

const POLL_MS = 2000;
const MAX_POLLS = 5;      // ~10s total, then give up (journal live likely not running)

export function useChartData(symbol: string, tf: Timeframe) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState<ChartStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const fromRef = useRef<number>(0);          // oldest loaded window bound (ms)
  const pollRef = useRef<number>(0);          // poll attempts for the current window
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const clearTimer = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };

  // Fetch [from,to], merge, and decide whether to keep polling this window.
  const load = useCallback(async (from: number, to: number, isPoll: boolean) => {
    try {
      const resp = await fetchCandles(symbol, tf, from, to);
      if (!alive.current) return;
      setCandles((prev) => mergeCandles(prev, resp.candles));
      setError(null);
      const stillMissing = resp.missing.length > 0;
      if (!stillMissing) { setStatus("ready"); pollRef.current = 0; return; }
      // Missing remains. Aggregated bars may already render (pending true) — that
      // is fine; keep polling bounded so a dead queue can't spin forever.
      if (pollRef.current >= MAX_POLLS) { setStatus("gaveup"); return; }
      setStatus("polling");
      pollRef.current += 1;
      clearTimer();
      timer.current = setTimeout(() => load(from, to, true), POLL_MS);
    } catch (e) {
      if (alive.current) { setError(String(e)); setStatus("error"); }
    }
  }, [symbol, tf]);

  // (Re)load from scratch whenever symbol/tf changes.
  useEffect(() => {
    alive.current = true;
    setCandles([]); setStatus("loading"); setError(null); pollRef.current = 0;
    const [from, to] = initialWindow(tf, Date.now());
    fromRef.current = from;
    load(from, to, false);
    return () => { alive.current = false; clearTimer(); };
  }, [symbol, tf, load]);

  const retry = useCallback(() => {
    pollRef.current = 0;
    setStatus("polling");
    const [, to] = initialWindow(tf, Date.now());
    load(fromRef.current, to, false);
  }, [tf, load]);

  // Pan: extend the loaded window to the left. Does not disturb the poll state.
  const loadOlder = useCallback(() => {
    const [from, to] = olderWindow(fromRef.current, tf);
    fromRef.current = from;
    load(from, to, false);
  }, [tf, load]);

  const lastBarMs = candles.length ? candles[candles.length - 1].time_msc : null;
  return { candles, status, error, lastBarMs, retry, loadOlder };
}
```

- [ ] **Step 2: Implement `CandleChart`**

Create `frontend/src/components/CandleChart.tsx`. NOTE on the series-creation line: this is written for **lightweight-charts v5** (`chart.addSeries(CandlestickSeries, …)`). If the installed major is v4, replace that one line with `chart.addCandlestickSeries({...})` and drop the `CandlestickSeries` import — verify against `node_modules/lightweight-charts` types before running.

```typescript
import {
  forwardRef, useEffect, useImperativeHandle, useRef,
} from "react";
import {
  createChart, CandlestickSeries, ColorType, CrosshairMode,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from "lightweight-charts";
import { toSeconds, type Sym, type Timeframe } from "../lib/candles";
import type { ChartSettings } from "../lib/chartPrefs";
import type { Candle, HoverBar } from "../lib/types";
import { wib } from "../lib/format";
import type { ChartHandle } from "../pages/Chart";

const DARK = {
  bg: "transparent", text: "#9a97c4", grid: "rgba(255,255,255,0.06)",
  border: "rgba(255,255,255,0.09)", up: "#34d399", down: "#fb7185",
};
const LIGHT = {
  bg: "#ffffff", text: "#334155", grid: "rgba(0,0,0,0.06)",
  border: "rgba(0,0,0,0.12)", up: "#059669", down: "#e11d48",
};

const CandleChart = forwardRef<ChartHandle, {
  symbol: Sym;
  tf: Timeframe;
  settings: ChartSettings;
  candles: Candle[];
  onHover: (b: HoverBar | null) => void;
  onNowVisibleChange: (v: boolean) => void;
  onRequestOlder: () => void;
  lastBarMs: number | null;
}>(function CandleChart(props, ref) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const cbs = useRef(props);
  cbs.current = props;

  // Create the chart once.
  useEffect(() => {
    if (!el.current) return;
    const theme = props.settings.theme === "light" ? LIGHT : DARK;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: props.settings.grid },
        horzLines: { color: theme.grid, visible: props.settings.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: theme.border },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
        // Axis labels in WIB (server=UTC, +7h; display only).
        tickMarkFormatter: (t: number) => wib((t as number) * 1000, 0).replace(" WIB", ""),
      },
      localization: { timeFormatter: (t: number) => wib((t as number) * 1000, 0) },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: theme.up, downColor: theme.down,
      wickUpColor: theme.up, wickDownColor: theme.down, borderVisible: false,
    });
    chart.current = c;
    series.current = s;

    c.subscribeCrosshairMove((param) => {
      const bar = param.seriesData.get(s) as
        | { open: number; high: number; low: number; close: number } | undefined;
      if (!bar || param.time === undefined) { cbs.current.onHover(null); return; }
      cbs.current.onHover({
        time_msc: (param.time as number) * 1000,
        o: bar.open, h: bar.high, l: bar.low, c: bar.close, v: 0,
      });
    });

    c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || !series.current) return;
      const bars = series.current.barsInLogicalRange(range);
      if (bars && bars.barsBefore < 20) cbs.current.onRequestOlder();
      const vis = c.timeScale().getVisibleRange();
      const toMs = vis ? (vis.to as number) * 1000 : null;
      const last = cbs.current.lastBarMs;
      cbs.current.onNowVisibleChange(
        last !== null && toMs !== null && toMs >= last - 60_000,
      );
    });

    return () => { c.remove(); chart.current = null; series.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply theme/grid when settings change (no full re-create).
  useEffect(() => {
    if (!chart.current || !series.current) return;
    const theme = props.settings.theme === "light" ? LIGHT : DARK;
    chart.current.applyOptions({
      layout: { background: { type: ColorType.Solid, color: theme.bg }, textColor: theme.text },
      grid: {
        vertLines: { color: theme.grid, visible: props.settings.grid },
        horzLines: { color: theme.grid, visible: props.settings.grid },
      },
    });
    series.current.applyOptions({
      upColor: theme.up, downColor: theme.down, wickUpColor: theme.up, wickDownColor: theme.down,
    });
  }, [props.settings]);

  // Push candle data.
  useEffect(() => {
    if (!series.current) return;
    series.current.setData(
      props.candles.map((c) => ({
        time: toSeconds(c.time_msc) as UTCTimestamp,
        open: c.o, high: c.h, low: c.l, close: c.c,
      })),
    );
  }, [props.candles]);

  useImperativeHandle(ref, () => ({
    jumpToNow: () => chart.current?.timeScale().scrollToRealTime(),
  }));

  return <div ref={el} className="w-full h-full" />;
});

export default CandleChart;
```

- [ ] **Step 3: Mount `CandleChart` + fill-poll UX in `Chart.tsx`**

Replace the chart placeholder `<div>` in `Chart.tsx` and wire `useChartData`. Add these imports and the data hook, then the chart column:

```typescript
import CandleChart from "../components/CandleChart";
import { useChartData } from "../hooks/useChartData";
```
Inside the component, after `currency`:
```typescript
  const data = useChartData(symbol, tf);
  const hasBars = data.candles.length > 0;
```
Replace the chart placeholder block with:
```typescript
        <div className="relative flex-1 min-h-0">
          {hasBars ? (
            <CandleChart
              ref={chartRef}
              symbol={symbol}
              tf={tf}
              settings={settings}
              candles={data.candles}
              lastBarMs={data.lastBarMs}
              onHover={setHovered}
              onNowVisibleChange={setNowVisible}
              onRequestOlder={data.loadOlder}
            />
          ) : (
            <div className="glass h-full flex items-center justify-center text-muted text-sm">
              {data.status === "loading" || data.status === "polling" ? (
                <span>⌛ Memuat data {symbol} {tf}…</span>
              ) : data.status === "gaveup" ? (
                <div className="text-center">
                  <div>Belum ada data ter-cache untuk rentang ini.</div>
                  <div className="mt-1">Jalankan <code>journal live</code> untuk mengisi cache.</div>
                  <button onClick={data.retry} className="glass mt-2 px-3 py-1 text-cyan">Coba lagi</button>
                </div>
              ) : (
                <span className="text-neg">Gagal memuat: {data.error}</span>
              )}
            </div>
          )}

          {/* Non-blocking banners while bars are already shown */}
          {hasBars && (data.status === "loading" || data.status === "polling") && (
            <div className="glass absolute top-2 left-2 px-2 py-1 text-[11px] text-muted">⌛ memuat data…</div>
          )}
          {hasBars && data.status === "gaveup" && (
            <div className="glass absolute top-2 left-2 px-2 py-1 text-[11px] text-muted flex items-center gap-2">
              <span>Data belum lengkap — jalankan <code>journal live</code>.</span>
              <button onClick={data.retry} className="text-cyan">Coba lagi</button>
            </div>
          )}
        </div>
```
Remove the old `chart — {symbol} {tf}` placeholder div. (Keep the info-panel `<aside>` placeholder for Task 8.)

- [ ] **Step 4: Build**

Run: `npm --prefix frontend run build`
Expected: 0 errors. If TypeScript flags the `addSeries`/`CandlestickSeries` API, reconcile with the installed lightweight-charts version (see the note in Step 2) — change only the series-creation line and its import.

- [ ] **Step 5: Manual verification**

Run `npm --prefix frontend run dev` + `uv run journal serve --db data/journal.db`. Open `/chart`:
- If cached candles exist for XAUUSDc/M5, they render; panning left triggers older loads (watch the network tab for widening `from`).
- With `journal live` NOT running and an uncached range: banner shows briefly, then the give-up hint + "Coba lagi".
- Toolbar "Ke sekarang" snaps to the latest bar.

Paste a one-line note of what you observed (bars rendered / give-up hint shown).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useChartData.ts frontend/src/components/CandleChart.tsx frontend/src/pages/Chart.tsx
git commit -m "feat(chart): candlestick render + lazy-load + bounded fill-poll UX

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Live overlay — SL/TP/entry price lines gated to "now"

**Files:**
- Modify: `frontend/src/components/CandleChart.tsx` (add the price-line effect)
- Modify: `frontend/src/pages/Chart.tsx` (pass `live` + `nowVisible` to `CandleChart`)

**Interfaces:**
- Consumes: `liveLines`, `LINE_COLORS`, `isNowVisible` (already imported/available), `LiveData`/`LivePosition` types, `ISeriesApi.createPriceLine` / `removePriceLine`.
- Produces: horizontal price lines per open position on the current symbol, drawn ONLY when `nowVisible` is true; cleared otherwise. Skips `null`/`0.0` (via `liveLines`).

- [ ] **Step 1: Pass live state into `CandleChart` from `Chart.tsx`**

Track `nowVisible` as real state (it is currently `[, setNowVisible]`). Change:
```typescript
  const [nowVisible, setNowVisible] = useState(false);
```
Add these props to the `<CandleChart … />` element:
```typescript
              live={live ?? null}
              nowVisible={nowVisible}
```

- [ ] **Step 2: Extend `CandleChart` props and draw the lines**

In `CandleChart.tsx`, add to the prop type:
```typescript
  live: import("../lib/types").LiveData | null;
  nowVisible: boolean;
```
Add imports:
```typescript
import { liveLines } from "../lib/candles";
import type { IPriceLine } from "lightweight-charts";
```
Add a ref for the current price lines near the other refs:
```typescript
  const priceLines = useRef<IPriceLine[]>([]);
```
Add this effect after the data effect:
```typescript
  // Live SL/TP/entry overlay — only when the current symbol has open positions
  // AND "now" is in view. Horizontal lines have no time, so they'd otherwise
  // hang over history where those levels never existed.
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];
    if (!props.nowVisible || !props.live || props.live.live.empty) return;
    const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
    for (const pos of mine) {
      for (const line of liveLines(pos)) {
        priceLines.current.push(
          s.createPriceLine({
            price: line.price,
            color: line.color,
            lineWidth: 1,
            lineStyle: 2,           // dashed
            axisLabelVisible: true,
            title: line.title,
          }),
        );
      }
    }
  }, [props.live, props.nowVisible, props.symbol]);
```

- [ ] **Step 3: Build**

Run: `npm --prefix frontend run build`
Expected: 0 errors. (`lineStyle: 2` is `LineStyle.Dashed`; if the installed types reject the numeric literal, import `LineStyle` and use `LineStyle.Dashed`.)

- [ ] **Step 4: Manual verification (needs a live position)**

With `journal live` running against the bridge and an open XAUUSDc position: on `/chart?symbol=XAUUSDc`, with the latest bar in view, dashed SL (red) / TP (green) / entry (grey) lines appear, skipping any unset (0.0) or unknown (null) value. Pan into history → lines vanish; "Ke sekarang" → they return. If no bridge is available, verify the negative path only: no lines when `live` is empty. Note which path you verified (the M9 live-bridge smoke is still pending a human run per docs/HANDOFF.md).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CandleChart.tsx frontend/src/pages/Chart.tsx
git commit -m "feat(chart): live SL/TP/entry overlay gated to now-in-view

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Info panel — crosshair OHLC, last+change, live block, meta

**Files:**
- Create: `frontend/src/components/ChartInfoPanel.tsx`
- Modify: `frontend/src/pages/Chart.tsx` (replace the `<aside>` placeholder)

**Interfaces:**
- Consumes: `price`, `money`, `wib` (format.ts), `LivePosition`/`LiveData`/`Candle`/`HoverBar` types, `Sym`/`Timeframe`.
- Produces: `ChartInfoPanel` component rendering four stacked blocks; falls back to the latest candle when nothing is hovered.

- [ ] **Step 1: Build the info panel**

Create `frontend/src/components/ChartInfoPanel.tsx`:

```typescript
import type { Sym, Timeframe } from "../lib/candles";
import type { Candle, HoverBar, LiveData } from "../lib/types";
import { money, price, wib } from "../lib/format";

function Row({ k, v, cls = "" }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex justify-between text-[12px]">
      <span className="text-muted">{k}</span>
      <span className={"num " + cls}>{v}</span>
    </div>
  );
}

export default function ChartInfoPanel({
  symbol, tf, candles, hovered, live, currency,
}: {
  symbol: Sym;
  tf: Timeframe;
  candles: Candle[];
  hovered: HoverBar | null;
  live: LiveData | null;
  currency: string;
}) {
  const latest = candles.length ? candles[candles.length - 1] : null;
  const bar = hovered ?? latest;
  const prev = candles.length >= 2 ? candles[candles.length - 2] : null;
  const change = latest && prev ? latest.c - prev.c : null;
  const changePct = latest && prev && prev.c !== 0 ? (latest.c - prev.c) / prev.c : null;
  const mine = live?.live.positions.filter((p) => p.symbol === symbol) ?? [];

  return (
    <div className="space-y-4">
      {/* 1 — crosshair OHLC (falls back to latest) */}
      <div>
        <div className="text-muted text-[11px] mb-1">
          {hovered ? "Bar (kursor)" : "Bar terakhir"}
        </div>
        {bar ? (
          <>
            <div className="text-[11px] text-muted mb-1">{wib(bar.time_msc, 0)}</div>
            <Row k="O" v={price(bar.o)} />
            <Row k="H" v={price(bar.h)} />
            <Row k="L" v={price(bar.l)} />
            <Row k="C" v={price(bar.c)} />
            {bar.v ? <Row k="V" v={String(bar.v)} /> : null}
          </>
        ) : (
          <div className="text-muted text-[12px]">—</div>
        )}
      </div>

      {/* 2 — last price + change (last-candle close, not a tick) */}
      <div>
        <div className="text-muted text-[11px] mb-1">Harga terakhir</div>
        <div className="num text-[20px]">{latest ? price(latest.c) : "—"}</div>
        {change !== null && (
          <div className={"text-[12px] num " + (change >= 0 ? "text-pos" : "text-neg")}>
            {change >= 0 ? "+" : ""}{price(change)}
            {changePct !== null ? ` (${(changePct * 100).toFixed(2)}%)` : ""}
          </div>
        )}
        <div className="text-[10px] text-muted mt-0.5">close bar terakhir · bukan tick live</div>
      </div>

      {/* 3 — live position block */}
      {mine.length > 0 && (
        <div>
          <div className="text-muted text-[11px] mb-1">Posisi live</div>
          {mine.map((p) => (
            <div key={p.position_id} className="glass p-2 mb-2 space-y-0.5">
              <div className="flex justify-between text-[12px]">
                <span className={p.direction === "buy" ? "text-pos" : "text-neg"}>
                  {p.direction.toUpperCase()} {p.volume}
                </span>
                <span className={"num " + ((p.profit ?? 0) >= 0 ? "text-pos" : "text-neg")}>
                  {money(p.profit, currency, { sign: true })}
                </span>
              </div>
              <Row k="entry" v={price(p.open_price)} />
              <Row k="now" v={price(p.price_current)} />
              <Row k="SL" v={price(p.sl)} cls="text-neg" />
              <Row k="TP" v={price(p.tp)} cls="text-pos" />
            </div>
          ))}
        </div>
      )}

      {/* 4 — symbol/tf meta */}
      <div className="text-[11px] text-muted border-t border-panel-border pt-2 space-y-0.5">
        <Row k="symbol" v={symbol} />
        <Row k="timeframe" v={tf} />
        <Row k="bars" v={String(candles.length)} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Mount it in `Chart.tsx`**

Add the import:
```typescript
import ChartInfoPanel from "../components/ChartInfoPanel";
```
Replace the `<aside>` placeholder with:
```typescript
        <aside className="glass w-[240px] shrink-0 p-3 hidden lg:block overflow-y-auto">
          <ChartInfoPanel
            symbol={symbol}
            tf={tf}
            candles={data.candles}
            hovered={hovered}
            live={live ?? null}
            currency={currency}
          />
        </aside>
```

- [ ] **Step 3: Build + full frontend suite**

Run: `npm --prefix frontend run build`
Expected: 0 errors.
Run: `npm --prefix frontend test`
Expected: all pass (no new tests here; the panel is a shell over tested formatters).

- [ ] **Step 4: Manual verification**

On `/chart`: hovering a candle updates the OHLC block (WIB time); moving off shows the latest bar; last price + change render; with a live position the block lists it; meta shows bar count growing as you pan.

- [ ] **Step 5: Commit + graph update**

```bash
git add frontend/src/components/ChartInfoPanel.tsx frontend/src/pages/Chart.tsx
git commit -m "feat(chart): info panel — crosshair OHLC, last+change, live, meta

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
graphify update .
```

---

## Final verification (run before opening the PR)

- [ ] `uv run pytest -q` — green; paste the summary line.
- [ ] `npm --prefix frontend test` — green.
- [ ] `npm --prefix frontend run build` — 0 errors.
- [ ] `uv run journal rebuild` — succeeds.
- [ ] `graphify update .` — graph refreshed.
- [ ] Manual `/chart` smoke: symbol/tf switch + URL restore, pan lazy-load, give-up hint without `journal live`, settings gear persists across reload.

## Self-Review (completed by plan author)

- **Spec coverage:** Part 0 §1–6 → Tasks 1–3. Page shell/routing/URL params → Task 5. lightweight-charts + data layer → Tasks 4, 6. Fill-poll UX → Task 6. Live overlay → Task 7. Info panel (4 blocks) → Task 8. Settings gear (persisted) → Tasks 4 (`chartPrefs`) + 5 (UI). Testing/DoD → each task + Final verification. All spec sections mapped.
- **Placeholder scan:** no TBD/TODO; every code step carries complete code; the only deferred-by-design detail (v4-vs-v5 series call, `LineStyle` literal) is called out explicitly with the exact fallback.
- **Type consistency:** `Timeframe`/`Sym`/`Candle`/`CandlesResponse`/`HoverBar`/`LiveData`/`LivePosition`/`ChartSettings`/`ChartHandle`/`ChartStatus` names are used identically across Tasks 4–8; `liveLines`/`isNowVisible`/`useChartData` signatures match their call sites.
