# Advanced SL/TP Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user drag SL/TP lines directly on the chart to modify them — for both real live positions and training/replay positions — instead of typing numbers into a form.

**Architecture:** `CandleChart` gains generic drag/ghost-line/double-click-remove mechanics behind two new optional props (`draggablePositions`, `onSlTpChange`) and knows nothing about live vs. replay. `Chart.tsx` supplies the commit logic per mode: replay calls the training backend directly (instant); live opens a small precision-edit dialog, then reuses the *existing* preview→`ConfirmModal`→enqueue pipeline (extracted from `Live.tsx` into a shared hook so both pages use it).

**Tech Stack:** Python 3.12 / FastAPI / sqlite3 (backend), React + TypeScript + `lightweight-charts` 5.2.0 + vitest/testing-library (frontend). No new dependencies.

## Global Constraints

- Money is USC; `sl`/`tp` on `training_positions` are `REAL NOT NULL DEFAULT 0` — `0` means "none set" (rule 4), never `NULL`, for this table.
- Compare floats with tolerance (`abs(a-b) < 1e-9`), never `==` (rule 5).
- `journal rebuild` must keep succeeding after every backend change; `training_*` tables are untouched by rebuild (rule 2/6).
- No new Python or npm dependencies without asking (rule 8).
- Full gate before any commit that touches shared code: `uv run pytest`, `npx vitest run` (from `frontend/`), `npx tsc --noEmit` (from `frontend/`), `uv run journal rebuild`.
- This plan builds entirely on `main` (the `feature/advanced-sltp-management` branch was scrapped; nothing on it is merged). Work happens on a **new branch**, e.g. `git checkout -b feature/sltp-drag main`.

---

### Task 1: Migration 008 — `training_session_stats` table

