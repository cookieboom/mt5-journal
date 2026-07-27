# Centralized Storage Management (`/storage`) — Design Specification

**Date:** 2026-07-28  
**Status:** Approved by User  
**Target Route:** `/storage` in React Frontend & `/api/storage/*` in FastAPI Backend  

---

## 1. Executive Summary & Goal
The Centralized Storage Management page (`/storage`) provides a single control center for system data health, disk usage monitoring, candle data completeness & gap visualization, backfill downloads via MT5 Bridge, data export, and database/cache maintenance.

---

## 2. Architecture & Data Flow

### 2.1 Storage Subsystems
1. **SQLite Database (`journal.db`)**:
   - `candles`: M1 OHLC bars storage.
   - `candle_coverage`: Tracks contiguous cached ranges `(symbol, timeframe, from_msc, to_msc)`.
   - `candle_requests`: Background fetch queue processed by `journal live`.
   - `deals_raw`, `orders_raw`, `trades`: Transactional trade data and reconstructed trades.
   - `sl_tp_snapshots`, `app_prefs`: Trade poller snapshots & UI settings.
2. **File Cache (`cache/`)**:
   - Stores temporary rendered chart PNGs (`cache/chart_*.png`) and generated weekly reports (`cache/weekly_*.md`).

### 2.2 Data Operations Flow
- **Data Completeness & Gap Detection**: Backend reads `candle_coverage` for selected symbol & timeframe, computes gaps against the overall range `[min_from_msc, max_to_msc]` using `missing_ranges()`.
- **Backfill Fetching**: User selects symbol/tf & date range or clicks "Fill Gap". Backend inserts requests into `candle_requests`. Worker `journal live` picks pending requests and fetches missing candles from MT5 Bridge.
- **Data Exporting**: Backend queries `load_bars()`, resamples if tf != M1, and streams formatted CSV or JSON file response.
- **System Maintenance**: Endpoints trigger SQLite `VACUUM`/`PRAGMA optimize`, clear files in `cache/`, or run `reconstruct()` & `compute_auto_tags()`.

---

## 3. Backend API Specification (`/api/storage/*`)

### 3.1 Overview & System Health
- **`GET /api/storage/overview`**
  - **Returns**:
    ```json
    {
      "db_size_bytes": 15420000,
      "wal_size_bytes": 204800,
      "total_m1_bars": 1250000,
      "total_trades": 342,
      "cache_size_bytes": 4500000,
      "cache_files_count": 28,
      "symbols": ["XAUUSDc", "EURUSD", "BTCUSD"]
    }
    ```

### 3.2 System Maintenance Endpoints
- **`POST /api/storage/maintenance/clear-cache`**
  - Clears all files in `cache/` directory. Returns `{ "cleared_files": 28, "freed_bytes": 4500000 }`.
- **`POST /api/storage/maintenance/vacuum`**
  - Executes `VACUUM` and `PRAGMA optimize` on `journal.db`. Returns `{ "status": "ok", "db_size_after": 14200000 }`.
- **`POST /api/storage/maintenance/rebuild`**
  - Runs trade reconstruction & auto-tagging. Returns `{ "status": "ok", "trades_rebuilt": 342 }`.

### 3.3 Candle Completeness & Backfill Endpoints
- **`GET /api/storage/candles/completeness?symbol={symbol}&tf={tf}`**
  - **Returns**:
    ```json
    {
      "symbol": "XAUUSDc",
      "timeframe": "M1",
      "total_bars": 45000,
      "from_ms": 1740000000000,
      "to_ms": 1742000000000,
      "coverage_percent": 94.2,
      "covered_ranges": [{"from_ms": 1740000000000, "to_ms": 1740900000000}],
      "gaps": [{"from_ms": 1740900000000, "to_ms": 1741000000000, "duration_hours": 24.0}]
    }
    ```
- **`POST /api/storage/candles/fetch`**
  - **Body**: `{ "symbol": "XAUUSDc", "timeframe": "M1", "from_ms": 1740900000000, "to_ms": 1741000000000 }`
  - Enqueues `candle_requests`. Returns `{ "status": "queued", "request_id": 42 }`.
- **`POST /api/storage/candles/fill-gaps`**
  - **Body**: `{ "symbol": "XAUUSDc", "timeframe": "M1" }`
  - Enqueues requests for all detected gaps. Returns `{ "status": "queued", "requests_count": 3 }`.
- **`GET /api/storage/candles/export?symbol={symbol}&tf={tf}&from_ms={from_ms}&to_ms={to_ms}&format=csv|json`**
  - Returns raw or resampled candle data formatted as CSV file attachment or JSON object.
- **`POST /api/storage/candles/prune`**
  - **Body**: `{ "symbol": "XAUUSDc", "older_than_days": 180 }`
  - Deletes M1 candles older than specified days and updates `candle_coverage`. Returns `{ "status": "ok", "deleted_bars": 12000 }`.

---

## 4. Frontend UI Specification (`/storage`)

### 4.1 Navigation
- Link added to `Sidebar.tsx`: `{ to: "/storage", label: "Storage" }` below "Commands".
- Route added to `App.tsx`: `<Route path="/storage" element={<StoragePage />} />`.

### 4.2 Tab Layout
1. **Tab 1: Overview & Maintenance**:
   - Disk usage stat cards (DB size, WAL size, M1 candle count, Cache size).
   - Maintenance quick action panel with confirm modals:
     - `[Clear PNG & Report Cache]`
     - `[Vacuum SQLite DB]`
     - `[Rebuild Trades & Tags]`
2. **Tab 2: Completeness & Data Center**:
   - Symbol & Timeframe selectors (`XAUUSDc`, `EURUSD`, etc. / `M1`, `M5`, `H1`, etc.).
   - Summary bar: Coverage % badge, Total Bars count, Date Span text.
   - `CoverageVisualizer`: Interactive horizontal timeline bar showing green segments (data present) and red/orange segments (missing gaps).
   - `GapTable`: List of gaps with start/end time, duration, and 1-click `[Fill Gap]` button per row + `[Fill All Gaps]` header button.
   - `DataExportFetchPanel`: Form for custom date range backfill request to MT5 Bridge + `[Export CSV]` / `[Export JSON]` buttons.
3. **Tab 3: Retention & Pruning**:
   - Candle Pruner tool: Purge M1 candles older than X days (with red safety confirm modal).
   - Historical re-sync triggers & pending request queue status table.

---

## 5. Security, Safety & Error Handling
- **Destructive Actions Guard**: Action modals require secondary button confirmation before executing clear-cache, vacuum, or prune operations.
- **Concurrency Safety**: Database operations run with WAL mode and `busy_timeout=5000` to prevent lock conflicts with `journal live`.
- **Export Streaming**: Large data exports stream chunks or set cap limits to prevent FastAPI memory spikes.

---

## 6. Testing & Verification Plan
- **Backend Unit Tests**:
  - `tests/test_storage_api.py`: Test overview, clear-cache, vacuum, completeness payload, gap fill enqueueing, prune endpoint, export CSV/JSON.
- **Frontend Component & Integration Tests**:
  - React component smoke tests for `StoragePage`, `CoverageVisualizer`, `GapTable`.
- **System End-to-End Verification**:
  - Run `pytest` to ensure all existing & new tests pass.
  - Run `npm run build` in `frontend/` to ensure clean TypeScript compilation.
