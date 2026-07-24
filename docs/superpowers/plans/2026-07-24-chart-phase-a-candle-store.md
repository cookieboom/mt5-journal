# Chart Phase A — Smart Candle Store + JSON API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data foundation for the new TradingView-style chart segment: a unified candle store that serves any `(symbol, timeframe, from, to)` from the DB, fills missing ranges from the MT5 bridge through a queue that `journal live` drains, and exposes it as `GET /api/candles`.

**Architecture:** A pure aggregator (`resample.py`) + a pure-DB store (`candles_store.py`) + a bridge-touching fill engine (`ingest/candle_fill.py`) + a retry-safe request queue (`store/candle_queue.py`). The web enqueues requests and reads the DB; `journal live` fulfils one request per cycle. The legacy trade PNG renderer and per-trade candle ingest are unified onto the same store.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), FastAPI, typer, pytest. No new dependencies.

## Global Constraints

- **Never `import MetaTrader5` outside `src/journal/adapter/`** (Hard rule 1). The fill engine calls the `MT5Client` Protocol only. `web/` must never import the fill engine or construct a bridge client.
- **`web/` never touches the bridge** (M9 boundary). On-demand fill is queue-mediated.
- **All timestamps are epoch milliseconds, integer, server time** (Hard rule 3). No naive datetimes; convert to WIB only at display time.
- **`candles` bar `time_msc` is written straight through** — the ×1000 seconds→ms conversion already happened at the adapter boundary (Trap 15). A value below `10**12` is a leaked-seconds bug; raise, never convert.
- **Charts/candles are cache, not data** (Hard rule 6). Everything must be re-fetchable.
- **Money/prices are `REAL`**; compare with tolerance, never `==` (Hard rule 5).
- **Tests before implementation** for `domain/` and store logic (Hard rule 7).
- **Schema changes go through a migration file**, never edit `schema.sql` in place once data exists; the same DDL is mirrored into `schema.sql` for fresh DBs, and `test_migrations.py::test_migrated_db_matches_a_fresh_db` compares them.
- **Timeframes cross the Protocol as strings** from `TIMEFRAMES = ("M1","M5","M15","H1","H4","D1")` (`adapter/base.py`).
- **Definition of done:** all tests pass with pasted pytest output, and `journal rebuild` still succeeds.
- Run tests with `uv run pytest`.

---

## File Structure

```
src/journal/domain/resample.py         NEW  bucket_start + resample_m1 (pure)
src/journal/store/candles_store.py     NEW  pure DB: read/insert/coverage/missing_ranges/row_to_candle
src/journal/ingest/candle_fill.py      NEW  fill_range + fulfill_request (touches bridge)
src/journal/store/candle_queue.py      NEW  candle_requests queue ops (pure DB)
src/journal/store/migrations/003_candle_store.sql  NEW
src/journal/store/schema.sql           EDIT mirror the 003 DDL
src/journal/store/db.py                EDIT SCHEMA_VERSION 2 → 3
src/journal/web/api.py                 EDIT candles_payload
src/journal/web/app.py                 EDIT GET /api/candles
src/journal/ingest/live.py             EDIT live_cycle fulfils one request/cycle + LiveReport fields
src/journal/cli.py                     EDIT candles-warm / candles-coverage
src/journal/render/chart.py            EDIT read via candles_store.read_candles
src/journal/ingest/candles.py          EDIT insert + record coverage via store
```

**Module-placement refinement vs spec:** the spec listed `fulfill_request` under `candle_queue.py`. To keep layering clean (`store/` never imports `ingest/`), `fulfill_request` lives in `ingest/candle_fill.py` (it needs the bridge); `store/candle_queue.py` holds only pure-DB queue ops (`request_candles`, `claim_next_request`, `mark_done`, `mark_failed`, `requeue_orphaned`).

---

## Task 1: `resample.py` — pure M1 aggregation

**Files:**
- Create: `src/journal/domain/resample.py`
- Test: `tests/test_resample.py`

**Interfaces:**
- Consumes: `Candle` from `adapter/base.py`.
- Produces:
  - `bucket_start(time_msc: int, timeframe: str) -> int`
  - `resample_m1(m1: list[Candle], timeframe: str, covered: list[tuple[int, int]] | None = None) -> list[Candle]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resample.py
import pytest
from journal.adapter.base import Candle
from journal.domain.resample import bucket_start, resample_m1

M1 = 60_000

def _c(t, o, h, l, c, v=1):
    return Candle(time_msc=t, open=o, high=h, low=l, close=c,
                  tick_volume=v, spread=0, real_volume=v)

def test_bucket_start_aligns_to_server_time_utc():
    # 1970-01-01 00:00 UTC is epoch 0, so D1 buckets align to UTC midnight.
    assert bucket_start(0, "D1") == 0
    assert bucket_start(86_400_000 + 5, "D1") == 86_400_000
    assert bucket_start(300_500, "M5") == 300_000  # 5-min bucket

def test_bucket_start_rejects_unknown_timeframe():
    with pytest.raises(ValueError):
        bucket_start(0, "M3")

def test_resample_m1_to_m5_ohlc():
    bars = [_c(0, 10, 12, 9, 11), _c(M1, 11, 15, 8, 14),
            _c(2*M1, 14, 14, 13, 13), _c(3*M1, 13, 13, 10, 12),
            _c(4*M1, 12, 20, 12, 19)]
    out = resample_m1(bars, "M5")
    assert len(out) == 1
    b = out[0]
    assert b.time_msc == 0
    assert (b.open, b.high, b.low, b.close) == (10, 20, 8, 19)
    assert b.tick_volume == 5

def test_resample_m1_splits_across_buckets():
    bars = [_c(0, 1, 1, 1, 1), _c(5*M1, 2, 2, 2, 2)]
    out = resample_m1(bars, "M5")
    assert [b.time_msc for b in out] == [0, 300_000]

def test_resample_guard_omits_partially_covered_bucket():
    # Only the first 3 of 5 M1 bars in the 0..5m bucket are covered → omit it.
    bars = [_c(0, 1, 1, 1, 1), _c(M1, 1, 1, 1, 1), _c(2*M1, 1, 1, 1, 1)]
    covered = [(0, 2*M1)]  # covers opens 0,60000,120000 — not 180000/240000
    out = resample_m1(bars, "M5", covered=covered)
    assert out == []

def test_resample_guard_emits_fully_covered_bucket():
    bars = [_c(i*M1, 1, 1, 1, 1) for i in range(5)]
    covered = [(0, 4*M1)]  # covers every M1 open in the 0..5m bucket
    out = resample_m1(bars, "M5", covered=covered)
    assert len(out) == 1 and out[0].time_msc == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resample.py -v`