**Files:**
- Create: `src/journal/store/migrations/008_training_session_stats.sql`
- Modify: `src/journal/store/schema.sql` (append table, byte-identical to the migration's `CREATE TABLE`)
- Modify: `src/journal/store/db.py:20` (`SCHEMA_VERSION = 7` → `8`)
- Modify: `tests/test_migrations.py` (two existing assertions)

**Interfaces:**
- Produces: table `training_session_stats(session_id PK, total_closed, sl_hits, tp_hits, manual_closes, updated_at_msc)`, one row per `training_sessions.id` (cascade-deleted with the session).

- [ ] **Step 1: Write the failing tests**

In `tests/test_migrations.py`, change:
```python
def test_schema_version_is_7():
    """Spec C adds live_heartbeat/live_watches/live_candles (migration 007)."""
    assert SCHEMA_VERSION == 7
```
to:
```python
def test_schema_version_is_8():
    """Advanced SL/TP adds training_session_stats (migration 008)."""
    assert SCHEMA_VERSION == 8
```
and change:
```python
        assert applied == [2, 3, 4, 5, 6, 7]
```
to:
```python
        assert applied == [2, 3, 4, 5, 6, 7, 8]
```
Also add a new test in the same file (near the other `test_fresh_db_has_*_tables` tests):
```python
def test_fresh_db_has_training_session_stats_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "fresh.db")
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(training_session_stats)").fetchall()}
        assert cols == {"session_id", "total_closed", "sl_hits", "tp_hits",
                        "manual_closes", "updated_at_msc"}
    finally:
        conn.close()
```
(Match the exact `init_schema`/`sqlite3.Row` pattern already used by the neighboring `test_fresh_db_has_training_tables` in this file — copy its imports if `init_schema`/`sqlite3` aren't already imported at the top.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: `test_schema_version_is_8` FAILS (`SCHEMA_VERSION == 7`), `test_migrate_reports_what_it_applied` FAILS (list mismatch), `test_fresh_db_has_training_session_stats_table` FAILS (no such table).

- [ ] **Step 3: Write the migration**

Create `src/journal/store/migrations/008_training_session_stats.sql`:
```sql
-- Migration 008: Training session SL/TP statistics tracking
-- Purpose: Track SL/TP hit rates and performance metrics for training sessions

CREATE TABLE IF NOT EXISTS training_session_stats (
  session_id INTEGER PRIMARY KEY REFERENCES training_sessions(id) ON DELETE CASCADE,
  total_closed INTEGER NOT NULL DEFAULT 0,
  sl_hits INTEGER NOT NULL DEFAULT 0,
  tp_hits INTEGER NOT NULL DEFAULT 0,
  manual_closes INTEGER NOT NULL DEFAULT 0,
  updated_at_msc INTEGER NOT NULL
);
```
(No backfill INSERT — unlike the Kiro reference, a fresh table with zero rows is correct here; `get_session_stats` in Task 2 lazily inserts a row on first read, so a backfill pass would just be immediately-overwritten dead code.)

In `src/journal/store/schema.sql`, append (matching the existing `training_positions` block's style, same file location — end of the training section):
```sql
-- Session-level SL/TP hit-rate stats (migration 008). 0 rows for a fresh DB;
-- get_session_stats() lazily creates a row on first read.
CREATE TABLE IF NOT EXISTS training_session_stats (
  session_id INTEGER PRIMARY KEY REFERENCES training_sessions(id) ON DELETE CASCADE,
  total_closed INTEGER NOT NULL DEFAULT 0,
  sl_hits INTEGER NOT NULL DEFAULT 0,
  tp_hits INTEGER NOT NULL DEFAULT 0,
  manual_closes INTEGER NOT NULL DEFAULT 0,
  updated_at_msc INTEGER NOT NULL
);
```

In `src/journal/store/db.py`, change line 20 from `SCHEMA_VERSION = 7` to `SCHEMA_VERSION = 8`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: all PASS.

- [ ] **Step 5: Full backend gate + commit**

Run: `uv run pytest` (all, must stay green) and `uv run journal rebuild` (must still succeed — this migration doesn't touch `trades`).
```bash
git add src/journal/store/migrations/008_training_session_stats.sql \
        src/journal/store/schema.sql src/journal/store/db.py tests/test_migrations.py
git commit -m "feat(store): migration 008 - training_session_stats table"
```

---

### Task 2: `training.py` — `modify_sltp` + `get_session_stats` + direction validation (TDD)

**Files:**
- Create: `tests/test_sltp_modification.py`
- Modify: `src/journal/web/training.py`

**Interfaces:**
- Consumes: `training_store` (`ts`) module already imported in `training.py` as `from ..store import training_store as ts`; `ts.get_position`/`get_session` (existing, `src/journal/store/training_store.py:97`/`:33`).
- Produces (used by Task 3 routes and Task 5 frontend wrapper):
  - `modify_sltp(conn, position_id: int, sl: float | None = None, tp: float | None = None) -> dict`
  - `get_session_stats(conn, session_id: int) -> dict` with keys `session_id, total_closed, sl_hits, tp_hits, manual_closes, sl_hit_rate, tp_hit_rate, avg_r_per_sl, avg_r_per_tp`.
  - `_resolve_close` (existing, line 132) gains a session-stats-update side effect — no signature change.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sltp_modification.py` (adapted from the reviewed-correct Kiro reference: fixed `from journal...` imports — **not** `from src.journal...`, which was the bug that broke this file's collection before — plus 6 new direction-validation tests appended at the end):

```python
"""Tests for SL/TP modification and session statistics tracking."""
import pytest
from journal.web import training
from journal.store.db import connect, now_ms
from journal.store import training_store as ts


@pytest.fixture
def conn():
    """In-memory database with schema."""
    c = connect(":memory:")
    yield c
    c.close()


def _create_test_session(conn):
    """Helper to create a test training session."""
    return ts.create_session(
        conn,
        symbol="EURUSD",
        symbol_base="EURUSD",
        timeframe="M15",
        range_start_msc=1000000,
        range_end_msc=2000000,
        cursor_msc=1000000
    )


def _create_test_position(conn, session_id, sl=0, tp=0, status="open", direction="buy"):
    """Helper to create a test training position."""
    pos_id = ts.insert_position(
        conn,
        session_id=session_id,
        direction=direction,
        volume=0.1,
        decision_msc=1000000,
        sl=sl,
        tp=tp
    )

    if status == "open":
        conn.execute(
            "UPDATE training_positions SET status = 'open', "
            "entry_msc = ?, entry_price = ? WHERE id = ?",
            (1000000, 1.2500, pos_id)
        )
        conn.commit()
    elif status == "closed":
        conn.execute(
            "UPDATE training_positions SET status = 'closed', "
            "entry_msc = ?, entry_price = ?, exit_msc = ?, exit_price = ? WHERE id = ?",
            (1000000, 1.2500, 1100000, 1.2550, pos_id)
        )
        conn.commit()

    return pos_id


# ---------------------------------------------------------------- modify_sltp tests


def test_modify_sltp_updates_sl_only(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=0, tp=0)

    result = training.modify_sltp(conn, pos_id, sl=1.2450, tp=None)

    assert result["sl"] == 1.2450
    assert result["tp"] == 0


def test_modify_sltp_updates_tp_only(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=0)

    result = training.modify_sltp(conn, pos_id, sl=None, tp=1.2650)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


def test_modify_sltp_updates_both(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id)

    result = training.modify_sltp(conn, pos_id, sl=1.2450, tp=1.2650)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


def test_modify_sltp_remove_sl_sets_to_zero(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450)

    result = training.modify_sltp(conn, pos_id, sl=0, tp=None)

    assert result["sl"] == 0


def test_modify_sltp_closed_position_raises(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, status="closed")

    with pytest.raises(ValueError, match="already closed"):
        training.modify_sltp(conn, pos_id, sl=1.2450)


def test_modify_sltp_nonexistent_position_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        training.modify_sltp(conn, 99999, sl=1.2450)


def test_modify_sltp_no_changes_when_both_none(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    result = training.modify_sltp(conn, pos_id, sl=None, tp=None)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


# --------------------------------------------------- direction-sanity validation


def test_modify_sltp_buy_sl_above_entry_raises(conn):
    """Buy: SL must be below entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="buy")  # entry_price=1.2500

    with pytest.raises(ValueError, match="SL"):
        training.modify_sltp(conn, pos_id, sl=1.2600)


def test_modify_sltp_buy_tp_below_entry_raises(conn):
    """Buy: TP must be above entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="buy")

    with pytest.raises(ValueError, match="TP"):
        training.modify_sltp(conn, pos_id, tp=1.2400)


def test_modify_sltp_sell_sl_below_entry_raises(conn):
    """Sell: SL must be above entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="sell")

    with pytest.raises(ValueError, match="SL"):
        training.modify_sltp(conn, pos_id, sl=1.2400)


def test_modify_sltp_sell_tp_above_entry_raises(conn):
    """Sell: TP must be below entry_price."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="sell")

    with pytest.raises(ValueError, match="TP"):
        training.modify_sltp(conn, pos_id, tp=1.2600)


def test_modify_sltp_remove_sl_skips_direction_check(conn):
    """Setting sl=0 (remove) must never be rejected by the direction guard."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, direction="buy")

    result = training.modify_sltp(conn, pos_id, sl=0)

    assert result["sl"] == 0


def test_modify_sltp_valid_buy_values_pass(conn):
    """Sanity check: correctly-sided values are accepted (regression guard —
    a too-strict validator would break the happy path already covered above)."""
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, direction="buy")

    result = training.modify_sltp(conn, pos_id, sl=1.2450, tp=1.2650)

    assert result["sl"] == 1.2450
    assert result["tp"] == 1.2650


def test_open_position_buy_sl_above_entry_raises(conn):
    """Same direction guard applies to open_position, not just modify_sltp."""
    session_id = _create_test_session(conn)
    # entry_price is only known after a bar fills it in step(); open_position
    # itself has no entry_price yet, so the guard here checks sl/tp against
    # each other (sl must be < tp for buy) when both are non-zero.
    with pytest.raises(ValueError, match="SL"):
        training.open_position(conn, session_id, direction="buy", volume=0.1,
                                sl=1.2650, tp=1.2450)


# ---------------------------------------------------------------- session stats tests


def test_get_session_stats_initializes_if_missing(conn):
    session_id = _create_test_session(conn)

    stats = training.get_session_stats(conn, session_id)

    assert stats["session_id"] == session_id
    assert stats["total_closed"] == 0
    assert stats["sl_hits"] == 0
    assert stats["tp_hits"] == 0
    assert stats["manual_closes"] == 0
    assert stats["sl_hit_rate"] is None
    assert stats["tp_hit_rate"] is None


def test_get_session_stats_calculates_rates_correctly(conn):
    session_id = _create_test_session(conn)

    conn.execute(
        "INSERT INTO training_session_stats "
        "(session_id, total_closed, sl_hits, tp_hits, manual_closes, updated_at_msc) "
        "VALUES (?, 10, 3, 5, 2, ?)",
        (session_id, now_ms())
    )
    conn.commit()

    stats = training.get_session_stats(conn, session_id)

    assert stats["total_closed"] == 10
    assert stats["sl_hit_rate"] == 0.30
    assert stats["tp_hit_rate"] == 0.50


def test_get_session_stats_with_avg_r(conn):
    session_id = _create_test_session(conn)

    for reason, r in [("sl", -1.0), ("sl", -0.5), ("tp", 2.0), ("tp", 3.0)]:
        pos_id = _create_test_position(conn, session_id, status="pending")
        conn.execute(
            "UPDATE training_positions SET status = 'closed', "
            "exit_reason = ?, r_multiple = ? WHERE id = ?",
            (reason, r, pos_id)
        )
    conn.commit()

    conn.execute(
        "INSERT INTO training_session_stats "
        "(session_id, total_closed, sl_hits, tp_hits, manual_closes, updated_at_msc) "
        "VALUES (?, 4, 2, 2, 0, ?)",
        (session_id, now_ms())
    )
    conn.commit()

    stats = training.get_session_stats(conn, session_id)

    assert stats["avg_r_per_sl"] == -0.75
    assert stats["avg_r_per_tp"] == 2.5


# ---------------------------------------------------------------- integration test


def test_close_position_updates_stats(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    from journal.domain import replay_eval as ev
    state = ev.PositionState(
        id=pos_id, direction="buy", volume=0.1, decision_msc=1000000,
        sl=1.2450, tp=1.2650, status="open",
        entry_msc=1000000, entry_price=1.2500,
        close_requested_msc=None, exit_msc=1100000, exit_price=1.2450,
        exit_reason="sl",
    )

    training._resolve_close(conn, "EURUSD", "M15", state)

    stats = training.get_session_stats(conn, session_id)
    assert stats["total_closed"] == 1
    assert stats["sl_hits"] == 1
    assert stats["tp_hits"] == 0


def test_close_with_tp_reason_increments_tp_counter(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    from journal.domain import replay_eval as ev
    state = ev.PositionState(
        id=pos_id, direction="buy", volume=0.1, decision_msc=1000000,
        sl=1.2450, tp=1.2650, status="open",
        entry_msc=1000000, entry_price=1.2500,
        close_requested_msc=None, exit_msc=1100000, exit_price=1.2650,
        exit_reason="tp",
    )

    training._resolve_close(conn, "EURUSD", "M15", state)

    stats = training.get_session_stats(conn, session_id)
    assert stats["total_closed"] == 1
    assert stats["tp_hits"] == 1


def test_manual_close_increments_manual_counter(conn):
    session_id = _create_test_session(conn)
    pos_id = _create_test_position(conn, session_id, sl=1.2450, tp=1.2650)

    from journal.domain import replay_eval as ev
    state = ev.PositionState(
        id=pos_id, direction="buy", volume=0.1, decision_msc=1000000,
        sl=1.2450, tp=1.2650, status="open",
        entry_msc=1000000, entry_price=1.2500,
        close_requested_msc=None, exit_msc=1100000, exit_price=1.2550,
        exit_reason="manual",
    )

    training._resolve_close(conn, "EURUSD", "M15", state)

    stats = training.get_session_stats(conn, session_id)
    assert stats["manual_closes"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sltp_modification.py -v`
Expected: FAIL with `AttributeError: module 'journal.web.training' has no attribute 'modify_sltp'` (and similarly for `get_session_stats`).

- [ ] **Step 3: Write the implementation**

In `src/journal/web/training.py`, add a shared direction-sanity helper and the two new functions. Insert the helper right after the imports (after line 22), and `modify_sltp`/`get_session_stats` at the end of the file (after `career_summary`, currently ending at line 212):

```python
def _check_direction(direction: str, entry_price: float | None,
                     sl: float | None, tp: float | None) -> None:
    """Reject SL/TP on the wrong side of entry for the position's direction.
    Skips whichever side is None (unchanged) or 0 (removed, rule 4) — there's
    nothing to be inconsistent with. If entry_price is unknown yet (position
    still pending), falls back to checking sl vs tp against each other."""
    def is_set(v: float | None) -> bool:
        return v is not None and abs(v) > 1e-9

    ref = entry_price if entry_price is not None else None
    if ref is None:
        if is_set(sl) and is_set(tp):
            if direction == "buy" and not (sl < tp):
                raise ValueError("SL must be below TP for a buy position")
            if direction == "sell" and not (sl > tp):
                raise ValueError("SL must be above TP for a sell position")
        return

    if is_set(sl):
        if direction == "buy" and not (sl < ref):
            raise ValueError("SL must be below entry price for a buy position")
        if direction == "sell" and not (sl > ref):
            raise ValueError("SL must be above entry price for a sell position")
    if is_set(tp):
        if direction == "buy" and not (tp > ref):
            raise ValueError("TP must be above entry price for a buy position")
        if direction == "sell" and not (tp < ref):
            raise ValueError("TP must be below entry price for a sell position")
```

Modify `open_position` (lines 95-109) to call it before inserting — insert the call right after the existing `volume <= 0` check (line 104-105):
```python
def open_position(conn: sqlite3.Connection, session_id: int, *, direction: str,
                  volume: float, sl: float, tp: float) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    if direction not in ("buy", "sell"):
        raise ValueError("direction must be 'buy' or 'sell'")
    if s["status"] != "active":
        raise ValueError(f"session {session_id} is not active")
    if volume <= 0:
        raise ValueError("volume must be > 0")
    _check_direction(direction, None, sl, tp)
    pid = ts.insert_position(conn, session_id=session_id, direction=direction,
                             volume=volume, decision_msc=s["cursor_msc"],
                             sl=sl, tp=tp)
    return _row(ts.get_position(conn, pid))
```
(`entry_price` is always `None` here — a training position has no fill yet at open time — so `_check_direction` falls into its `sl` vs `tp` comparison branch.)

Append at the end of the file:
```python
def modify_sltp(
    conn: sqlite3.Connection,
    position_id: int,
    sl: float | None = None,
    tp: float | None = None,
) -> dict:
    """Modify SL/TP of an open training position. sl/tp: None = leave
    unchanged, 0 = remove (rule 4), any other value = set."""
    pos = conn.execute(
        "SELECT * FROM training_positions WHERE id = ?", (position_id,)
    ).fetchone()
    if not pos:
        raise ValueError(f"position {position_id} not found")
    if pos["status"] == "closed":
        raise ValueError(f"position {position_id} already closed")

    _check_direction(pos["direction"], pos["entry_price"], sl, tp)

    updates, params = [], []
    if sl is not None:
        updates.append("sl = ?")
        params.append(sl)
    if tp is not None:
        updates.append("tp = ?")
        params.append(tp)
    if updates:
        params.append(position_id)
        conn.execute(f"UPDATE training_positions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    return _row(conn.execute(
        "SELECT * FROM training_positions WHERE id = ?", (position_id,)
    ).fetchone())


def get_session_stats(conn: sqlite3.Connection, session_id: int) -> dict:
    """SL/TP hit statistics for a training session. Lazily initializes the
    stats row on first read (a session created before this feature existed
    has none yet)."""
    stats = conn.execute(
        "SELECT * FROM training_session_stats WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not stats:
        conn.execute(
            "INSERT INTO training_session_stats (session_id, updated_at_msc) VALUES (?, ?)",
            (session_id, now_ms()),
        )
        conn.commit()
        stats = {"total_closed": 0, "sl_hits": 0, "tp_hits": 0, "manual_closes": 0}

    total = stats["total_closed"]
    sl_rate = stats["sl_hits"] / total if total > 0 else None
    tp_rate = stats["tp_hits"] / total if total > 0 else None

    avg_r_sl = conn.execute(
        "SELECT AVG(r_multiple) FROM training_positions "
        "WHERE session_id = ? AND exit_reason = 'sl'", (session_id,),
    ).fetchone()[0]
    avg_r_tp = conn.execute(
        "SELECT AVG(r_multiple) FROM training_positions "
        "WHERE session_id = ? AND exit_reason = 'tp'", (session_id,),
    ).fetchone()[0]

    return {
        "session_id": session_id,
        "total_closed": total,
        "sl_hits": stats["sl_hits"],
        "tp_hits": stats["tp_hits"],
        "manual_closes": stats["manual_closes"],
        "sl_hit_rate": sl_rate,
        "tp_hit_rate": tp_rate,
        "avg_r_per_sl": avg_r_sl,
        "avg_r_per_tp": avg_r_tp,
    }
```
Add `from .db import now_ms` to the top-level imports (line 22 area) — do **not** re-import it inline inside functions (the Kiro reference did `from ..store.db import now_ms` inline in two places; use one module-level import instead, matching this file's existing import style).

Finally, hook the stats update into `_resolve_close` (lines 132-158) — add at the end of the function body, after the existing `ts.mark_close(...)` call:
```python
    session_id = conn.execute(
        "SELECT session_id FROM training_positions WHERE id = ?", (state.id,)
    ).fetchone()["session_id"]
    conn.execute(
        "INSERT OR IGNORE INTO training_session_stats (session_id, updated_at_msc) VALUES (?, ?)",
        (session_id, now_ms()),
    )
    column = {"sl": "sl_hits", "tp": "tp_hits"}.get(state.exit_reason, "manual_closes")
    conn.execute(
        f"UPDATE training_session_stats SET {column} = {column} + 1, "
        "total_closed = total_closed + 1, updated_at_msc = ? WHERE session_id = ?",
        (now_ms(), session_id),
    )
    conn.commit()
```
(This collapses the Kiro reference's three near-identical `if/elif/else` UPDATE branches into one parametrized-by-column-name statement — same behavior, less repetition. `"eod"` and `"manual"` both fall into the `manual_closes` bucket via `.get(..., "manual_closes")`, matching the reference's `else` branch.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sltp_modification.py -v`
Expected: all PASS (17 tests).

- [ ] **Step 5: Full backend gate + commit**

Run: `uv run pytest` (all) and `uv run journal rebuild`.
```bash
git add tests/test_sltp_modification.py src/journal/web/training.py
git commit -m "feat(backend): modify_sltp + session stats + direction validation"
```

---

### Task 3: `app.py` routes — `PATCH .../sltp` + `GET .../stats`

**Files:**
- Modify: `src/journal/web/app.py` (insert after the existing `@app.get("/api/training/summary")` block, which ends at line 464)

**Interfaces:**
- Consumes: `training.modify_sltp`, `training.get_session_stats` (Task 2).
- Produces: `PATCH /api/training/positions/{position_id}/sltp`, `GET /api/training/sessions/{session_id}/stats` — consumed by Task 5's `replayApi.ts`.

- [ ] **Step 1: Add the routes**

Insert immediately after line 464 (`return JSONResponse(api.to_jsonable(training.career_summary(conn)))`), before the `# -------------------------------------------------------- storage & maintenance` section:
```python
    @app.patch("/api/training/positions/{position_id}/sltp")
    def api_training_modify_sltp(
        position_id: int,
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            position = training.modify_sltp(conn, position_id, sl, tp)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"position": api.to_jsonable(position)})

    @app.get("/api/training/sessions/{session_id}/stats")
    def api_training_session_stats(session_id: int,
                                   conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.to_jsonable(training.get_session_stats(conn, session_id)))
```
(No `try/except` needed on the stats route — `get_session_stats` never raises; it lazily creates a missing row instead, per Task 2.)

- [ ] **Step 2: Manual smoke test**

Run: `uv run journal serve` in one terminal, then in another:
```bash
curl -s -X POST localhost:8000/api/training/sessions -H 'Content-Type: application/json' \
  -d '{"symbol":"XAUUSDc","timeframe":"M5","range_start_msc":1700000000000,"range_end_msc":1700010000000}'
# note the returned session id, then:
curl -s -X POST localhost:8000/api/training/sessions/<id>/positions -H 'Content-Type: application/json' \
  -d '{"direction":"buy","volume":0.01,"sl":0,"tp":0}'
# note the returned position id, then:
curl -s -X PATCH localhost:8000/api/training/positions/<pid>/sltp -H 'Content-Type: application/json' \
  -d '{"sl":1900.0}'
curl -s localhost:8000/api/training/sessions/<id>/stats
```
Expected: all three return 200 with the expected JSON shapes (no 404/500).

- [ ] **Step 3: Full backend gate + commit**

Run: `uv run pytest` and `uv run journal rebuild`.
```bash
git add src/journal/web/app.py
git commit -m "feat(api): PATCH /api/training/positions/{id}/sltp + GET session stats"
```

---

### Task 4: `sltpDrag.ts` — pure drag/hit-test logic (TDD)

**Files:**
- Create: `frontend/src/lib/sltpDrag.ts`
- Test: `frontend/src/lib/sltpDrag.test.ts`

**Interfaces:**
- Produces (consumed by Task 6's `CandleChart.tsx`):
  - `interface DraggablePosition { id: number; direction: "buy" | "sell"; entry_price: number | null; sl: number; tp: number }`
  - `type LineKind = "entry" | "sl" | "tp"`
  - `HIT_THRESHOLD_PX: number` (= 8)
  - `resolveDragTarget(pos: DraggablePosition, price: number): "sl" | "tp"`
  - `ghostTitle(kind: "sl" | "tp", entryPrice: number | null, price: number): string`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/sltpDrag.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { resolveDragTarget, ghostTitle, HIT_THRESHOLD_PX, type DraggablePosition } from "./sltpDrag";

const buyPos: DraggablePosition = { id: 1, direction: "buy", entry_price: 100, sl: 0, tp: 0 };
const sellPos: DraggablePosition = { id: 2, direction: "sell", entry_price: 100, sl: 0, tp: 0 };

describe("resolveDragTarget", () => {
  it("buy: price below entry resolves to sl", () => {
    expect(resolveDragTarget(buyPos, 95)).toBe("sl");
  });
  it("buy: price above entry resolves to tp", () => {
    expect(resolveDragTarget(buyPos, 105)).toBe("tp");
  });
  it("sell: price above entry resolves to sl", () => {
    expect(resolveDragTarget(sellPos, 105)).toBe("sl");
  });
  it("sell: price below entry resolves to tp", () => {
    expect(resolveDragTarget(sellPos, 95)).toBe("tp");
  });
  it("no entry_price known: defaults to sl (caller must not rely on this for entry-drag)", () => {
    const pending: DraggablePosition = { ...buyPos, entry_price: null };
    expect(resolveDragTarget(pending, 95)).toBe("sl");
  });
});

describe("ghostTitle", () => {
  it("shows signed distance from entry for sl", () => {
    expect(ghostTitle("sl", 100, 95)).toBe("SL → -5.00000");
  });
  it("shows signed distance from entry for tp with positive sign", () => {
    expect(ghostTitle("tp", 100, 105)).toBe("TP → +5.00000");
  });
  it("falls back to bare price when entry is unknown", () => {
    expect(ghostTitle("sl", null, 95)).toBe("SL → 95.00000");
  });
});

describe("HIT_THRESHOLD_PX", () => {
  it("is a small pixel tolerance", () => {
    expect(HIT_THRESHOLD_PX).toBe(8);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/sltpDrag.test.ts`
Expected: FAIL — `sltpDrag.ts` doesn't exist.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/sltpDrag.ts`:
```ts
// Pure drag/hit-test logic for chart-based SL/TP editing. No chart or DOM
// access here — mirrors lib/measure.ts's pure-logic/reducer style. The
// component (CandleChart) owns pixel<->price projection and calls into this
// module with already-resolved prices.

export interface DraggablePosition {
  id: number;
  direction: "buy" | "sell";
  entry_price: number | null;
  sl: number;   // 0 = none set (rule 4)
  tp: number;   // 0 = none set (rule 4)
}

export type LineKind = "entry" | "sl" | "tp";

export const HIT_THRESHOLD_PX = 8;

// When dragging FROM the entry line (no sl/tp set yet), decide whether the
// dragged-to price should become the SL or the TP, based on direction and
// which side of entry the price landed on. If entry_price is unknown, this
// can't be resolved meaningfully — defaults to "sl" (callers should only
// invoke this for an entry-line drag, where entry_price is always known;
// the null case exists purely so the function total, not partial).
export function resolveDragTarget(pos: DraggablePosition, price: number): "sl" | "tp" {
  if (pos.entry_price === null) return "sl";
  const above = price > pos.entry_price;
  if (pos.direction === "buy") return above ? "tp" : "sl";
  return above ? "sl" : "tp";
}

// Ghost-line title while dragging: signed distance from entry, 5 decimals
// (matches lib/format.ts::price()'s full-precision philosophy — never round
// away a price digit). Falls back to the bare price if entry is unknown.
export function ghostTitle(kind: "sl" | "tp", entryPrice: number | null, price: number): string {
  const label = kind.toUpperCase();
  if (entryPrice === null) return `${label} → ${price.toFixed(5)}`;
  const distance = price - entryPrice;
  const sign = distance >= 0 ? "+" : "";
  return `${label} → ${sign}${distance.toFixed(5)}`;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/sltpDrag.test.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd frontend && npx tsc --noEmit
git add frontend/src/lib/sltpDrag.ts frontend/src/lib/sltpDrag.test.ts
git commit -m "feat(fe): sltpDrag.ts - pure drag-target and ghost-title logic"
```

---

### Task 5: Frontend API wrappers — `patchJson` + `replayApi.modifySltp` + `useReplaySession.modifySltp`

**Files:**
- Modify: `frontend/src/lib/api.ts` (add `patchJson`)
- Modify: `frontend/src/lib/replayApi.ts` (add `modifySltp`)
- Modify: `frontend/src/hooks/useReplaySession.ts` (add `modifySltp` method)
- Test: `frontend/src/hooks/useReplaySession.test.ts` (new — this hook currently has no test file; adding one narrow test for the new method only, not a full hook test suite)

**Interfaces:**
- Produces: `patchJson<T>(path: string, body: unknown): Promise<{ok: boolean; data?: T; error?: string}>`; `replayApi.modifySltp(pid: number, body: {sl?: number; tp?: number}): Promise<{ok:boolean; data?: TrainingPosition; error?: string}>`; `useReplaySession()` return object gains `modifySltp: (pid: number, change: {sl?: number; tp?: number}) => Promise<void>`.

- [ ] **Step 1: Add `patchJson` to `api.ts`**

In `frontend/src/lib/api.ts`, add right after the existing `postJson` function:
```ts
export async function patchJson<T>(
  path: string,
  body: unknown,
): Promise<{ ok: boolean; data?: T; error?: string }> {
  try {
    const r = await fetch(path, {
      method: "PATCH",
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

- [ ] **Step 2: Add `modifySltp` to `replayApi.ts`**

In `frontend/src/lib/replayApi.ts`, change the import line to add `patchJson`:
```ts
import { postJson, patchJson } from "./api";
```
Then add, after `closePosition`:
```ts
export function modifySltp(pid: number, body: { sl?: number; tp?: number }) {
  return patchJson<{ position: TrainingPosition }>(`/api/training/positions/${pid}/sltp`, body);
}
```

- [ ] **Step 3: Write the failing test for `useReplaySession.modifySltp`**

Create `frontend/src/hooks/useReplaySession.test.ts`:
```ts
import { renderHook, act } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import { useReplaySession } from "./useReplaySession";
import * as replayApi from "../lib/replayApi";

beforeEach(() => {
  vi.restoreAllMocks();
});

it("modifySltp calls replayApi.modifySltp then refreshes the session", async () => {
  const { result } = renderHook(() => useReplaySession());

  // Seed an active session id the way `start`/`open` would.
  vi.spyOn(replayApi, "createSession").mockResolvedValue({
    ok: true,
    data: { session: { id: 42, symbol: "XAUUSDc", symbol_base: "XAUUSD", timeframe: "M5",
      range_start_msc: 0, range_end_msc: 1000, cursor_msc: 0, status: "active", created_at_msc: 0 },
      pending: false },
  });
  await act(async () => { await result.current.start({
    symbol: "XAUUSDc", timeframe: "M5", range_start_msc: 0, range_end_msc: 1000,
  } as any); });

  const modifySpy = vi.spyOn(replayApi, "modifySltp").mockResolvedValue({
    ok: true, data: { position: {} as any },
  });
  const getSessionSpy = vi.spyOn(replayApi, "getSession").mockResolvedValue({
    session: { id: 42 } as any, positions: [], summary: {} as any,
  });

  await act(async () => { await result.current.modifySltp(7, { sl: 1900 }); });

  expect(modifySpy).toHaveBeenCalledWith(7, { sl: 1900 });
  expect(getSessionSpy).toHaveBeenCalled();
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useReplaySession.test.ts`
Expected: FAIL — `result.current.modifySltp is not a function`.

- [ ] **Step 5: Add `modifySltp` to the hook**

In `frontend/src/hooks/useReplaySession.ts`, add the import (near the top, alongside the existing `replayApi` import — check the existing import statement's exact form and extend it) and a new function mirroring `close` (lines 108-115). Add right after `close`:
```ts
const modifySltp = useCallback(async (pid: number, change: { sl?: number; tp?: number }) => {
  const r = await replayApi.modifySltp(pid, change);
  if (!r.ok) { setError(r.error ?? "gagal mengubah SL/TP"); return; }
  await refresh();
}, [refresh]);
```
Add `modifySltp` to the returned object (line 138-143 block), alongside `start, step, play, pause, jump, reset, open, close, end, discard`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/hooks/useReplaySession.test.ts`
Expected: PASS.

- [ ] **Step 7: Full frontend gate + commit**

Run (from `frontend/`): `npx vitest run`, `npx tsc --noEmit`.
```bash
git add frontend/src/lib/api.ts frontend/src/lib/replayApi.ts \
        frontend/src/hooks/useReplaySession.ts frontend/src/hooks/useReplaySession.test.ts
git commit -m "feat(fe): patchJson + replayApi.modifySltp + useReplaySession.modifySltp"
```

---

### Task 6: `CandleChart.tsx` — draggable SL/TP lines (component TDD)

**Files:**
- Modify: `frontend/src/components/CandleChart.tsx`
- Modify: `frontend/src/components/CandleChart.test.tsx`

**Interfaces:**
- Consumes: `DraggablePosition`, `resolveDragTarget`, `ghostTitle`, `HIT_THRESHOLD_PX` (Task 4); `LINE_COLORS` (`../lib/candles`, existing).
- Produces (consumed by Task 7 and Task 10): new props `draggablePositions?: DraggablePosition[]`, `onSlTpChange?: (positionId: number, change: { sl?: number; tp?: number }) => void`.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/CandleChart.test.tsx` (after the existing tests, same file). These need the mock at the top of the file extended to capture `createPriceLine` calls and to stub `priceToCoordinate`/`coordinateToPrice` deterministically. Change the `vi.mock("lightweight-charts", ...)` block to also patch `addSeries` for price-line capture and coordinate stubs:
```ts
// Add near the top, alongside capturedMarkers/capturedLogicalRange:
let capturedPriceLines: { price: number; color: string; title: string }[] = [];

// Inside the vi.mock's chart.addSeries override, after wrapping setMarkers, add:
        const origCreatePriceLine = s.createPriceLine;
        s.createPriceLine = (opts: any) => {
          capturedPriceLines.push({ price: opts.price, color: opts.color, title: opts.title });
          return origCreatePriceLine ? origCreatePriceLine.call(s, opts) : { applyOptions: () => {}, options: () => opts, remove: () => {} };
        };
        // Deterministic pixel<->price mapping for hit-test/drag math:
        // y=200 <-> price=100 (SL line), y=100 <-> price=110 (TP line),
        // y=150 <-> price=105 (entry line); 1px = 0.1 price unit elsewhere.
        s.priceToCoordinate = (price: number) => 200 - (price - 100) * 10;
        s.coordinateToPrice = (y: number) => 100 + (200 - y) / 10;
```
And reset `capturedPriceLines = [];` in `beforeEach`.

Then add the new test cases:
```ts
import { fireEvent } from "@testing-library/react";
import type { DraggablePosition } from "../lib/sltpDrag";

const draggablePos: DraggablePosition = { id: 1, direction: "buy", entry_price: 105, sl: 100, tp: 110 };

it("renders draggable SL/TP/entry lines when draggablePositions is provided", () => {
  render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
    />
  );

  const prices = capturedPriceLines.map((l) => l.price).sort((a, b) => a - b);
  expect(prices).toEqual([100, 105, 110]);
});

it("dragging the SL line to a new pixel position calls onSlTpChange with the new price", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelector("div > div") as HTMLElement;

  // SL line is at y=200 (price 100, per the mock mapping). Press there,
  // move to y=180 (price 102), release.
  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 180 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 180 });

  expect(onSlTpChange).toHaveBeenCalledWith(1, { sl: 102 });
});

it("double-clicking an existing SL line calls onSlTpChange with sl: 0 (remove)", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelector("div > div") as HTMLElement;

  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 200 });
  fireEvent.pointerDown(node, { clientX: 51, clientY: 201 });   // within 350ms/5px -> double-click-hold

  expect(onSlTpChange).toHaveBeenCalledWith(1, { sl: 0 });
});

it("does not confuse a plain drag-a-line with the Spec-B measure gesture", () => {
  const onSlTpChange = vi.fn();
  const { container } = render(
    <CandleChart
      symbol="XAUUSDc" tf="M1" settings={DEFAULT_SETTINGS} candles={mockCandles}
      onHover={() => {}} onNowVisibleChange={() => {}} onRequestOlder={() => {}}
      lastBarMs={2_140_000} live={null} nowVisible={true}
      draggablePositions={[draggablePos]}
      onSlTpChange={onSlTpChange}
    />
  );
  const node = container.querySelector("div > div") as HTMLElement;

  // A single (non-double) press-and-drag on the SL line must go through the
  // drag-a-line path, not fall through into the idle "clear frozen measure" branch.
  fireEvent.pointerDown(node, { clientX: 50, clientY: 200 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 190 });
  fireEvent.pointerUp(window, { clientX: 50, clientY: 190 });

  expect(onSlTpChange).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/CandleChart.test.tsx`
Expected: FAIL — no `draggablePositions`/`onSlTpChange` props exist yet, `capturedPriceLines` stays empty.

- [ ] **Step 3: Implement in `CandleChart.tsx`**

Add imports (extend the existing `../lib/candles` import to include `LINE_COLORS`, add the new `sltpDrag` import):
```tsx
import { toSeconds, liveLines, isNowVisible, LINE_COLORS, type Sym, type Timeframe } from "../lib/candles";
import { resolveDragTarget, ghostTitle, HIT_THRESHOLD_PX, type DraggablePosition, type LineKind } from "../lib/sltpDrag";
```

Add two new props to the `forwardRef<ChartHandle, {...}>` type (after `overlayLines`, line 76):
```tsx
  draggablePositions?: DraggablePosition[];
  onSlTpChange?: (positionId: number, change: { sl?: number; tp?: number }) => void;
```

Add new refs/state after `dragging` (line 94):
```tsx
  const sltpDragging = useRef<{ positionId: number; kind: LineKind; startPrice: number } | null>(null);
  const [sltpGhost, setSltpGhost] = useState<{ price: number; kind: "sl" | "tp" } | null>(null);
  const linesMeta = useRef<{ line: IPriceLine; positionId: number; kind: LineKind }[]>([]);
  const ghostLine = useRef<IPriceLine | null>(null);
```

Add a hit-test helper alongside `toPoint` (after line 109):
```tsx
  const hitTestLine = useCallback((y: number): { positionId: number; kind: LineKind; price: number } | null => {
    const s = series.current;
    if (!s) return null;
    for (const meta of linesMeta.current) {
      const py = s.priceToCoordinate(meta.line.options().price);
      if (py !== null && Math.abs((py as number) - y) <= HIT_THRESHOLD_PX) {
        return { positionId: meta.positionId, kind: meta.kind, price: meta.line.options().price };
      }
    }
    return null;
  }, []);
```

Extend the pointer-event effect (lines 212-278). Replace the whole `onDown`/`onMove`/`onUp` bodies:
```tsx
    const onDown = (e: PointerEvent) => {
      const { x, y } = rel(e);
      const prev = lastUp.current;
      if (prev && isDoubleClickHold(prev.ms, prev.x, prev.y, e.timeStamp, x, y)) {
        const hit = hitTestLine(y);
        if (hit && hit.kind !== "entry" && cbs.current.onSlTpChange) {
          cbs.current.onSlTpChange(hit.positionId, hit.kind === "sl" ? { sl: 0 } : { tp: 0 });
          e.preventDefault();
          return;
        }
        const anchor = toPoint(x, y);
        if (!anchor) return;
        dragging.current = true;
        c.applyOptions({ handleScroll: false, handleScale: false });
        setMeasure((s) => measureReducer(s, { t: "start", anchor }));
        e.preventDefault();
      } else {
        const hit = hitTestLine(y);
        if (hit && cbs.current.onSlTpChange) {
          sltpDragging.current = { positionId: hit.positionId, kind: hit.kind, startPrice: hit.price };
          c.applyOptions({ handleScroll: false, handleScale: false });
          const pt = toPoint(x, y);
          if (pt) setSltpGhost({ price: pt.price, kind: hit.kind === "tp" ? "tp" : "sl" });
          e.preventDefault();
          return;
        }
        setMeasure((s) => (s.phase === "frozen" ? measureReducer(s, { t: "clear" }) : s));
      }
    };

    const onMove = (e: PointerEvent) => {
      if (sltpDragging.current) {
        const { x, y } = rel(e);
        const pt = toPoint(x, y);
        if (!pt) return;
        const drag = sltpDragging.current;
        const pos = cbs.current.draggablePositions?.find((p) => p.id === drag.positionId);
        const kind = drag.kind === "entry" && pos ? resolveDragTarget(pos, pt.price)
          : (drag.kind as "sl" | "tp");
        setSltpGhost({ price: pt.price, kind });
        return;
      }
      if (!dragging.current) return;
      const { x, y } = rel(e);
      const cur = toPoint(x, y);
      if (cur) setMeasure((s) => measureReducer(s, { t: "move", cursor: cur }));
    };

    const onUp = (e: PointerEvent) => {
      const { x, y } = rel(e);
      lastUp.current = { ms: e.timeStamp, x, y };
      if (sltpDragging.current) {
        const drag = sltpDragging.current;
        sltpDragging.current = null;
        setSltpGhost(null);
        endDrag();
        const pt = toPoint(x, y);
        const pos = cbs.current.draggablePositions?.find((p) => p.id === drag.positionId);
        // Skip the no-op case: a plain click (press+release with no real
        // movement) must not fire a "change" to the same value it already
        // had — same float-tolerance convention as rule 5 elsewhere.
        if (pt && cbs.current.onSlTpChange && Math.abs(pt.price - drag.startPrice) > 1e-9) {
          const target = drag.kind === "entry" && pos ? resolveDragTarget(pos, pt.price)
            : (drag.kind as "sl" | "tp");
          cbs.current.onSlTpChange(drag.positionId, { [target]: pt.price } as { sl?: number; tp?: number });
        }
        return;
      }
      if (dragging.current) {
        endDrag();
        setMeasure((s) => measureReducer(s, { t: "release" }));
      }
    };
```
Add `hitTestLine` to that effect's dependency array (currently `[toPoint, endDrag]`, line 278) → `[toPoint, endDrag, hitTestLine]`.

Extend the overlay-lines effect (lines 402-436) to populate `linesMeta` and add the `draggablePositions` branch. Replace the body from `for (const pl of priceLines.current) s.removePriceLine(pl);` through the closing `}, [...])`:
```tsx
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];
    linesMeta.current = [];

    const addLine = (positionId: number, kind: LineKind, price: number | null,
                     color: string, title: string) => {
      if (price === null || price === undefined || Math.abs(price) < 1e-9) return;
      const line = s.createPriceLine({
        price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title,
      });
      priceLines.current.push(line);
      linesMeta.current.push({ line, positionId, kind });
    };

    if (props.draggablePositions !== undefined) {
      for (const pos of props.draggablePositions) {
        addLine(pos.id, "entry", pos.entry_price, LINE_COLORS.entry, `entry #${pos.id}`);
        addLine(pos.id, "sl", pos.sl, LINE_COLORS.sl, `SL #${pos.id}`);
        addLine(pos.id, "tp", pos.tp, LINE_COLORS.tp, `TP #${pos.id}`);
      }
      return;
    }

    const explicit = props.overlayLines;
    if (explicit !== undefined) {
      for (const line of explicit) {
        priceLines.current.push(s.createPriceLine({
          price: line.price, color: line.color, lineWidth: 1,
          lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: line.title,
        }));
      }
      return;
    }

    if (!props.settings.liveOverlay || !props.nowVisible || !props.live || props.live.live.empty) return;
    const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
    for (const pos of mine) {
      addLine(pos.position_id, "entry", pos.open_price, LINE_COLORS.entry, `entry #${pos.position_id}`);
      addLine(pos.position_id, "sl", pos.sl, LINE_COLORS.sl, `SL #${pos.position_id}`);
      addLine(pos.position_id, "tp", pos.tp, LINE_COLORS.tp, `TP #${pos.position_id}`);
    }
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay,
      props.settings.chartType, props.overlayLines, props.draggablePositions]);
```
(The live-fallback branch now goes through `addLine`/`linesMeta` too — so live positions become draggable automatically whenever `onSlTpChange` is passed, with **no** need for the caller to separately build a `draggablePositions` array for the live case. `draggablePositions` is only needed for replay, where there's no `props.live` equivalent to source from.)

Add a ghost-line-rendering effect, placed right after the effect above:
```tsx
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (ghostLine.current) { s.removePriceLine(ghostLine.current); ghostLine.current = null; }
    if (sltpGhost) {
      const drag = sltpDragging.current;
      const pos = drag && cbs.current.draggablePositions?.find((p) => p.id === drag.positionId);
      const entryFallback = pos?.entry_price ?? null;
      const title = ghostTitle(sltpGhost.kind, entryFallback, sltpGhost.price);
      const color = (sltpGhost.kind === "tp" ? LINE_COLORS.tp : LINE_COLORS.sl) + "80";
      ghostLine.current = s.createPriceLine({
        price: sltpGhost.price, color, lineWidth: 2, lineStyle: LineStyle.Solid,
        axisLabelVisible: true, title,
      });
    }
  }, [sltpGhost]);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/CandleChart.test.tsx`
Expected: all PASS. If the pixel/price mock values in Step 1 don't line up exactly with the real `toPoint`/`hitTestLine` math (off-by-one from the mocked `priceToCoordinate`/`coordinateToPrice` formulas), adjust the mock's linear formula constants (not the production code) until they do — the production logic is what Task 4's unit tests already pinned down.

- [ ] **Step 5: Full frontend gate + commit**

Run: `npx vitest run`, `npx tsc --noEmit` (from `frontend/`).
```bash
git add frontend/src/components/CandleChart.tsx frontend/src/components/CandleChart.test.tsx
git commit -m "feat(fe): draggable SL/TP/entry lines in CandleChart"
```

---

### Task 7: Wire replay mode in `Chart.tsx`

**Files:**
- Modify: `frontend/src/pages/Chart.tsx`

**Interfaces:**
- Consumes: `CandleChart`'s new `draggablePositions`/`onSlTpChange` props (Task 6); `replay.modifySltp` (Task 5).

- [ ] **Step 1: Replace the `overlay`/`overlayLines` wiring with `draggablePositions`**

In `frontend/src/pages/Chart.tsx`, remove the `overlay` useMemo (lines 161-164) and the `replayLines` import if it becomes unused (check — `replayLines` may still be used elsewhere in the file; if not, drop it from the `import { clipToCursor, replayLines, type TrainingSummary } from "../lib/replay";` line). Replace with:
```tsx
  const draggableReplay = useMemo(
    () => (replayOpen
      ? replay.positions
          .filter((p) => p.status !== "closed")
          .map((p) => ({ id: p.id, direction: p.direction, entry_price: p.entry_price, sl: p.sl, tp: p.tp }))
      : undefined),
    [replayOpen, replay.positions],
  );
  const handleSlTpChange = useCallback((positionId: number, change: { sl?: number; tp?: number }) => {
    if (replayOpen) {
      replay.modifySltp(positionId, change);
    }
    // Live-mode handling added in Task 10.
  }, [replayOpen, replay]);
```
Update the `<CandleChart>` invocation (around lines 215-231): replace `overlayLines={overlay}` with:
```tsx
    draggablePositions={draggableReplay}
    onSlTpChange={handleSlTpChange}
```

- [ ] **Step 2: Verify manually**

Run `cd frontend && npm run build` (must succeed) — this is a wiring-only change with no new pure logic to unit-test in isolation; correctness is covered by Task 6's `CandleChart` tests plus Task 5's `useReplaySession.modifySltp` test. A full click-through (start a replay session, open a position, drag its SL line) is part of the **PENDING HUMAN** visual pass at the end of this plan (Task 12).

- [ ] **Step 3: Full frontend gate + commit**

Run: `npx vitest run`, `npx tsc --noEmit` (from `frontend/`).
```bash
git add frontend/src/pages/Chart.tsx
git commit -m "feat(fe): wire replay SL/TP drag to training.modify_sltp"
```

---

### Task 8: Extract `useLiveCommand` hook from `Live.tsx` (refactor, behavior-preserving)

**Files:**
- Create: `frontend/src/hooks/useLiveCommand.ts`
- Modify: `frontend/src/pages/Live.tsx`
- Test: `frontend/src/hooks/useLiveCommand.test.ts`

**Interfaces:**
- Produces (consumed by Task 10): a hook exposing `{ preview, error, submitting, toast, request, confirm, cancel }`, where `request(position_id, action, body)` runs the preview step, `confirm()` runs the enqueue step, `cancel()` clears state — extracted verbatim from `Live.tsx`'s current inline `onAction`/`onConfirm`/`onCancel` (lines 24-47), no behavior change.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/useLiveCommand.test.ts`:
```ts
import { renderHook, act } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import { useLiveCommand } from "./useLiveCommand";
import * as api from "../lib/api";

beforeEach(() => { vi.restoreAllMocks(); });

it("request() fetches a preview and stores it without sending the real command", async () => {
  const postSpy = vi.spyOn(api, "postJson").mockResolvedValue({
    ok: true, data: { intent: "Set SL to 1900", position_id: 5, kind: "modify_sltp",
      symbol: "XAUUSDc", fields: { sl: 1900, tp: null, volume: null } },
  });
  const { result } = renderHook(() => useLiveCommand());

  await act(async () => { await result.current.request(5, "sltp", { sl: 1900 }); });

  expect(postSpy).toHaveBeenCalledWith("/api/live/5/sltp/preview", { sl: 1900 });
  expect(result.current.preview?.intent).toBe("Set SL to 1900");
});

it("confirm() enqueues the pending command and clears the preview", async () => {
  vi.spyOn(api, "postJson").mockResolvedValueOnce({
    ok: true, data: { intent: "Set SL to 1900", position_id: 5, kind: "modify_sltp",
      symbol: "XAUUSDc", fields: { sl: 1900, tp: null, volume: null } },
  });
  const { result } = renderHook(() => useLiveCommand());
  await act(async () => { await result.current.request(5, "sltp", { sl: 1900 }); });

  const enqueueSpy = vi.spyOn(api, "postJson").mockResolvedValue({
    ok: true, data: { ok: true, command_id: 42 },
  });
  await act(async () => { await result.current.confirm(); });

  expect(enqueueSpy).toHaveBeenCalledWith("/api/live/5/sltp", { sl: 1900 });
  expect(result.current.preview).toBeNull();
});

it("cancel() clears preview and pending state without enqueueing", async () => {
  vi.spyOn(api, "postJson").mockResolvedValue({
    ok: true, data: { intent: "Set SL to 1900", position_id: 5, kind: "modify_sltp",
      symbol: "XAUUSDc", fields: { sl: 1900, tp: null, volume: null } },
  });
  const { result } = renderHook(() => useLiveCommand());
  await act(async () => { await result.current.request(5, "sltp", { sl: 1900 }); });

  act(() => { result.current.cancel(); });

  expect(result.current.preview).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useLiveCommand.test.ts`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the hook**

Create `frontend/src/hooks/useLiveCommand.ts` — this is a direct extraction of `Live.tsx`'s current lines 24-47, unchanged in behavior:
```ts
import { useRef, useState } from "react";
import { postJson } from "../lib/api";
import type { ActionKind, CommandBody, PreviewResult } from "../lib/types";

// Two-step live trade command: preview writes nothing (server re-validates),
// confirm is the only write. Extracted from Live.tsx so Chart.tsx (SL/TP
// drag) can reuse the exact same safety flow instead of duplicating it.
export function useLiveCommand() {
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [pending, setPending] = useState<{ action: ActionKind; body: CommandBody } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const enqueuing = useRef(false);

  const request = async (position_id: number, action: ActionKind, body: CommandBody) => {
    setError(null);
    const r = await postJson<PreviewResult>(`/api/live/${position_id}/${action}/preview`, body);
    if (!r.ok) { setToast(null); setError(r.error ?? "gagal"); setPreview(null); return; }
    setPending({ action, body });
    setPreview(r.data ?? null);
  };

  const confirm = async () => {
    if (!preview || !pending) return;
    if (enqueuing.current) return;
    enqueuing.current = true;
    setSubmitting(true);
    const r = await postJson<{ ok: boolean; command_id: number }>(
      `/api/live/${preview.position_id}/${pending.action}`, pending.body);
    setSubmitting(false);
    if (!r.ok) { setError(r.error ?? "gagal"); enqueuing.current = false; return; }
    setPreview(null); setPending(null); setError(null);
    setToast(`Perintah #${r.data?.command_id} masuk antrean — journal live akan mengeksekusi.`);
    enqueuing.current = false;
  };

  const cancel = () => { setPreview(null); setPending(null); setError(null); enqueuing.current = false; };

  return { preview, error, submitting, toast, request, confirm, cancel };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/hooks/useLiveCommand.test.ts`
Expected: all PASS.

- [ ] **Step 5: Wire `Live.tsx` to use the hook (behavior-preserving refactor)**

In `frontend/src/pages/Live.tsx`, replace the inline `actionError`/`onAction`/`onConfirm`/`onCancel`/`preview`/`submitting`/`toast`/`enqueuing`/`pending` state (lines ~24-47 and wherever those states are declared) with:
```tsx
import { useLiveCommand } from "../hooks/useLiveCommand";
// ...
const cmd = useLiveCommand();
```
Replace every reference: `preview` → `cmd.preview`, `submitting` → `cmd.submitting`, `actionError` → `cmd.error`, `toast` → `cmd.toast`, the `onAction(p.position_id, action, body)` call → `cmd.request(p.position_id, action, body)`, `onConfirm` → `cmd.confirm`, `onCancel` → `cmd.cancel`. The JSX structure (toast div, error div, `<ConfirmModal>` usage) stays exactly as-is, just reading off `cmd.*` instead of local state.

- [ ] **Step 6: Manual regression check**

Run `cd frontend && npm run build`, then start the app (`uv run journal serve`) and manually exercise Close / Tutup sebagian / Tambah / the current SL/TP form on `/live` to confirm identical behavior to before the refactor (no automated test previously existed for `Live.tsx` as a whole — this is a targeted manual check, not a new gap introduced by this task).

- [ ] **Step 7: Full frontend gate + commit**

Run: `npx vitest run`, `npx tsc --noEmit` (from `frontend/`).
```bash
git add frontend/src/hooks/useLiveCommand.ts frontend/src/hooks/useLiveCommand.test.ts frontend/src/pages/Live.tsx
git commit -m "refactor(fe): extract useLiveCommand hook from Live.tsx for reuse by Chart.tsx"
```

---

### Task 9: `SltpConfirmDialog.tsx` — live precision-edit dialog

**Files:**
- Create: `frontend/src/components/SltpConfirmDialog.tsx`
- Test: `frontend/src/components/SltpConfirmDialog.test.tsx`

**Interfaces:**
- Produces (consumed by Task 10):
```tsx
function SltpConfirmDialog(props: {
  positionId: number;
  kind: "sl" | "tp";
  price: number;              // pre-filled, editable
  removing?: boolean;         // true for the double-click-remove case (price is 0)
  onConfirm: (price: number) => void;
  onCancel: () => void;
}): JSX.Element
```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/SltpConfirmDialog.test.tsx`:
```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import SltpConfirmDialog from "./SltpConfirmDialog";

it("pre-fills the dragged price and confirms with it unchanged", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="sl" price={1900.5}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText(/SL/i) as HTMLInputElement;
  expect(input.value).toBe("1900.5");

  fireEvent.click(screen.getByText(/konfirmasi/i));
  expect(onConfirm).toHaveBeenCalledWith(1900.5);
});

it("sends the edited value, not the original drag value", () => {
  const onConfirm = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="tp" price={1950}
    onConfirm={onConfirm} onCancel={() => {}} />);

  const input = screen.getByLabelText(/TP/i) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "1955.25" } });
  fireEvent.click(screen.getByText(/konfirmasi/i));

  expect(onConfirm).toHaveBeenCalledWith(1955.25);
});

it("shows removal copy and disables the price field when removing", () => {
  render(<SltpConfirmDialog positionId={5} kind="sl" price={0} removing
    onConfirm={() => {}} onCancel={() => {}} />);

  expect(screen.getByText(/tanpa stop-loss/i)).toBeTruthy();
});

it("calls onCancel and never onConfirm when Batal is clicked", () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(<SltpConfirmDialog positionId={5} kind="sl" price={1900}
    onConfirm={onConfirm} onCancel={onCancel} />);

  fireEvent.click(screen.getByText(/batal/i));
  expect(onCancel).toHaveBeenCalled();
  expect(onConfirm).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/SltpConfirmDialog.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/SltpConfirmDialog.tsx`:
```tsx
import { useState } from "react";
import { optNum } from "../lib/parse";

export default function SltpConfirmDialog(props: {
  positionId: number;
  kind: "sl" | "tp";
  price: number;
  removing?: boolean;
  onConfirm: (price: number) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(String(props.price));
  const [fieldError, setFieldError] = useState<string | null>(null);
  const label = props.kind === "sl" ? "SL" : "TP";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         onClick={props.onCancel}>
      <div className="glass max-w-sm w-full p-5" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-[15px] font-bold mb-1">
          {props.removing ? `Hapus ${label}?` : `Atur ${label} — posisi #${props.positionId}`}
        </h2>
        {props.removing ? (
          <p className="text-[12px] text-neg mb-3">
            Posisi jadi tanpa {label === "SL" ? "stop-loss" : "take-profit"}. Lanjutkan?
          </p>
        ) : (
          <label className="flex flex-col text-muted text-[10px] mb-3">
            {label}
            <input
              className="bg-white/5 rounded px-2 py-1 text-ink num"
              aria-label={label}
              value={value}
              onChange={(e) => { setValue(e.target.value); setFieldError(null); }}
            />
          </label>
        )}
        {fieldError && <div className="text-neg text-[11px] mb-2">{fieldError}</div>}
        <div className="flex justify-end gap-2">
          <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
            onClick={props.onCancel}>Batal</button>
          <button className="px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold"
            onClick={() => {
              if (props.removing) { props.onConfirm(0); return; }
              const n = optNum(value);
              if (n === null || Number.isNaN(n)) { setFieldError("angka tidak valid"); return; }
              props.onConfirm(n);
            }}>Konfirmasi</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/SltpConfirmDialog.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Full frontend gate + commit**

Run: `npx vitest run`, `npx tsc --noEmit` (from `frontend/`).
```bash
git add frontend/src/components/SltpConfirmDialog.tsx frontend/src/components/SltpConfirmDialog.test.tsx
git commit -m "feat(fe): SltpConfirmDialog - precision-edit step before live ConfirmModal"
```

---

### Task 10: Wire live mode in `Chart.tsx` + render `ConfirmModal`

**Files:**
- Modify: `frontend/src/pages/Chart.tsx`

**Interfaces:**
- Consumes: `useLiveCommand` (Task 8), `SltpConfirmDialog` (Task 9), `ConfirmModal` (existing, `frontend/src/components/ConfirmModal.tsx`).

- [ ] **Step 1: Add the hook, dialog state, and finish `handleSlTpChange`**

In `frontend/src/pages/Chart.tsx`, add imports:
```tsx
import { useLiveCommand } from "../hooks/useLiveCommand";
import SltpConfirmDialog from "../components/SltpConfirmDialog";
import ConfirmModal from "../components/ConfirmModal";
```
Add state near the other live-related state (after `const { status: liveStatus } = useLiveStatus();`):
```tsx
  const liveCmd = useLiveCommand();
  const [sltpDialog, setSltpDialog] = useState<
    { positionId: number; kind: "sl" | "tp"; price: number; removing?: boolean } | null
  >(null);
```
Replace the `handleSlTpChange` written in Task 7 with the complete version:
```tsx
  const handleSlTpChange = useCallback((positionId: number, change: { sl?: number; tp?: number }) => {
    if (replayOpen) {
      replay.modifySltp(positionId, change);
      return;
    }
    const kind: "sl" | "tp" = change.sl !== undefined ? "sl" : "tp";
    const price = (change.sl ?? change.tp)!;
    setSltpDialog({ positionId, kind, price, removing: price === 0 });
  }, [replayOpen, replay]);
```
Add the dialog + `ConfirmModal` render right before the closing tag of the page's root JSX (wherever the existing `return (...)` closes — add as siblings near the end, matching how other modals in this codebase are rendered at the page root):
```tsx
      {sltpDialog && (
        <SltpConfirmDialog
          positionId={sltpDialog.positionId}
          kind={sltpDialog.kind}
          price={sltpDialog.price}
          removing={sltpDialog.removing}
          onConfirm={(price) => {
            setSltpDialog(null);
            liveCmd.request(sltpDialog.positionId, "sltp", { [sltpDialog.kind]: price });
          }}
          onCancel={() => setSltpDialog(null)}
        />
      )}
      {liveCmd.preview && (
        <ConfirmModal
          preview={liveCmd.preview}
          submitting={liveCmd.submitting}
          error={liveCmd.error}
          onConfirm={liveCmd.confirm}
          onCancel={liveCmd.cancel}
        />
      )}
```

- [ ] **Step 2: Enable dragging on the live chart**

Update the `<CandleChart>` invocation: it already receives `onSlTpChange={handleSlTpChange}` from Task 7. No `draggablePositions` prop is needed for the live case — per Task 6's design, `CandleChart`'s internal live-fallback branch (driven by `props.live`) becomes draggable automatically whenever `onSlTpChange` is set and `draggablePositions` is `undefined` (which it is, outside `replayOpen`, since `draggableReplay` evaluates to `undefined` when `!replayOpen`).

- [ ] **Step 3: Full frontend gate + commit**

Run: `npx vitest run`, `npx tsc --noEmit`, `npm run build` (from `frontend/`).
```bash
git add frontend/src/pages/Chart.tsx
git commit -m "feat(fe): wire live SL/TP drag through SltpConfirmDialog + existing ConfirmModal"
```

---

### Task 11: Remove the old SL/TP form from `LivePositionCard.tsx`

**Files:**
- Modify: `frontend/src/components/LivePositionCard.tsx`

**Interfaces:** none new — pure removal.

- [ ] **Step 1: Remove the SL/TP inputs and button**

In `frontend/src/components/LivePositionCard.tsx`, delete the first `<div className="flex gap-2 items-end">...</div>` block inside the actions row — the one containing the SL input, TP input, and "Ubah SL/TP…" button (currently the first child of the `<div className="flex flex-wrap gap-3 items-end text-[12px]">` row). Also delete the two state declarations `const [sl, setSl] = useState("");` and `const [tp, setTp] = useState("");` — both are read only inside the block just removed. Keep `const [fieldError, setFieldError] = useState<string | null>(null);` and `const [vol, setVol] = useState("");` — both are still read by the Close / Tutup sebagian / Tambah buttons below.

Leave the `SL/TP` display line (`<div><span className="text-muted">SL/TP </span>...` in the info grid) untouched — that's read-only display of the current values, not the edit form, and still useful to see at a glance alongside the now-interactive chart.

- [ ] **Step 2: Manual verification**

Run `cd frontend && npm run build`, start the app, open `/live` with an active MT5 bridge or fixture data, and confirm: Close / Tutup sebagian / Tambah buttons still work exactly as before; the SL/TP number inputs and "Ubah SL/TP…" button are gone from the card.

- [ ] **Step 3: Full frontend gate + commit**

Run: `npx vitest run`, `npx tsc --noEmit` (from `frontend/`).
```bash
git add frontend/src/components/LivePositionCard.tsx
git commit -m "refactor(fe): remove manual SL/TP form from LivePositionCard (superseded by chart drag)"
```

---

### Task 12: Final full-repo gate + human visual pass sign-off

**Files:** none (verification only).

- [ ] **Step 1: Run every gate**

```bash
uv run pytest
uv run journal rebuild
cd frontend && npx vitest run && npx tsc --noEmit && npm run build
```
All must be green/succeed. Paste the actual output before calling this task done (per this project's Definition of Done in `CLAUDE.md` — not "looks right").

- [ ] **Step 2: Flag the PENDING HUMAN items**

This cannot be automated — note it explicitly rather than claiming completion:
1. In-browser click-through of replay SL/TP drag (open a training position with no SL/TP, drag from the entry line, confirm it resolves to SL or TP correctly per direction; drag an existing SL/TP line; double-click to remove one).
2. In-browser click-through of live SL/TP drag **with the MT5 bridge container running**: drag a real position's SL line, confirm `SltpConfirmDialog` shows the right pre-filled price, edit it, confirm, confirm again in the existing `ConfirmModal`, and verify the order actually reaches the broker (`journal live` logs / MT5 terminal) and that a too-close `stops_level` violation surfaces as an error instead of silently failing.
3. Manual regression pass on `/live`'s Close / Tutup sebagian / Tambah buttons (Task 8's hook extraction touched shared state for all of them).

- [ ] **Step 3: Update project memory**

This is a claude-mem memory-system update, not a code change — record in the project's memory (mirroring how `sltp-kiro-scrap-2026-07-31` and the chart-segment/spec memories were kept up to date): branch name, final commit, gate results, and the three PENDING HUMAN items above, so a future session picks this up correctly.

- [ ] **Step 4: Offer to push / open a PR**

Do not push or open a PR automatically — ask the user first (per this project's standing convention of local-merge-then-ask, seen throughout Spec A/B/C).
