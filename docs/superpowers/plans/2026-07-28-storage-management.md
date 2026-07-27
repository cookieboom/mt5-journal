# Storage Management (`/storage`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a centralized storage management control center (`/storage`) for system disk usage, candle data completeness & gap visualizer, historical backfill download via MT5 Bridge, data export, and SQLite DB / file cache maintenance.

**Architecture:** A set of new FastAPI endpoints (`/api/storage/*`) interfacing with `candles_store.py`, `journal.db`, and `cache/` folder, consumed by a React frontend page (`/storage`) featuring 3 interactive tabs (*Overview & Maintenance*, *Completeness & Data Center*, *Retention & Pruning*).

**Tech Stack:** Python 3.10+ (FastAPI, SQLite, pytest), React 18, TypeScript, Tailwind CSS, Vite.

## Global Constraints

- Backend endpoints must be pure or tested via pytest using isolated SQLite database instances.
- SQLite connections must use `WAL` mode and `busy_timeout=5000`.
- All timestamps across APIs must be epoch milliseconds UTC.
- Destructive operations (Clear Cache, Prune Candles, Vacuum DB) must be guarded with frontend modal confirmations.

---

### Task 1: Backend Storage Overview & Maintenance APIs

**Files:**
- Modify: `src/journal/web/app.py`
- Test: `tests/test_storage_api.py`

**Interfaces:**
- Consumes: `src/journal/web/app.py` (`create_app`), `src/journal/store/db.py` (`connect`)
- Produces: `GET /api/storage/overview`, `POST /api/storage/maintenance/clear-cache`, `POST /api/storage/maintenance/vacuum`, `POST /api/storage/maintenance/rebuild`

- [ ] **Step 1: Write failing test for overview & maintenance endpoints**

```python
# tests/test_storage_api.py
import pytest
from pathlib import Path
from journal.web.app import create_app

def test_storage_overview_returns_disk_stats(tmp_path):
    db_path = tmp_path / "journal.db"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "chart_1.png").write_bytes(b"fake png")

    app = create_app(db_path=str(db_path), cache_dir=str(cache_dir))
    with app.test_client() as client:
        res = client.get("/api/storage/overview")
        assert res.status_code == 200
        data = res.get_json()
        assert "db_size_bytes" in data
        assert "cache_size_bytes" in data
        assert data["cache_files_count"] == 1

def test_storage_clear_cache(tmp_path):
    db_path = tmp_path / "journal.db"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "chart_1.png").write_bytes(b"fake png")

    app = create_app(db_path=str(db_path), cache_dir=str(cache_dir))
    with app.test_client() as client:
        res = client.post("/api/storage/maintenance/clear-cache")
        assert res.status_code == 200
        assert res.get_json()["cleared_files"] == 1
        assert not (cache_dir / "chart_1.png").exists()
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_storage_api.py -v`
Expected: FAIL with route not found (404) or import error.

- [ ] **Step 3: Implement overview & maintenance routes in `app.py`**