Expected: FAIL — `ModuleNotFoundError: journal.domain.resample`.

- [ ] **Step 3: Implement `resample.py`**

```python
# src/journal/domain/resample.py
"""Pure OHLC aggregation: M1 bars → any coarser timeframe.

No I/O. Bucket boundaries are computed in SERVER time (the stored `time_msc`);
because epoch 0 is 1970-01-01 00:00 UTC and this broker's server clock is UTC
(server_utc_offset_s = 0, re-measured each sync), modulo-bucketing aligns D1 to
UTC midnight and H4 to 00/04/08/12/16/20:00. Never assume the offset elsewhere.
"""
from __future__ import annotations

from ..adapter.base import Candle

_M1 = 60_000
_TF_MS = {
    "M1": _M1, "M5": 300_000, "M15": 900_000,
    "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000,
}


def bucket_start(time_msc: int, timeframe: str) -> int:
    if timeframe not in _TF_MS:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(_TF_MS)}")
    size = _TF_MS[timeframe]
    return time_msc - (time_msc % size)


def _bucket_fully_covered(bstart: int, size: int, covered: list[tuple[int, int]]) -> bool:
    # Every M1 open in [bstart, bstart+size) must sit inside one covered interval.
    # The last possible M1 open in the bucket is bstart + size - _M1.
    last_open = bstart + size - _M1
    return any(a <= bstart and b >= last_open for a, b in covered)


def resample_m1(
    m1: list[Candle],
    timeframe: str,
    covered: list[tuple[int, int]] | None = None,
) -> list[Candle]:
    """Aggregate M1 `Candle`s into `timeframe`. M1 → M1 is identity (sorted).

    When `covered` is given, a bucket is emitted only if its whole span is
    covered — a partially-covered bucket is dropped rather than emitted with a
    wrong high/low/close (the correctness guard).
    """
    if timeframe not in _TF_MS:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(_TF_MS)}")
    size = _TF_MS[timeframe]
    ordered = sorted(m1, key=lambda c: c.time_msc)
    if timeframe == "M1":
        return ordered

    groups: dict[int, list[Candle]] = {}
    for c in ordered:
        groups.setdefault(bucket_start(c.time_msc, timeframe), []).append(c)

    out: list[Candle] = []
    for bstart in sorted(groups):
        if covered is not None and not _bucket_fully_covered(bstart, size, covered):
            continue
        bars = groups[bstart]
        tv = sum((b.tick_volume or 0) for b in bars)
        rv = sum((b.real_volume or 0) for b in bars)
        out.append(
            Candle(
                time_msc=bstart,
                open=bars[0].open,
                high=max(b.high for b in bars),
                low=min(b.low for b in bars),
                close=bars[-1].close,
                tick_volume=tv or None,
                spread=None,          # charts don't need it; not meaningful post-merge
                real_volume=rv or None,
            )
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_resample.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/resample.py tests/test_resample.py
git commit -m "feat(candles): pure M1→TF resample with coverage correctness guard"
```

---

## Task 2: Migration 003 — `candle_coverage` + `candle_requests`

**Files:**
- Create: `src/journal/store/migrations/003_candle_store.sql`
- Modify: `src/journal/store/schema.sql` (append the same DDL)
- Modify: `src/journal/store/db.py` (`SCHEMA_VERSION = 2` → `3`)
- Modify: `tests/test_migrations.py` (version test + new-table tests)

**Interfaces:**
- Produces: tables `candle_coverage(symbol, timeframe, from_msc, to_msc)` and `candle_requests(id, symbol, timeframe, from_msc, to_msc, status, requested_msc, claimed_msc, completed_msc, bars_written, error)`; `SCHEMA_VERSION == 3`.

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_migrations.py`, change the version test and add table checks:

```python
def test_schema_version_is_3():
    """Phase A adds candle_coverage + candle_requests."""
    assert SCHEMA_VERSION == 3


def test_fresh_db_has_candle_store_tables(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"candle_coverage", "candle_requests"} <= names
    finally:
        conn.close()
```

Delete the old `test_schema_version_is_2` (replaced by `_is_3`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL — `SCHEMA_VERSION == 2`, tables missing.

- [ ] **Step 3: Create the migration file**

```sql
-- src/journal/store/migrations/003_candle_store.sql
-- Migration 003 — Phase A smart candle store.
--
-- Brings a v2 database forward to v3. ADDITIVE only: two new tables, no existing
-- table touched. The same DDL lives in schema.sql for fresh databases; the two
-- must stay in lockstep (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).

-- Which [from_msc, to_msc] ranges have actually been FETCHED, per (symbol,
-- timeframe). This is how "empty because market closed" (fetched, no bars) is
-- told apart from "empty because never fetched" (must fetch). Ranges are merged
-- on insert into a minimal disjoint set. Bar-open ms, server time. Inclusive.
CREATE TABLE IF NOT EXISTS candle_coverage (
    symbol     TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    from_msc   INTEGER NOT NULL,
    to_msc     INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe, from_msc)
);