Add `/api/storage/overview`, `/api/storage/maintenance/clear-cache`, `/api/storage/maintenance/vacuum`, `/api/storage/maintenance/rebuild` in `src/journal/web/app.py`.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_storage_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_storage_api.py src/journal/web/app.py
git commit -m "feat(backend): add storage overview and maintenance endpoints"
```

---

### Task 2: Backend Candle Completeness, Fetch & Gap Fill APIs

**Files:**
- Modify: `src/journal/web/app.py`
- Test: `tests/test_storage_api.py`

**Interfaces:**
- Consumes: `src/journal/store/candles_store.py` (`load_bars`, `insert_candle`, `record_coverage`, `missing_ranges`)
- Produces: `GET /api/storage/candles/completeness`, `POST /api/storage/candles/fetch`, `POST /api/storage/candles/fill-gaps`, `POST /api/storage/candles/prune`

- [ ] **Step 1: Write failing test for completeness & fetch endpoints**

```python
def test_storage_candles_completeness_and_fetch(tmp_path):
    db_path = tmp_path / "journal.db"
    app = create_app(db_path=str(db_path))

    with app.test_client() as client:
        # Seed M1 candle & coverage
        from journal.store.db import connect
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES ('XAUUSDc', 'M1', 1000, 5000)"
            )
            conn.execute(
                "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES ('XAUUSDc', 'M1', 8000, 10000)"
            )
            conn.commit()

        res = client.get("/api/storage/candles/completeness?symbol=XAUUSDc&tf=M1")
        assert res.status_code == 200
        data = res.get_json()
        assert data["symbol"] == "XAUUSDc"
        assert len(data["gaps"]) == 1
        assert data["gaps"][0]["from_ms"] == 5000
        assert data["gaps"][0]["to_ms"] == 8000

        # Post fetch request
        res = client.post("/api/storage/candles/fetch", json={
            "symbol": "XAUUSDc",
            "timeframe": "M1",
            "from_ms": 5000,
            "to_ms": 8000
        })
        assert res.status_code == 200
        assert res.get_json()["status"] == "queued"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_storage_api.py::test_storage_candles_completeness_and_fetch -v`
Expected: FAIL with 404 Not Found.

- [ ] **Step 3: Implement completeness, fetch, fill-gaps, and prune routes**

Implement routes in `src/journal/web/app.py`:
- `GET /api/storage/candles/completeness`
- `POST /api/storage/candles/fetch`
- `POST /api/storage/candles/fill-gaps`
- `POST /api/storage/candles/prune`

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_storage_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_storage_api.py src/journal/web/app.py
git commit -m "feat(backend): add candle completeness, gap fill, and prune endpoints"
```

---

### Task 3: Backend Candle Data Export API

**Files:**
- Modify: `src/journal/web/app.py`
- Test: `tests/test_storage_api.py`

**Interfaces:**
- Consumes: `src/journal/store/candles_store.py` (`load_bars`)
- Produces: `GET /api/storage/candles/export?symbol=...&tf=...&format=csv|json`

- [ ] **Step 1: Write failing test for export endpoint**

```python
def test_storage_candles_export_csv_and_json(tmp_path):
    db_path = tmp_path / "journal.db"
    app = create_app(db_path=str(db_path))

    from journal.store.db import connect
    from journal.store.candles_store import insert_candle
    with connect(db_path) as conn:
        insert_candle(conn, "XAUUSDc", 1000, 2000.0, 2005.0, 1995.0, 2002.0, 10.0)

    with app.test_client() as client:
        # Test JSON
        res_json = client.get("/api/storage/candles/export?symbol=XAUUSDc&tf=M1&format=json")
        assert res_json.status_code == 200
        assert len(res_json.get_json()["bars"]) == 1

        # Test CSV
        res_csv = client.get("/api/storage/candles/export?symbol=XAUUSDc&tf=M1&format=csv")
        assert res_csv.status_code == 200
        assert "time_msc,open,high,low,close,tick_volume" in res_csv.get_data(as_text=True)
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_storage_api.py::test_storage_candles_export_csv_and_json -v`
Expected: FAIL with 404.

- [ ] **Step 3: Implement export route in `app.py`**