-- The on-demand fill queue. The web INSERTs a 'pending' row and never talks to
-- the bridge; `journal live` claims and fulfils it. UNLIKE trade_commands this
-- is idempotent and retry-safe: an orphaned 'claimed' row is re-queued, never
-- failed. No money, no position — refetching candles is always safe.
CREATE TABLE IF NOT EXISTS candle_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    from_msc      INTEGER NOT NULL,
    to_msc        INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|done|failed
    requested_msc INTEGER NOT NULL,
    claimed_msc   INTEGER,
    completed_msc INTEGER,
    bars_written  INTEGER,
    error         TEXT
);
```

- [ ] **Step 4: Mirror the DDL into `schema.sql`**

Append the exact same two `CREATE TABLE IF NOT EXISTS` statements (identical column definitions) to `src/journal/store/schema.sql`, in the candles area of the file.

- [ ] **Step 5: Bump the version**

In `src/journal/store/db.py`, change `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3`.

- [ ] **Step 6: Run migration + full suite**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS, including `test_migrated_db_matches_a_fresh_db`.

- [ ] **Step 7: Commit**

```bash
git add src/journal/store/migrations/003_candle_store.sql src/journal/store/schema.sql src/journal/store/db.py tests/test_migrations.py
git commit -m "feat(store): migration 003 — candle_coverage + candle_requests"
```

---

## Task 3: `candles_store.py` — pure DB read/insert/coverage

**Files:**
- Create: `src/journal/store/candles_store.py`
- Test: `tests/test_candles_store.py`

**Interfaces:**
- Consumes: `Candle` from `adapter/base.py`; the schema from Task 2.
- Produces:
  - `insert_candle(conn, symbol, timeframe, c: Candle) -> int`
  - `read_candles(conn, symbol, timeframe, from_ms, to_ms) -> list[sqlite3.Row]`
  - `row_to_candle(r) -> Candle`
  - `read_coverage(conn, symbol, timeframe) -> list[tuple[int, int]]`
  - `record_coverage(conn, symbol, timeframe, from_ms, to_ms) -> None`  (caller commits)
  - `missing_ranges(covered: list[tuple[int, int]], want: tuple[int, int]) -> list[tuple[int, int]]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_candles_store.py
import pytest
from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candles_store as cs

M1 = 60_000

def _conn(tmp_path):
    return connect(tmp_path / "t.db")

def _c(t, o=1.0, h=2.0, l=0.5, c=1.5, v=3):
    return Candle(time_msc=t, open=o, high=h, low=l, close=c,
                  tick_volume=v, spread=1, real_volume=v)

def test_missing_ranges_full_when_no_coverage():
    assert cs.missing_ranges([], (0, 100)) == [(0, 100)]

def test_missing_ranges_subtracts_middle():
    assert cs.missing_ranges([(20, 40)], (0, 100)) == [(0, 19), (41, 100)]

def test_missing_ranges_empty_when_fully_covered():
    assert cs.missing_ranges([(0, 100)], (10, 90)) == []

def test_insert_and_read_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    assert cs.insert_candle(conn, "XAUUSDc", "M1", _c(2*M1)) == 1
    assert cs.insert_candle(conn, "XAUUSDc", "M1", _c(2*M1)) == 0  # PK dedupe
    rows = cs.read_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    assert [r["time_msc"] for r in rows] == [2*M1]

def test_insert_rejects_seconds_leak(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        cs.insert_candle(conn, "XAUUSDc", "M1", _c(1_700_000))  # seconds, < 1e12

def test_record_coverage_merges_touching(tmp_path):
    conn = _conn(tmp_path)
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 100)
    cs.record_coverage(conn, "XAUUSDc", "M1", 101, 200)   # touches (gap 1)
    cs.record_coverage(conn, "XAUUSDc", "M1", 500, 600)   # disjoint
    conn.commit()
    assert cs.read_coverage(conn, "XAUUSDc", "M1") == [(0, 200), (500, 600)]

def test_row_to_candle(tmp_path):
    conn = _conn(tmp_path)
    cs.insert_candle(conn, "XAUUSDc", "M1", _c(2*M1, o=1.1))
    r = cs.read_candles(conn, "XAUUSDc", "M1", 0, 3*M1)[0]
    c = cs.row_to_candle(r)
    assert c.time_msc == 2*M1 and c.open == 1.1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_candles_store.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `candles_store.py`**

```python
# src/journal/store/candles_store.py
"""The pure-DB candle store: read bars, insert bars, track coverage. NO bridge,
NO MT5 — safe to import from web/ and render/. The bridge-touching fill lives in
ingest/candle_fill.py.

Coverage is a minimal set of disjoint inclusive [from_msc, to_msc] ranges per
(symbol, timeframe): what has actually been fetched, so a genuinely-empty range
(market closed) is remembered and never re-fetched.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import Candle

# Below this, a bar time_msc is SECONDS that leaked through the adapter boundary
# (Trap 15), never a value to convert here. Same tripwire as ingest/candles.py.
_MSC_FLOOR = 10**12


def insert_candle(conn: sqlite3.Connection, symbol: str, timeframe: str, c: Candle) -> int:
    """INSERT OR IGNORE one bar. `time_msc` is written straight through — the
    ×1000 already happened at the adapter boundary. Returns 1 if newly inserted,
    0 if the PK (symbol, timeframe, time_msc) already existed."""
    if c.time_msc is None or c.time_msc < _MSC_FLOOR:
        raise ValueError(
            f"candle time_msc={c.time_msc!r} for {symbol} {timeframe} is below "
            f"{_MSC_FLOOR} — looks like SECONDS leaked through (Trap 15). Fix the "
            "adapter boundary, not this module; it must never do its own ×1000."
        )
    cur = conn.execute(
        "INSERT OR IGNORE INTO candles "
        "(symbol, timeframe, time_msc, open, high, low, close, tick_volume, spread, real_volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, timeframe, c.time_msc, c.open, c.high, c.low, c.close,
         c.tick_volume, c.spread, c.real_volume),
    )
    return cur.rowcount


def read_candles(conn: sqlite3.Connection, symbol: str, timeframe: str,
                 from_ms: int, to_ms: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT time_msc, open, high, low, close, tick_volume, spread, real_volume "
        "FROM candles WHERE symbol = ? AND timeframe = ? AND time_msc BETWEEN ? AND ? "
        "ORDER BY time_msc",
        (symbol, timeframe, from_ms, to_ms),
    ).fetchall()


def row_to_candle(r: sqlite3.Row) -> Candle:
    return Candle(
        time_msc=r["time_msc"], open=r["open"], high=r["high"], low=r["low"],
        close=r["close"], tick_volume=r["tick_volume"], spread=r["spread"],
        real_volume=r["real_volume"],
    )


def read_coverage(conn: sqlite3.Connection, symbol: str, timeframe: str) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT from_msc, to_msc FROM candle_coverage "
        "WHERE symbol = ? AND timeframe = ? ORDER BY from_msc",
        (symbol, timeframe),
    ).fetchall()
    return [(int(r["from_msc"]), int(r["to_msc"])) for r in rows]


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1:              # overlap or adjacent (gap ≤ 1ms)
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def record_coverage(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int) -> None:
    """Merge [from_ms, to_ms] into stored coverage and rewrite the disjoint set.
    Caller commits (fill_range / ingest do one commit)."""
    merged = _merge(read_coverage(conn, symbol, timeframe) + [(from_ms, to_ms)])
    conn.execute("DELETE FROM candle_coverage WHERE symbol = ? AND timeframe = ?", (symbol, timeframe))
    conn.executemany(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) VALUES (?, ?, ?, ?)",
        [(symbol, timeframe, a, b) for a, b in merged],
    )


def missing_ranges(covered: list[tuple[int, int]], want: tuple[int, int]) -> list[tuple[int, int]]:
    """Inclusive integer ranges in `want` not covered by `covered`."""
    lo, hi = want
    if lo > hi:
        return []
    result: list[tuple[int, int]] = []
    cursor = lo
    for a, b in sorted(covered):
        if b < cursor:
            continue
        if a > hi:
            break
        if a > cursor:
            result.append((cursor, min(a - 1, hi)))
        cursor = max(cursor, b + 1)
        if cursor > hi:
            break
    if cursor <= hi:
        result.append((cursor, hi))
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_candles_store.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/candles_store.py tests/test_candles_store.py
git commit -m "feat(store): pure-DB candle store — read/insert/coverage/missing_ranges"
```

---

## Task 4: `candle_fill.py` — bridge fetch engine

**Files:**
- Create: `src/journal/ingest/candle_fill.py`
- Test: `tests/test_candle_fill.py`

**Interfaces:**
- Consumes: `candles_store` (Task 3); the `MT5Client` Protocol's `copy_rates_range`.
- Produces: `fill_range(client, conn, symbol, timeframe, from_ms, to_ms) -> int` (bars_new).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_candle_fill.py
from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candles_store as cs
from journal.ingest.candle_fill import fill_range

M1 = 60_000

class FakeRates:
    """Minimal MT5Client stub: scripts copy_rates_range and counts calls.
    (Same local-fake pattern as tests/test_poller.py::FakePositionsClient.)"""
    def __init__(self, bars_by_range):
        self.bars_by_range = bars_by_range   # {(from_ms, to_ms): [Candle,...]}
        self.calls = []
    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        f = int(date_from.timestamp() * 1000)
        t = int(date_to.timestamp() * 1000)
        self.calls.append((symbol, timeframe, f, t))
        return self.bars_by_range.get((f, t), [])

def _c(t):
    return Candle(time_msc=t, open=1, high=2, low=0.5, close=1.5,
                  tick_volume=1, spread=1, real_volume=1)

def test_fill_fetches_gap_inserts_and_records_coverage(tmp_path):
    conn = connect(tmp_path / "t.db")
    client = FakeRates({(0, 3*M1): [_c(M1), _c(2*M1)]})
    n = fill_range(client, conn, "XAUUSDc", "M1", 0, 3*M1)
    assert n == 2
    assert [r["time_msc"] for r in cs.read_candles(conn, "XAUUSDc", "M1", 0, 3*M1)] == [M1, 2*M1]
    assert cs.read_coverage(conn, "XAUUSDc", "M1") == [(0, 3*M1)]

def test_fill_records_coverage_for_empty_range(tmp_path):
    conn = connect(tmp_path / "t.db")
    client = FakeRates({})  # market closed: no bars
    n = fill_range(client, conn, "XAUUSDc", "M1", 0, 3*M1)
    assert n == 0
    assert cs.read_coverage(conn, "XAUUSDc", "M1") == [(0, 3*M1)]  # remembered as fetched

def test_fill_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    client = FakeRates({(0, 3*M1): [_c(M1)]})
    fill_range(client, conn, "XAUUSDc", "M1", 0, 3*M1)
    client.calls.clear()
    n2 = fill_range(client, conn, "XAUUSDc", "M1", 0, 3*M1)  # already covered
    assert n2 == 0
    assert client.calls == []   # no gap → no bridge call
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_candle_fill.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `candle_fill.py` (fill_range only)**

```python
# src/journal/ingest/candle_fill.py
"""The one module that fetches candles from the bridge. Used by `journal live`
(to drain the request queue) and by `journal candles-warm`. NEVER imported by
web/ — that is what keeps the M9 bridge boundary intact.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..adapter.base import MT5Client
from ..store import candles_store as cs


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def fill_range(client: MT5Client, conn: sqlite3.Connection, symbol: str,
               timeframe: str, from_ms: int, to_ms: int) -> int:
    """Fetch only the UNCOVERED sub-ranges of [from_ms, to_ms] from the bridge,
    insert the bars, and record coverage (even for ranges that return zero bars,
    so a genuinely-empty span is never re-fetched). Idempotent. Returns bars_new.
    One commit at the end."""
    covered = cs.read_coverage(conn, symbol, timeframe)
    bars_new = 0
    for lo, hi in cs.missing_ranges(covered, (from_ms, to_ms)):
        bars = client.copy_rates_range(symbol, timeframe, _ms_to_dt(lo), _ms_to_dt(hi))
        for c in bars:
            bars_new += cs.insert_candle(conn, symbol, timeframe, c)
        cs.record_coverage(conn, symbol, timeframe, lo, hi)
    conn.commit()
    return bars_new
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_candle_fill.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/journal/ingest/candle_fill.py tests/test_candle_fill.py
git commit -m "feat(candles): fill_range — gap-only bridge fetch + coverage"
```