Add `GET /api/storage/candles/export` returning JSON or CSV attachment response.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_storage_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_storage_api.py src/journal/web/app.py
git commit -m "feat(backend): add candle export CSV and JSON API endpoint"
```

---

### Task 4: Frontend Types, API Client & Navigation Integration

**Files:**
- Create: `frontend/src/lib/storageApi.ts`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `/api/storage/*` endpoints
- Produces: `storageApi` helper functions, `/storage` route in React Router.

- [ ] **Step 1: Write `frontend/src/lib/storageApi.ts`**

Define TypeScript interfaces:
`StorageOverview`, `CandleCompleteness`, `GapItem`, `CoveredRangeItem`.
Provide API caller functions:
`fetchStorageOverview()`, `clearCache()`, `vacuumDb()`, `rebuildTrades()`, `fetchCompleteness()`, `fetchBackfill()`, `fillAllGaps()`, `pruneCandles()`.

- [ ] **Step 2: Update Sidebar.tsx & App.tsx**

Add `{ to: "/storage", label: "Storage" }` link to `Sidebar.tsx`.
Add `<Route path="/storage" element={<StoragePage />} />` in `App.tsx`.

- [ ] **Step 3: Verify TypeScript compilation**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS (or placeholder StoragePage import).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/storageApi.ts frontend/src/components/Sidebar.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add storage API client and /storage navigation route"
```

---

### Task 5: Frontend Overview & Maintenance Tab Components

**Files:**
- Create: `frontend/src/components/storage/DiskStatsCard.tsx`
- Create: `frontend/src/components/storage/MaintenancePanel.tsx`

**Interfaces:**
- Consumes: `StorageOverview` type & maintenance functions from `storageApi.ts`.
- Produces: Stat cards for disk/cache/bar metrics and confirmation action modals.

- [ ] **Step 1: Create `DiskStatsCard.tsx`**

Stat card component displaying DB size, WAL size, M1 Candle bars count, Trade count, and Cache size formatted cleanly.

- [ ] **Step 2: Create `MaintenancePanel.tsx`**

Maintenance panel with action buttons: `Clear PNG Cache`, `Vacuum Database`, `Rebuild Trades & Auto-tags`, including confirmation dialog modals.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/storage/DiskStatsCard.tsx frontend/src/components/storage/MaintenancePanel.tsx
git commit -m "feat(frontend): build storage disk stats cards and maintenance panel"
```

---

### Task 6: Frontend Completeness & Data Center Tab Components

**Files:**
- Create: `frontend/src/components/storage/CoverageVisualizer.tsx`
- Create: `frontend/src/components/storage/GapTable.tsx`
- Create: `frontend/src/components/storage/DataExportFetchPanel.tsx`

**Interfaces:**
- Consumes: `CandleCompleteness` type and gap fill/export API functions.
- Produces: Visual timeline coverage bar, gaps table with 1-click fill buttons, export/backfill panel.

- [ ] **Step 1: Create `CoverageVisualizer.tsx`**

Horizontal canvas/DOM timeline bar displaying green covered blocks and red gap blocks relative to total time span `[from_ms, to_ms]`.

- [ ] **Step 2: Create `GapTable.tsx`**

Table displaying missing gaps, start/end dates, gap duration in hours, and 1-click `[Fill Gap]` button for each row + `[Fill All Gaps]` header button.

- [ ] **Step 3: Create `DataExportFetchPanel.tsx`**

Form to select custom date ranges for MT5 Bridge backfill request and download buttons for CSV / JSON export.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/storage/CoverageVisualizer.tsx frontend/src/components/storage/GapTable.tsx frontend/src/components/storage/DataExportFetchPanel.tsx
git commit -m "feat(frontend): build coverage visualizer, gap table, and data export/fetch panel"
```

---

### Task 7: Frontend Retention Tab & StoragePage Assembly

**Files:**
- Create: `frontend/src/components/storage/PrunePanel.tsx`
- Create: `frontend/src/pages/StoragePage.tsx`

**Interfaces:**
- Consumes: `storageApi.ts`, all storage tab components.
- Produces: Main `/storage` page with 3 tabs (*Overview & Maintenance*, *Completeness & Data Center*, *Retention & Pruning*).

- [ ] **Step 1: Create `PrunePanel.tsx`**

Pruning tool panel allowing users to purge M1 candle data older than X days with safety confirmation guard.

- [ ] **Step 2: Assemble `StoragePage.tsx`**

Create `StoragePage.tsx` assembling the 3 tabs, state management, and toast notifications.

- [ ] **Step 3: Run full backend and frontend build checks**

Run: `pytest`
Run: `cd frontend && npm run build`
Expected: ALL PASS with 0 build or test errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/storage/PrunePanel.tsx frontend/src/pages/StoragePage.tsx
git commit -m "feat(frontend): assemble StoragePage with 3 tabs and prune panel"
```