---

## Task 5: `candle_queue.py` + `fulfill_request`

**Files:**
- Create: `src/journal/store/candle_queue.py`
- Modify: `src/journal/ingest/candle_fill.py` (add `fulfill_request`)
- Test: `tests/test_candle_queue.py`

**Interfaces:**
- Consumes: `candles_store`, `candle_fill.fill_range`, `store.db.now_ms`.
- Produces:
  - `request_candles(conn, symbol, timeframe, from_ms, to_ms) -> int`  (0 = already covered, else request id)
  - `claim_next_request(conn) -> sqlite3.Row | None`
  - `mark_done(conn, req_id, bars) -> None` / `mark_failed(conn, req_id, error) -> None`
  - `requeue_orphaned(conn) -> int`
  - `fulfill_request(client, conn, req) -> int`  (in `candle_fill.py`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_candle_queue.py
from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candle_queue as q
from journal.store import candles_store as cs
from journal.ingest.candle_fill import fulfill_request

M1 = 60_000

class FakeRates:
    def __init__(self, bars): self.bars = bars
    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        return self.bars

def _c(t):
    return Candle(time_msc=t, open=1, high=2, low=0.5, close=1.5,
                  tick_volume=1, spread=1, real_volume=1)

def test_request_dedupes_identical_pending(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    b = q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    assert a == b and a > 0

def test_request_returns_zero_when_already_covered(tmp_path):
    conn = connect(tmp_path / "t.db")
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 3*M1); conn.commit()
    assert q.request_candles(conn, "XAUUSDc", "M1", M1, 2*M1) == 0

def test_claim_marks_claimed_once(tmp_path):
    conn = connect(tmp_path / "t.db")
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    r1 = q.claim_next_request(conn)
    assert r1 is not None and r1["status"] == "claimed"
    assert q.claim_next_request(conn) is None   # nothing left pending

def test_fulfill_fills_and_marks_done(tmp_path):
    conn = connect(tmp_path / "t.db")
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    req = q.claim_next_request(conn)
    bars = fulfill_request(FakeRates([_c(M1)]), conn, req)
    assert bars == 1
    row = conn.execute("SELECT status, bars_written FROM candle_requests WHERE id=?", (req["id"],)).fetchone()
    assert row["status"] == "done" and row["bars_written"] == 1

def test_requeue_orphaned_resets_claimed(tmp_path):
    conn = connect(tmp_path / "t.db")
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    q.claim_next_request(conn)
    assert q.requeue_orphaned(conn) == 1
    assert q.claim_next_request(conn) is not None   # pending again
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_candle_queue.py -v`
Expected: FAIL — modules/functions missing.

- [ ] **Step 3: Implement `candle_queue.py`**

```python
# src/journal/store/candle_queue.py
"""The candle_requests queue — pure DB. The web INSERTs (request_candles);
`journal live` drains (claim/fulfil/mark). Idempotent and retry-safe: an
orphaned 'claimed' row is re-queued, never failed (candles carry no money).
"""
from __future__ import annotations

import sqlite3

from .db import now_ms
from . import candles_store as cs


def request_candles(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int) -> int:
    """Enqueue a fill unless the range is already covered or an identical request
    is already pending/claimed. Returns 0 when nothing was queued (already
    covered), else the (new or existing) request id."""
    if not cs.missing_ranges(cs.read_coverage(conn, symbol, timeframe), (from_ms, to_ms)):
        return 0
    row = conn.execute(
        "SELECT id FROM candle_requests WHERE symbol = ? AND timeframe = ? "
        "AND from_msc = ? AND to_msc = ? AND status IN ('pending', 'claimed') LIMIT 1",
        (symbol, timeframe, from_ms, to_ms),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO candle_requests (symbol, timeframe, from_msc, to_msc, status, requested_msc) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (symbol, timeframe, from_ms, to_ms, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def claim_next_request(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Take the oldest pending request. The `WHERE status='pending'` + rowcount
    check is the lock (same shape as execute.claim_next)."""
    row = conn.execute(
        "SELECT id FROM candle_requests WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE candle_requests SET status = 'claimed', claimed_msc = ? WHERE id = ? AND status = 'pending'",
        (now_ms(), row["id"]),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    return conn.execute("SELECT * FROM candle_requests WHERE id = ?", (row["id"],)).fetchone()


def mark_done(conn: sqlite3.Connection, req_id: int, bars: int) -> None:
    conn.execute(
        "UPDATE candle_requests SET status = 'done', completed_msc = ?, bars_written = ? WHERE id = ?",
        (now_ms(), bars, req_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, req_id: int, error: str) -> None:
    conn.execute(
        "UPDATE candle_requests SET status = 'failed', completed_msc = ?, error = ? WHERE id = ?",
        (now_ms(), error, req_id),
    )
    conn.commit()


def requeue_orphaned(conn: sqlite3.Connection) -> int:
    """On `journal live` startup, reset any 'claimed' row (a crash mid-fetch) back
    to 'pending'. Safe because refetching candles is idempotent — the opposite of
    trade_commands, which must NEVER auto-retry."""
    cur = conn.execute(
        "UPDATE candle_requests SET status = 'pending', claimed_msc = NULL WHERE status = 'claimed'"
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Add `fulfill_request` to `candle_fill.py`**

Append to `src/journal/ingest/candle_fill.py`:

```python
from ..store import candle_queue as _queue


def fulfill_request(client: MT5Client, conn: sqlite3.Connection, req: sqlite3.Row) -> int:
    """Run a claimed request through fill_range; mark done (or failed + re-raise).
    Returns bars_new."""
    try:
        bars = fill_range(client, conn, req["symbol"], req["timeframe"],
                          req["from_msc"], req["to_msc"])
    except Exception as e:
        _queue.mark_failed(conn, int(req["id"]), str(e))
        raise
    _queue.mark_done(conn, int(req["id"]), bars)
    return bars
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_candle_queue.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/journal/store/candle_queue.py src/journal/ingest/candle_fill.py tests/test_candle_queue.py
git commit -m "feat(candles): candle_requests queue + fulfill_request (retry-safe)"
```

---

## Task 6: `journal live` fulfils one request per cycle

**Files:**
- Modify: `src/journal/ingest/live.py` (`live_cycle`, `LiveReport`, startup requeue)
- Test: `tests/test_live.py` (add a candle-fulfilment case)

**Interfaces:**
- Consumes: `candle_queue.claim_next_request/requeue_orphaned`, `candle_fill.fulfill_request`.
- Produces: `LiveReport.candle_request_id: int | None`, `LiveReport.candle_bars_written: int | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_live.py` (reuse the file's existing fake client + `connect` helpers; the fake's `copy_rates_range` returns candles for the requested range — extend the local fake if needed to return one bar):

```python
def test_live_cycle_fulfils_one_candle_request(tmp_path):
    from journal.store import candle_queue as q
    from journal.adapter.base import Candle
    conn = connect(tmp_path / "t.db")
    _seed_account(conn)                      # existing helper in this test module
    M1 = 60_000
    # a fake client whose copy_rates_range yields one bar in-range
    client = _FakeLiveClientWithRates(bar=Candle(
        time_msc=M1, open=1, high=2, low=0.5, close=1.5,
        tick_volume=1, spread=1, real_volume=1))
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    report = live_cycle(client, conn, login=_LOGIN, trading=True)
    assert report.candle_request_id is not None
    assert report.candle_bars_written == 1
    row = conn.execute("SELECT status FROM candle_requests WHERE id=?",
                       (report.candle_request_id,)).fetchone()
    assert row["status"] == "done"
```

(If the existing `FakeLiveClient` already implements `copy_rates_range`, script it to return `[bar]` and drop `_FakeLiveClientWithRates`. Match whatever fake `test_live.py` already defines — do not introduce a second parallel fake if one exists.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_live.py -k candle_request -v`
Expected: FAIL — `LiveReport` has no `candle_request_id`.

- [ ] **Step 3: Extend `LiveReport`**

Add two fields (both defaulting to `None`) to the `LiveReport` dataclass in `src/journal/ingest/live.py`:

```python
    candle_request_id: int | None = None
    candle_bars_written: int | None = None
```

- [ ] **Step 4: Fulfil one request in `live_cycle`**

At the top of `src/journal/ingest/live.py`, add imports:

```python
from ..store.candle_queue import claim_next_request, requeue_orphaned
from .candle_fill import fulfill_request
```

In `live_cycle`, after the existing command block (`if trading: ... _execute_one_command(...)`) and before building `LiveReport`, insert:

```python
    # (4) one candle request per cycle — same one-per-cycle discipline as
    # commands, so a big backfill can never starve the position heartbeat. This
    # is the ONLY place a browser-triggered candle fetch reaches the bridge.
    candle_request_id: int | None = None
    candle_bars_written: int | None = None
    req = claim_next_request(conn)
    if req is not None:
        candle_request_id = int(req["id"])
        try:
            candle_bars_written = fulfill_request(client, conn, req)
        except Exception:
            log.exception(
                "live: candle request %d failed — marked failed, will not auto-retry "
                "this exact row (a new request re-queues)", candle_request_id
            )
```

Then add both to the `LiveReport(...)` constructor call:

```python
        candle_request_id=candle_request_id,
        candle_bars_written=candle_bars_written,
```

- [ ] **Step 5: Requeue orphans at `live` startup**

Find where `recover_interrupted(` is called at `journal live` startup:

Run: `grep -rn "recover_interrupted(" src/journal`

Immediately after that call, add `requeue_orphaned(conn)` (import it there too if that call is in a different module than `live_cycle`). This resets any request left `claimed` by a crash back to `pending`.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS, including the new candle-fulfilment test.

- [ ] **Step 7: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "feat(live): fulfil one candle request per cycle + requeue orphans"
```

---

## Task 7: `GET /api/candles` — DB read + enqueue

**Files:**
- Modify: `src/journal/web/api.py` (`candles_payload`)
- Modify: `src/journal/web/app.py` (route)
- Test: `tests/test_api.py` and `tests/test_web.py`

**Interfaces:**
- Consumes: `candles_store`, `candle_queue.request_candles`, `resample.resample_m1`, `TIMEFRAMES`.
- Produces: `api.candles_payload(conn, symbol, timeframe, from_ms, to_ms, *, max_bars=5000) -> dict`; route `GET /api/candles`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py  (add)
from journal.web import api
from journal.store.db import connect
from journal.store import candles_store as cs
from journal.adapter.base import Candle

M1 = 60_000

def _c(t):
    return Candle(time_msc=t, open=1, high=2, low=0.5, close=1.5,
                  tick_volume=3, spread=1, real_volume=3)

def test_candles_payload_serves_native_and_no_pending(tmp_path):
    conn = connect(tmp_path / "t.db")
    cs.insert_candle(conn, "XAUUSDc", "M1", _c(M1))
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 3*M1); conn.commit()
    p = api.candles_payload(conn, "XAUUSDc", "M1", 0, 3*M1)
    assert p["candles"] == [{"time_msc": M1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3}]
    assert p["missing"] == [] and p["pending"] is False

def test_candles_payload_enqueues_when_uncovered(tmp_path):
    conn = connect(tmp_path / "t.db")
    p = api.candles_payload(conn, "XAUUSDc", "M1", 0, 3*M1)
    assert p["candles"] == []
    assert p["missing"] == [[0, 3*M1]] and p["pending"] is True
    # the fill was queued, NOT executed (no bridge in web)
    n = conn.execute("SELECT count(*) FROM candle_requests WHERE status='pending'").fetchone()[0]
    assert n == 1

def test_candles_payload_rejects_unknown_timeframe(tmp_path):
    conn = connect(tmp_path / "t.db")
    import pytest
    with pytest.raises(ValueError):
        api.candles_payload(conn, "XAUUSDc", "M3", 0, 3*M1)
```

```python
# tests/test_web.py  (add — mirror the existing TestClient setup in this file)
def test_api_candles_route_returns_200_with_missing(client):
    # `client` = the existing TestClient fixture/factory used elsewhere in this file
    r = client.get("/api/candles", params={"symbol": "XAUUSDc", "timeframe": "M1",
                                           "from": 0, "to": 180000})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "XAUUSDc" and "candles" in body and body["pending"] is True

def test_api_candles_route_400_on_bad_timeframe(client):
    r = client.get("/api/candles", params={"symbol": "XAUUSDc", "timeframe": "M3",
                                           "from": 0, "to": 180000})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k candles tests/test_web.py -k candles -v`
Expected: FAIL — `candles_payload` / route missing.

- [ ] **Step 3: Implement `candles_payload` in `api.py`**

Add near the other payload builders in `src/journal/web/api.py` (add imports at top: `from ..adapter.base import TIMEFRAMES`, `from ..store import candles_store as cs`, `from ..store import candle_queue`, `from ..domain.resample import resample_m1`):

```python
def candles_payload(
    conn: sqlite3.Connection, symbol: str, timeframe: str,
    from_ms: int, to_ms: int, *, max_bars: int = 5000,
) -> dict:
    """Serve candles from the DB (native, else aggregated from M1). NEVER touches
    the bridge: if a range is uncovered it ENQUEUES a fill (deduped) for
    `journal live` to drain, and reports `missing`/`pending` so the client polls.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")

    native = cs.read_candles(conn, symbol, timeframe, from_ms, to_ms)
    if native:
        bars = [cs.row_to_candle(r) for r in native]
    elif timeframe != "M1":
        m1 = cs.read_candles(conn, symbol, "M1", from_ms, to_ms)
        bars = (
            resample_m1([cs.row_to_candle(r) for r in m1], timeframe,
                        covered=cs.read_coverage(conn, symbol, "M1"))
            if m1 else []
        )
    else:
        bars = []

    if len(bars) > max_bars:
        bars = bars[-max_bars:]

    missing = cs.missing_ranges(cs.read_coverage(conn, symbol, timeframe), (from_ms, to_ms))
    pending = False
    if missing:
        candle_queue.request_candles(conn, symbol, timeframe, from_ms, to_ms)
        pending = True

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {"time_msc": b.time_msc, "o": b.open, "h": b.high,
             "l": b.low, "c": b.close, "v": b.tick_volume}
            for b in bars
        ],
        "missing": [[lo, hi] for lo, hi in missing],
        "pending": pending,
    }
```

- [ ] **Step 4: Add the route in `app.py`**

Add `Query` to the existing `fastapi` import, then register alongside the other `/api/*` GET routes (before the SPA catch-all):

```python
    @app.get("/api/candles")
    def api_candles(
        symbol: str,
        timeframe: str,
        from_ms: int = Query(..., alias="from"),
        to_ms: int = Query(..., alias="to"),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Read-only candle feed for the chart. Serves from the DB and enqueues a
        fill for any uncovered range (never talks to the bridge — M9 boundary).
        A bad symbol/timeframe is a 400; missing/non-integer from/to yield
        FastAPI's own 422."""
        try:
            payload = api.candles_payload(conn, symbol, timeframe, from_ms, to_ms)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(payload)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api.py -k candles tests/test_web.py -k candles -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py tests/test_web.py
git commit -m "feat(web): GET /api/candles — DB read + queue-mediated fill"
```

---

## Task 8: CLI — `candles-warm` and `candles-coverage`

**Files:**
- Modify: `src/journal/cli.py`
- Test: `tests/test_cli.py` (if it exists; else assert via a smoke import — see Step 1)

**Interfaces:**
- Consumes: `candle_fill.fill_range`, `candles_store.read_coverage`, `connect`, `LiveMT5Client`.
- Produces: `journal candles-warm <symbol> <timeframe> --from <ms> --to <ms>`, `journal candles-coverage [--symbol S]`.

- [ ] **Step 1: Write a failing CLI test**

Add `tests/test_cli.py` (or extend it) — `candles-coverage` needs no bridge, so it is the testable one via `typer.testing.CliRunner`:

```python
# tests/test_cli.py  (add)
from typer.testing import CliRunner
from journal.cli import app
from journal.store.db import connect
from journal.store import candles_store as cs

def test_candles_coverage_prints_ranges(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 180000); conn.commit(); conn.close()
    res = CliRunner().invoke(app, ["candles-coverage", "--db", str(db)])
    assert res.exit_code == 0
    assert "XAUUSDc" in res.stdout and "M1" in res.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k candles_coverage -v`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement both commands in `cli.py`**

After the existing `candles` command, add (note the hyphenated names avoid restructuring `journal candles` into a group):

```python
@app.command("candles-warm")
def candles_warm(
    symbol: str = typer.Argument(..., help="Exact MT5 symbol, e.g. XAUUSDc."),
    timeframe: str = typer.Argument(..., help="One of M1,M5,M15,H1,H4,D1."),
    from_ms: int = typer.Option(..., "--from", help="Range start, epoch ms (server time)."),
    to_ms: int = typer.Option(..., "--to", help="Range end, epoch ms (server time)."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Eagerly fill a candle range from the bridge into the store (pre-warm before
    an offline session). Needs the live bridge. Idempotent."""
    from .adapter.live import LiveMT5Client
    from .ingest.candle_fill import fill_range

    client = LiveMT5Client()
    conn = connect(db)
    try:
        n = fill_range(client, conn, symbol, timeframe, from_ms, to_ms)
    finally:
        conn.close()
    typer.echo(f"== candles-warm ==")
    typer.echo(f"{symbol} {timeframe} [{from_ms}, {to_ms}]: {n} new bars")


@app.command("candles-coverage")
def candles_coverage(
    symbol: str = typer.Option(None, help="Filter to one symbol."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Print stored candle coverage ranges per (symbol, timeframe). No bridge."""
    from .store import candles_store as cs

    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol, timeframe FROM candle_coverage "
            + ("WHERE symbol = ? " if symbol else "")
            + "ORDER BY symbol, timeframe",
            (symbol,) if symbol else (),
        ).fetchall()
        typer.echo("== candles-coverage ==")
        if not rows:
            typer.echo("(none)")
        for r in rows:
            ranges = cs.read_coverage(conn, r["symbol"], r["timeframe"])
            spans = ", ".join(f"[{a}, {b}]" for a, b in ranges)
            typer.echo(f"{r['symbol']:10} {r['timeframe']:4} {spans}")
    finally:
        conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -k candles_coverage -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/cli.py tests/test_cli.py
git commit -m "feat(cli): candles-warm (eager fill) + candles-coverage (inspect)"
```

---

## Task 9: Unify the legacy renderer + ingest onto the store

**Files:**
- Modify: `src/journal/render/chart.py` (read via `candles_store.read_candles`)
- Modify: `src/journal/ingest/candles.py` (insert via store + record coverage)
- Test: existing `tests/test_render*.py` / `tests/test_candles*.py` must stay green (regression)

**Interfaces:**
- Consumes: `candles_store.read_candles`, `candles_store.insert_candle`, `candles_store.record_coverage`.
- Produces: no new public API; behaviour unchanged, one coverage source of truth.

- [ ] **Step 1: Confirm the baseline is green**

Run: `uv run pytest -q`
Expected: PASS (record the count). This is the regression baseline.

- [ ] **Step 2: Route `render/chart.py` reads through the store**

In `src/journal/render/chart.py`, add `from ..store import candles_store` at the top, and replace the inline candle SELECT (currently ~L216-221):

```python
    rows = conn.execute(
        "SELECT time_msc, open, high, low, close, tick_volume FROM candles "
        "WHERE symbol = ? AND timeframe = ? AND time_msc BETWEEN ? AND ? "
        "ORDER BY time_msc",
        (trade["symbol"], chosen_tf, from_msc, to_msc),
    ).fetchall()
```

with:

```python
    rows = candles_store.read_candles(conn, trade["symbol"], chosen_tf, from_msc, to_msc)
```

`read_candles` selects a superset of columns (adds `spread`, `real_volume`) — the downstream code reads `r["time_msc"]`, `r["open"]`, `r["high"]`, `r["low"]`, `r["close"]`, `r["tick_volume"]` by name, so the extra columns are harmless and output is unchanged.

- [ ] **Step 3: Route `ingest/candles.py` through the store + record coverage**

In `src/journal/ingest/candles.py`:
- Replace the body of the module's `_insert_candle` with a delegation, or import and use `candles_store.insert_candle` directly in `sync_candles`. Simplest: add `from ..store import candles_store` and in the loop replace `bars_new += _insert_candle(conn, r["symbol"], tf, c)` with `bars_new += candles_store.insert_candle(conn, r["symbol"], tf, c)`, then delete the now-unused local `_insert_candle` and `_MSC_FLOOR`.
- After the per-trade fetch loop for each `(symbol, tf)` window, record coverage so legacy ingest also populates the coverage table:

```python
        candles_store.record_coverage(conn, r["symbol"], tf, from_msc, to_msc)
```

  (place it right after the `for c in candles:` insert loop, inside the `for r in rows:` loop, before `conn.commit()` at the end). The existing single `conn.commit()` covers it.

- [ ] **Step 4: Run the regression suite**

Run: `uv run pytest -q`
Expected: PASS with the same count as Step 1 (no regressions). Paste the output.

- [ ] **Step 5: Verify `journal rebuild` still succeeds**

Run: `uv run journal rebuild`
Expected: completes without error (Definition of Done). If no local `data/journal.db` exists, note that instead and rely on the passing suite.

- [ ] **Step 6: Update graphify**

Run: `graphify update .`

- [ ] **Step 7: Commit**

```bash
git add src/journal/render/chart.py src/journal/ingest/candles.py
git commit -m "refactor(candles): unify renderer + ingest onto candles_store"
```

---

## Self-Review (author checklist — completed)

**Spec coverage:**
- `resample.py` + guard → Task 1. ✓
- `candle_coverage` + `candle_requests` migration, version bump, schema mirror → Task 2. ✓
- Pure-DB store (read/insert/coverage/missing_ranges/row_to_candle) → Task 3. ✓
- Fill engine (gap-only fetch, empty-range memory, idempotent) → Task 4. ✓
- Queue (dedupe, claim-once, requeue-orphaned) + fulfil → Task 5. ✓
- `journal live` one-request-per-cycle + startup requeue → Task 6. ✓
- Read-only `GET /api/candles` that enqueues, never fetches → Task 7. ✓
- CLI `candles-warm` / `candles-coverage` → Task 8. ✓
- Unification of renderer + ingest, regression, rebuild → Task 9. ✓
- M1-aggregation fallback → Task 7 (`candles_payload`), guard tested in Task 1. ✓

**Placeholder scan:** every code step carries full code; the only "locate" instructions (Task 6 Step 5 grep for `recover_interrupted`, Task 9 Step 2 line ref) are search anchors, not missing code. ✓

**Type consistency:** `read_candles` returns `list[sqlite3.Row]`; `row_to_candle` converts to `Candle`; `resample_m1`/`candles_payload` consume `Candle`; `missing_ranges` and coverage use `list[tuple[int,int]]` throughout. `request_candles` returns `int` (0 sentinel) consistently. ✓

**Known deviations from spec (documented):** `fulfill_request` lives in `candle_fill.py` not `candle_queue.py` (layering); API returns `missing`/`pending` (not `bridge_status`/`gaps`) — the queue model makes "pending fill" the meaningful signal; invalid `from`/`to` yield FastAPI 422 (only domain errors are 400).
