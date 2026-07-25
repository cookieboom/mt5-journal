# Chart Phase D — Training / Replay Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TradingView-style bar-replay training mode to `/chart` where the user replays cached historical candles bar-by-bar, opens fake positions with SL/TP, and the backend evaluates them and stores per-session + cumulative scores in new, separate tables.

**Architecture:** A pure Python evaluator (`domain/replay_eval.py`) is the single source of truth for fills/SL-TP/P&L/R; a pure-DB store (`store/training_store.py`) persists sessions and positions; an orchestration service (`web/training.py`) composes the evaluator, the store, and the cached candle reader; thin JSON payloads (`web/api.py`) and routes (`web/app.py`) expose `/api/training/*`. The frontend adds an isolated replay mode overlaid on the existing Chart page — `useReplaySession` owns all replay state and never writes chart prefs; entering/exiting snapshots and restores the pre-training view.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), FastAPI, pytest · React 18, TypeScript, Vite, tailwind, react-router, lightweight-charts 5.2.0, vitest.

## Global Constraints

- **Rule 1 / 12 / M9:** never `import MetaTrader5` and no MT5 constants outside `adapter/`. The web/replay path reads only **cached** candles; missing ranges are filled via `candle_queue.request_candles` (queue), never a bridge call.
- **Rule 2:** training data lives in **new `training_*` tables** only; never touch `trades` / `deals_raw` / `orders_raw`. `journal rebuild` (`reconstruct.rebuild(conn)`) must leave training rows intact — proven by test.
- **Rule 3:** all `*_msc` are epoch-millisecond **integers, server-UTC** (offset 0). lightweight-charts needs UNIX **seconds** → `toSeconds()` divides by 1000 only at the chart boundary.
- **Rule 4:** `NULL` = unknown, `0` = "none set". Fake SL/TP use `0 = none set`; a position with no SL has `r_multiple = NULL` and is excluded from R stats.
- **Rule 5:** money/prices are `REAL`; compare with tolerance `abs(a-b) < 1e-9`, never `==`.
- **Account = USC (US cents):** every money figure is in `accounts.currency`; never a bare `$`. Prefer R-multiple (unit-free) over absolute P&L.
- **§8:** every statistic shows `n`; rate/average aggregates with `n < 20` are returned as `null` (mirroring `weekly_payload`) and greyed in the UI. `total_r` and `n` are always shown.
- **Phase C isolation:** chart settings persist under `app_prefs` key `"chart"`. Replay state/config MUST NOT read or write that key beyond *reading* `ChartSettings` for rendering.
- **Rule 9:** the tool evaluates decisions the user makes; it never suggests entries or scores "should I take this trade". No signal features.
- **Symbol normalisation:** `symbol_base` via `domain/symbols.py::to_base(symbol)`. Query candles/specs with the exact `symbol` (e.g. `XAUUSDc`); group by `symbol_base`.
- **Verification:** `uv run pytest` green (paste output), `npm --prefix frontend test` green, `npm --prefix frontend run build` 0 errors, `uv run journal rebuild` succeeds. After code changes run `graphify update .`.
- **Commit footer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014yiB4J8MzvWWUYLVN5ggWK
  ```

## File Structure

**Backend (create):**
- `src/journal/domain/replay_eval.py` — pure evaluator: `Bar`, `PositionState`, `FillEvent`, `step_bar`, `net_profit_usc`, `r_multiple`.
- `src/journal/store/migrations/006_training_tables.sql` — the two new tables.
- `src/journal/store/training_store.py` — pure-DB session/position CRUD + summary queries.
- `src/journal/web/training.py` — orchestration service (composes evaluator + store + candle reader + queue).
- `tests/test_replay_eval.py`, `tests/test_training_store.py`, `tests/test_training_service.py`.

**Backend (modify):**
- `src/journal/store/schema.sql` — add the two tables (byte-identical DDL to the migration).
- `src/journal/store/db.py:20` — `SCHEMA_VERSION = 5` → `6`.
- `src/journal/store/candles_store.py` — add `load_bars()` (extracted from `candles_payload`).
- `src/journal/web/api.py` — add training payloads; refactor `candles_payload` onto `load_bars`.
- `src/journal/web/app.py` — add `/api/training/*` routes.
- `tests/test_migrations.py` — version + new-table + rebuild-safety assertions.
- `tests/test_api.py` — training payload/route tests.

**Frontend (create):**
- `frontend/src/lib/replay.ts` — pure types + display helpers (`TrainingSession`, `TrainingPosition`, `StepEvent`, `Summary`, `clipToCursor`, `replayLines`, `unrealizedR`, `msPerStep`).
- `frontend/src/lib/replayApi.ts` — typed fetch wrappers for `/api/training/*`.
- `frontend/src/hooks/useReplaySession.ts` — replay state machine + playback loop.
- `frontend/src/components/ReplayConfigModal.tsx`, `ReplayControls.tsx`, `ReplayOrderTicket.tsx`, `ReplayPositions.tsx`, `ReplaySummary.tsx`.
- `frontend/src/lib/replay.test.ts`.

**Frontend (modify):**
- `frontend/src/lib/types.ts` — add `PriceLineSpec` (if not already present).
- `frontend/src/components/CandleChart.tsx` — optional `overlayLines` prop.
- `frontend/src/components/ChartToolbar.tsx` — a "▶ Replay" button.
- `frontend/src/pages/Chart.tsx` — replay-mode wiring, snapshot/restore, `REPLAY` badge.

---

## Task 1: Schema + migration 006 (training tables)

**Files:**
- Create: `src/journal/store/migrations/006_training_tables.sql`
- Modify: `src/journal/store/schema.sql` (append two tables after `app_prefs`, ~line 388), `src/journal/store/db.py:20`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: tables `training_sessions`, `training_positions` (columns per below); `SCHEMA_VERSION == 6`.

- [ ] **Step 1: Write the failing version test**

In `tests/test_migrations.py`, replace `test_schema_version_is_5` with:

```python
def test_schema_version_is_6():
    """Chart Phase D adds training_sessions + training_positions (migration 006)."""
    assert SCHEMA_VERSION == 6


def test_fresh_db_has_training_tables(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"training_sessions", "training_positions"} <= names
    finally:
        conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_migrations.py::test_schema_version_is_6 tests/test_migrations.py::test_fresh_db_has_training_tables -v`
Expected: FAIL (`SCHEMA_VERSION == 5`; tables absent).

- [ ] **Step 3: Write the migration file**

Create `src/journal/store/migrations/006_training_tables.sql`:

```sql
-- Migration 006 — training/replay tables (Chart Phase D).
--
-- Brings a v5 database forward to v6. ADDITIVE only: two new tables, no existing
-- table touched. The same DDL lives in schema.sql for fresh databases; the two
-- must stay byte-identical (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- Durable training data. NOT a chart cache and NOT derived from raw — `journal
-- rebuild` never touches it (it rebuilds only `trades`). No bridge. Money is USC.
-- All *_msc are epoch ms, server-UTC. sl/tp: 0 = none set (rule 4).
CREATE TABLE IF NOT EXISTS training_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    symbol_base     TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    range_start_msc INTEGER NOT NULL,
    range_end_msc   INTEGER NOT NULL,
    cursor_msc      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'ended')),
    created_at_msc  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS training_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL
                            REFERENCES training_sessions(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL CHECK (direction IN ('buy', 'sell')),
    volume              REAL NOT NULL,
    decision_msc        INTEGER NOT NULL,
    entry_msc           INTEGER,
    entry_price         REAL,
    sl                  REAL NOT NULL DEFAULT 0,   -- 0 = none set (rule 4)
    tp                  REAL NOT NULL DEFAULT 0,   -- 0 = none set (rule 4)
    close_requested_msc INTEGER,                   -- NULL = no manual close pending
    exit_msc            INTEGER,
    exit_price          REAL,
    exit_reason         TEXT CHECK (exit_reason IN ('tp','sl','manual','eod')),
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','open','closed')),
    net_profit          REAL,                      -- USC, signed; NULL until resolved
    r_multiple          REAL,                      -- NULL if no SL
    mae                 REAL,
    mfe                 REAL,
    mae_r               REAL,
    mfe_r               REAL,
    created_at_msc      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_training_positions_session
    ON training_positions (session_id);
CREATE INDEX IF NOT EXISTS idx_training_positions_status
    ON training_positions (status);
```

- [ ] **Step 4: Add the identical DDL to `schema.sql`**

Append the exact same two `CREATE TABLE` blocks and the two `CREATE INDEX` lines to the end of `src/journal/store/schema.sql` (after the `app_prefs` table). Copy them **verbatim** from the migration (the migration-vs-fresh test compares every table and column).

- [ ] **Step 5: Bump `SCHEMA_VERSION`**

In `src/journal/store/db.py:20`, change `SCHEMA_VERSION = 5` to `SCHEMA_VERSION = 6`.

- [ ] **Step 6: Run the migration + drift tests**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS — including the existing `test_migrated_db_matches_a_fresh_db`, `test_migrate_is_idempotent`, `test_migration_files_are_numbered_contiguously_from_2`. If drift fails, the `schema.sql` copy is not byte-identical to the migration; reconcile.

- [ ] **Step 7: Add the rebuild-safety test**

Append to `tests/test_migrations.py`:

```python
def test_training_rows_survive_rebuild(tmp_path):
    """journal rebuild rebuilds only `trades` from raw; training data is durable
    (rule 2), like app_prefs. A seeded training session must remain untouched."""
    from journal.domain.reconstruct import rebuild
    conn = connect(tmp_path / "journal.db")
    try:
        conn.execute(
            "INSERT INTO accounts (login, currency, first_seen_at) VALUES (0, 'USC', 1)"
        )
        conn.execute(
            "INSERT INTO training_sessions "
            "(symbol, symbol_base, timeframe, range_start_msc, range_end_msc, "
            " cursor_msc, status, created_at_msc) "
            "VALUES ('XAUUSDc','XAUUSD','M15',1000,2000,1000,'active',1)"
        )
        conn.commit()
        rebuild(conn)  # rebuilds trades from (empty) raw; must not touch training_*
        n = conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0]
        assert n == 1
    finally:
        conn.close()
```

- [ ] **Step 8: Run it**

Run: `uv run pytest tests/test_migrations.py::test_training_rows_survive_rebuild -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/journal/store/migrations/006_training_tables.sql src/journal/store/schema.sql src/journal/store/db.py tests/test_migrations.py
git commit -m "feat(chart-d): training_* tables (migration 006, SCHEMA_VERSION 6)"
```

---

## Task 2: Pure evaluator `domain/replay_eval.py`

The heart of Phase D. Pure, no DB, no bridge (rule 7). TDD.

**Files:**
- Create: `src/journal/domain/replay_eval.py`
- Test: `tests/test_replay_eval.py`

**Interfaces:**
- Produces:
  - `@dataclass Bar(time_msc:int, open:float, high:float, low:float, close:float)`
  - `@dataclass PositionState(id:int, direction:str, volume:float, decision_msc:int, sl:float, tp:float, status:str, entry_msc:int|None, entry_price:float|None, close_requested_msc:int|None, exit_msc:int|None=None, exit_price:float|None=None, exit_reason:str|None=None)`
  - `@dataclass FillEvent(position_id:int, kind:str, price:float, time_msc:int, reason:str|None)` — `kind` is `"fill"` or `"exit"`; `reason` is `"tp"|"sl"|"manual"` on exits, else `None`.
  - `step_bar(positions:list[PositionState], bar:Bar) -> list[FillEvent]` — mutates `positions` in place, returns events for THIS bar.
  - `net_profit_usc(direction:str, entry:float, exit:float, volume:float, tick_size:float, tick_value:float) -> float`
  - `r_multiple(direction:str, entry:float, exit:float, sl:float) -> float|None` — `None` if `sl == 0`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_replay_eval.py`:

```python
"""Pure replay evaluator — fixture-tested, no DB, no bridge (CLAUDE.md rule 7)."""
from __future__ import annotations

from journal.domain.replay_eval import (
    Bar, PositionState, step_bar, net_profit_usc, r_multiple,
)


def _pending(pid=1, direction="buy", sl=0.0, tp=0.0, decision_msc=1000, volume=0.1):
    return PositionState(
        id=pid, direction=direction, volume=volume, decision_msc=decision_msc,
        sl=sl, tp=tp, status="pending", entry_msc=None, entry_price=None,
        close_requested_msc=None,
    )


def _bar(t, o, h, l, c):
    return Bar(time_msc=t, open=o, high=h, low=l, close=c)


def test_pending_fills_at_next_bar_open():
    p = _pending(decision_msc=1000)
    # Same-time bar must NOT fill (needs strictly later); next bar fills at open.
    assert step_bar([p], _bar(1000, 10, 11, 9, 10)) == []
    assert p.status == "pending"
    ev = step_bar([p], _bar(2000, 12, 13, 11, 12))
    assert p.status == "open" and p.entry_price == 12 and p.entry_msc == 2000
    assert [e.kind for e in ev] == ["fill"]


def test_long_take_profit_hit_at_level():
    p = _pending(direction="buy", tp=13.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 9.5, 10))     # fills at 10
    ev = step_bar([p], _bar(3000, 11, 13.5, 10.5, 12))  # high 13.5 >= tp 13
    assert p.status == "closed" and p.exit_reason == "tp" and p.exit_price == 13.0
    assert [(e.kind, e.reason) for e in ev] == [("exit", "tp")]


def test_long_stop_loss_hit_at_level():
    p = _pending(direction="buy", sl=9.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 9.5, 10))     # fills at 10
    step_bar([p], _bar(3000, 9.8, 10, 8.5, 9))        # low 8.5 <= sl 9
    assert p.status == "closed" and p.exit_reason == "sl" and p.exit_price == 9.0


def test_both_hit_in_one_bar_is_pessimistic_sl_first():
    p = _pending(direction="buy", sl=9.0, tp=13.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10, 10, 10))         # fills at 10
    step_bar([p], _bar(3000, 10, 14, 8, 11))          # bar spans BOTH sl and tp
    assert p.exit_reason == "sl" and p.exit_price == 9.0


def test_entry_bar_itself_can_stop_out():
    # Fill at next bar open; that same bar's wick immediately hits the stop.
    p = _pending(direction="buy", sl=9.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 8.5, 9.2))     # fills at 10 AND low 8.5 <= 9
    assert p.status == "closed" and p.exit_reason == "sl" and p.exit_price == 9.0


def test_short_mirror():
    p = _pending(direction="sell", sl=11.0, tp=8.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10, 10, 10))         # fills at 10
    step_bar([p], _bar(3000, 10, 12, 9.5, 10))        # high 12 >= sl 11 → stop
    assert p.exit_reason == "sl" and p.exit_price == 11.0


def test_manual_close_fills_next_bar_open_and_beats_same_bar_wick():
    p = _pending(direction="buy", sl=9.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 9.5, 10))      # fills at 10
    p.close_requested_msc = 2000                       # requested at cursor bar 2000
    ev = step_bar([p], _bar(3000, 10.2, 11, 8.5, 9))  # open exit BEFORE the 8.5 wick
    assert p.exit_reason == "manual" and p.exit_price == 10.2
    assert [(e.kind, e.reason) for e in ev] == [("exit", "manual")]


def test_no_sl_never_stops_and_r_is_none():
    p = _pending(direction="buy", sl=0.0, tp=0.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10, 10, 10))
    step_bar([p], _bar(3000, 1, 10, 0.5, 2))          # huge adverse move, sl=0=none
    assert p.status == "open"
    assert r_multiple("buy", 10, 5, 0.0) is None


def test_net_profit_and_r_math_xauusd():
    # XAUUSDc: tick_size=0.001, tick_value=0.1 USC, volume 0.1.
    # +1.0 price move = 1000 ticks * 0.1 * 0.1 = 10.0 USC.
    assert abs(net_profit_usc("buy", 4000.0, 4001.0, 0.1, 0.001, 0.1) - 10.0) < 1e-9
    assert abs(net_profit_usc("sell", 4000.0, 4001.0, 0.1, 0.001, 0.1) + 10.0) < 1e-9
    # R = signed_move / |entry - sl|.
    assert abs(r_multiple("buy", 4000.0, 4002.0, 3999.0) - 2.0) < 1e-9
    assert abs(r_multiple("sell", 4000.0, 3998.0, 4001.0) - 2.0) < 1e-9
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_replay_eval.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the evaluator**

Create `src/journal/domain/replay_eval.py`:

```python
"""Pure replay evaluator — the single source of truth for fake-position fills,
SL/TP resolution, P&L and R during Chart Phase D training. No DB, no bridge, no
MT5 (CLAUDE.md rules 1, 7): it takes plain dataclasses and is fixture-testable.

Fill model: a decision made while bar N is the newest revealed bar creates a
PENDING position that fills at the OPEN of the first bar strictly later than the
decision. The entry bar itself is then evaluated for SL/TP (a gap can stop you
on the bar you entered on). When a single bar's wick reaches BOTH sl and tp,
the STOP fills first (pessimistic — OHLC cannot reveal true intra-bar order, and
an honest trainer never flatters). Exit price is the SL/TP level itself
(gap-through-level slippage is not modelled). A manual close is a market order
filled at the next bar's open, and executes at that open BEFORE the bar's wicks.

Money is USC (account currency); R is a unit-free ratio (rule 4: NULL when no SL).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Bar:
    time_msc: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class PositionState:
    id: int
    direction: str            # "buy" | "sell"
    volume: float
    decision_msc: int
    sl: float                 # 0.0 = none set (rule 4)
    tp: float                 # 0.0 = none set (rule 4)
    status: str               # "pending" | "open" | "closed"
    entry_msc: int | None
    entry_price: float | None
    close_requested_msc: int | None
    exit_msc: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None   # "tp" | "sl" | "manual" | "eod"


@dataclass
class FillEvent:
    position_id: int
    kind: str                 # "fill" | "exit"
    price: float
    time_msc: int
    reason: str | None        # exit: "tp"|"sl"|"manual"; fill: None


def _close(p: PositionState, price: float, time_msc: int, reason: str) -> FillEvent:
    p.status = "closed"
    p.exit_price = price
    p.exit_msc = time_msc
    p.exit_reason = reason
    return FillEvent(p.id, "exit", price, time_msc, reason)


def step_bar(positions: list[PositionState], bar: Bar) -> list[FillEvent]:
    """Advance every position by one revealed `bar`. Mutates `positions` in place
    and returns the fills/exits that happened ON this bar, in position order.

    Order per position: (1) fill if pending and this bar is strictly later than
    the decision; (2) if a manual close is pending, exit at this bar's OPEN
    (market order, ahead of the wicks); (3) otherwise resolve SL/TP against the
    bar's wicks, stop-first when both are inside the bar.
    """
    events: list[FillEvent] = []
    for p in positions:
        if p.status == "closed":
            continue

        if p.status == "pending":
            if bar.time_msc <= p.decision_msc:
                continue                       # not tradable until strictly later
            p.status = "open"
            p.entry_price = bar.open
            p.entry_msc = bar.time_msc
            events.append(FillEvent(p.id, "fill", bar.open, bar.time_msc, None))

        # p is now open (either already, or just filled above and evaluated same bar).
        if p.status != "open":
            continue

        if p.close_requested_msc is not None and bar.time_msc > p.close_requested_msc:
            events.append(_close(p, bar.open, bar.time_msc, "manual"))
            continue

        if p.direction == "buy":
            sl_hit = p.sl > 0 and bar.low <= p.sl
            tp_hit = p.tp > 0 and bar.high >= p.tp
        else:
            sl_hit = p.sl > 0 and bar.high >= p.sl
            tp_hit = p.tp > 0 and bar.low <= p.tp

        if sl_hit:                              # stop-first when both are hit
            events.append(_close(p, p.sl, bar.time_msc, "sl"))
        elif tp_hit:
            events.append(_close(p, p.tp, bar.time_msc, "tp"))

    return events


def _signed_move(direction: str, entry: float, exit: float) -> float:
    return (exit - entry) if direction == "buy" else (entry - exit)


def net_profit_usc(direction: str, entry: float, exit: float, volume: float,
                   tick_size: float, tick_value: float) -> float:
    """Signed P&L in account currency (USC). `tick_value` is per lot per tick,
    already in account currency (symbol_specs). Never a bare '$'."""
    ticks = _signed_move(direction, entry, exit) / tick_size
    return ticks * tick_value * volume


def r_multiple(direction: str, entry: float, exit: float, sl: float) -> float | None:
    """Unit-free R = signed move / initial risk distance. NULL when no SL is set
    (sl == 0) or the SL sits exactly at entry (known-zero risk — Trap 6 shape)."""
    risk = abs(entry - sl)
    if sl == 0 or risk < 1e-9:
        return None
    return _signed_move(direction, entry, exit) / risk
```

- [ ] **Step 4: Run to verify all pass**

Run: `uv run pytest tests/test_replay_eval.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/replay_eval.py tests/test_replay_eval.py
git commit -m "feat(chart-d): pure replay evaluator (fills, SL/TP, P&L, R)"
```

---

## Task 3: Extract `load_bars` (DRY the candle read)

The step orchestration needs the same "native, else aggregate from M1" bar read that `candles_payload` does. Extract it so both share one implementation.

**Files:**
- Modify: `src/journal/store/candles_store.py` (add `load_bars`), `src/journal/web/api.py` (`candles_payload` uses it)
- Test: `tests/test_candles_store.py`

**Interfaces:**
- Produces: `load_bars(conn, symbol:str, timeframe:str, from_ms:int, to_ms:int) -> list[Candle]` — bars in `[from_ms, to_ms]`, ascending; native if stored, else aggregated from M1 over bucket-aligned bounds, else `[]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_candles_store.py`:

```python
def test_load_bars_returns_native_rows(tmp_path):
    from journal.store.db import connect
    from journal.store import candles_store as cs
    from journal.adapter.base import Candle
    conn = connect(tmp_path / "j.db")
    try:
        c = Candle(time_msc=1_700_000_000_000, open=1, high=2, low=0.5, close=1.5,
                   tick_volume=3, spread=0, real_volume=0)
        cs.insert_candle(conn, "XAUUSDc", "M5", c)
        conn.commit()
        bars = cs.load_bars(conn, "XAUUSDc", "M5", 1_700_000_000_000, 1_700_000_000_000)
        assert len(bars) == 1 and bars[0].close == 1.5
    finally:
        conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_candles_store.py::test_load_bars_returns_native_rows -v`
Expected: FAIL (`load_bars` undefined).

- [ ] **Step 3: Add `load_bars` to `candles_store.py`**

Add near the top of `src/journal/store/candles_store.py` (after `row_to_candle`), and import resample lazily inside to avoid any import-order surprise:

```python
def load_bars(conn: sqlite3.Connection, symbol: str, timeframe: str,
              from_ms: int, to_ms: int) -> list["Candle"]:
    """Bars in [from_ms, to_ms], ascending: native rows if stored, else
    aggregated from M1 over BUCKET-ALIGNED bounds (resample_m1's coverage guard
    assumes it is handed every M1 bar for any bucket it may emit), else []. Pure
    DB — no bridge (M9 boundary). The single bar-read shared by the /api/candles
    payload and the Phase D replay step."""
    from ..domain.resample import resample_m1, bucket_start, timeframe_ms

    native = read_candles(conn, symbol, timeframe, from_ms, to_ms)
    if native:
        return [row_to_candle(r) for r in native]
    if timeframe == "M1":
        return []
    lo = bucket_start(from_ms, timeframe)
    hi = bucket_start(to_ms, timeframe) + timeframe_ms(timeframe) - 1
    m1 = read_candles(conn, symbol, "M1", lo, hi)
    if not m1:
        return []
    return resample_m1([row_to_candle(r) for r in m1], timeframe,
                       covered=read_coverage(conn, symbol, "M1"))
```

- [ ] **Step 4: Refactor `candles_payload` onto it**

In `src/journal/web/api.py`, replace the `native/elif/else` bar-loading block inside `candles_payload` (lines ~139-157) with:

```python
    bars = cs.load_bars(conn, symbol, timeframe, from_ms, to_ms)
```

Remove the now-unused `from ..domain.resample import resample_m1, bucket_start, timeframe_ms` import at the top of `api.py` **only if** nothing else there uses it (grep first). Keep `TIMEFRAMES`, `candle_queue`, `cs`, `views`.

- [ ] **Step 5: Run candle + api tests**

Run: `uv run pytest tests/test_candles_store.py tests/test_api.py -v`
Expected: PASS (the existing candle-payload tests still pass through the extracted function).

- [ ] **Step 6: Commit**

```bash
git add src/journal/store/candles_store.py src/journal/web/api.py tests/test_candles_store.py
git commit -m "refactor(chart-d): extract candles_store.load_bars, reuse in candles_payload"
```

---

## Task 4: Pure-DB `store/training_store.py`

**Files:**
- Create: `src/journal/store/training_store.py`
- Test: `tests/test_training_store.py`

**Interfaces:**
- Consumes: `db.now_ms`.
- Produces (all take `conn` first):
  - `create_session(conn, *, symbol, symbol_base, timeframe, range_start_msc, range_end_msc, cursor_msc) -> int`
  - `get_session(conn, session_id) -> sqlite3.Row | None`
  - `list_sessions(conn, status: str | None = None) -> list[sqlite3.Row]`
  - `delete_session(conn, session_id) -> None`
  - `update_cursor(conn, session_id, cursor_msc) -> None`
  - `set_session_status(conn, session_id, status) -> None`
  - `insert_position(conn, *, session_id, direction, volume, decision_msc, sl, tp) -> int`
  - `list_positions(conn, session_id) -> list[sqlite3.Row]`
  - `active_positions(conn, session_id) -> list[sqlite3.Row]` — status in (`pending`,`open`)
  - `get_position(conn, position_id) -> sqlite3.Row | None`
  - `mark_fill(conn, position_id, *, entry_msc, entry_price) -> None`
  - `request_close(conn, position_id, close_requested_msc) -> None`
  - `mark_close(conn, position_id, *, exit_msc, exit_price, exit_reason, net_profit, r_multiple, mae, mfe, mae_r, mfe_r) -> None`
  - `career_summary(conn) -> dict`, `session_summary(conn, session_id) -> dict` — `{n, win_rate, avg_r, total_r, avg_mae_r, avg_mfe_r}`, rates/averages `None` when `n < 20` (§8); `total_r` and `n` always present.

- [ ] **Step 1: Write failing tests**

Create `tests/test_training_store.py`:

```python
"""training_store — pure DB CRUD + §8-gated summaries. No bridge, no MT5."""
from __future__ import annotations

import pytest

from journal.store.db import connect
from journal.store import training_store as ts


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _session(conn):
    return ts.create_session(
        conn, symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M15",
        range_start_msc=1000, range_end_msc=9000, cursor_msc=1000,
    )


def test_session_roundtrip_and_status(conn):
    sid = _session(conn)
    row = ts.get_session(conn, sid)
    assert row["symbol"] == "XAUUSDc" and row["status"] == "active"
    ts.update_cursor(conn, sid, 2000)
    ts.set_session_status(conn, sid, "ended")
    row = ts.get_session(conn, sid)
    assert row["cursor_msc"] == 2000 and row["status"] == "ended"


def test_position_lifecycle(conn):
    sid = _session(conn)
    pid = ts.insert_position(conn, session_id=sid, direction="buy", volume=0.1,
                             decision_msc=1000, sl=3999.0, tp=4002.0)
    assert ts.get_position(conn, pid)["status"] == "pending"
    ts.mark_fill(conn, pid, entry_msc=2000, entry_price=4000.0)
    assert ts.get_position(conn, pid)["status"] == "open"
    ts.mark_close(conn, pid, exit_msc=3000, exit_price=4002.0, exit_reason="tp",
                  net_profit=20.0, r_multiple=2.0, mae=0.5, mfe=2.0,
                  mae_r=0.5, mfe_r=2.0)
    row = ts.get_position(conn, pid)
    assert row["status"] == "closed" and row["exit_reason"] == "tp"
    assert len(ts.active_positions(conn, sid)) == 0


def test_delete_session_cascades_positions(conn):
    sid = _session(conn)
    ts.insert_position(conn, session_id=sid, direction="buy", volume=0.1,
                       decision_msc=1000, sl=0.0, tp=0.0)
    ts.delete_session(conn, sid)
    assert ts.get_session(conn, sid) is None
    assert ts.list_positions(conn, sid) == []


def test_summary_is_section8_gated(conn):
    sid = _session(conn)
    # 3 closed winners → n=3 < 20, so rates/averages are suppressed (null),
    # but total_r and n are always present.
    for _ in range(3):
        pid = ts.insert_position(conn, session_id=sid, direction="buy", volume=0.1,
                                 decision_msc=1000, sl=3999.0, tp=4002.0)
        ts.mark_fill(conn, pid, entry_msc=2000, entry_price=4000.0)
        ts.mark_close(conn, pid, exit_msc=3000, exit_price=4002.0, exit_reason="tp",
                      net_profit=20.0, r_multiple=2.0, mae=0.0, mfe=2.0,
                      mae_r=0.0, mfe_r=2.0)
    s = ts.career_summary(conn)
    assert s["n"] == 3
    assert s["win_rate"] is None and s["avg_r"] is None      # §8: n < 20
    assert abs(s["total_r"] - 6.0) < 1e-9                     # always shown
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_training_store.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the store**

Create `src/journal/store/training_store.py`:

```python
"""training_store — pure DB access for Chart Phase D. Sessions and fake positions
live here and NOWHERE near `trades`/raw (CLAUDE.md rule 2). No MT5 adapter import
(M9 boundary, rules 1/12). Money is USC; R is unit-free (rule 4). Summaries follow
§8: rate/average metrics are null when n < 20; `n` and `total_r` always show.

Only CLOSED positions with a non-null `net_profit` count toward a summary — an
`eod` (unresolved) or never-filled position is excluded (unknown outcome, rule 4).
"""
from __future__ import annotations

import sqlite3

from .db import now_ms

_MIN_N = 20   # §8 sample floor for rate/average metrics


def create_session(conn: sqlite3.Connection, *, symbol: str, symbol_base: str,
                   timeframe: str, range_start_msc: int, range_end_msc: int,
                   cursor_msc: int) -> int:
    cur = conn.execute(
        "INSERT INTO training_sessions "
        "(symbol, symbol_base, timeframe, range_start_msc, range_end_msc, "
        " cursor_msc, status, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
        (symbol, symbol_base, timeframe, range_start_msc, range_end_msc,
         cursor_msc, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM training_sessions WHERE id = ?", (session_id,)
    ).fetchone()


def list_sessions(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute(
            "SELECT * FROM training_sessions ORDER BY id DESC"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM training_sessions WHERE status = ? ORDER BY id DESC",
        (status,),
    ).fetchall()


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute("DELETE FROM training_sessions WHERE id = ?", (session_id,))
    conn.commit()


def update_cursor(conn: sqlite3.Connection, session_id: int, cursor_msc: int) -> None:
    conn.execute(
        "UPDATE training_sessions SET cursor_msc = ? WHERE id = ?",
        (cursor_msc, session_id),
    )
    conn.commit()


def set_session_status(conn: sqlite3.Connection, session_id: int, status: str) -> None:
    conn.execute(
        "UPDATE training_sessions SET status = ? WHERE id = ?", (status, session_id)
    )
    conn.commit()


def insert_position(conn: sqlite3.Connection, *, session_id: int, direction: str,
                    volume: float, decision_msc: int, sl: float, tp: float) -> int:
    cur = conn.execute(
        "INSERT INTO training_positions "
        "(session_id, direction, volume, decision_msc, sl, tp, status, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (session_id, direction, volume, decision_msc, sl, tp, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_positions(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM training_positions WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()


def active_positions(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM training_positions WHERE session_id = ? "
        "AND status IN ('pending','open') ORDER BY id",
        (session_id,),
    ).fetchall()


def get_position(conn: sqlite3.Connection, position_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM training_positions WHERE id = ?", (position_id,)
    ).fetchone()


def mark_fill(conn: sqlite3.Connection, position_id: int, *, entry_msc: int,
              entry_price: float) -> None:
    conn.execute(
        "UPDATE training_positions SET status = 'open', entry_msc = ?, "
        "entry_price = ? WHERE id = ?",
        (entry_msc, entry_price, position_id),
    )
    conn.commit()


def request_close(conn: sqlite3.Connection, position_id: int,
                  close_requested_msc: int) -> None:
    conn.execute(
        "UPDATE training_positions SET close_requested_msc = ? "
        "WHERE id = ? AND status = 'open'",
        (close_requested_msc, position_id),
    )
    conn.commit()


def mark_close(conn: sqlite3.Connection, position_id: int, *, exit_msc: int,
               exit_price: float | None, exit_reason: str,
               net_profit: float | None, r_multiple: float | None,
               mae: float | None, mfe: float | None,
               mae_r: float | None, mfe_r: float | None) -> None:
    conn.execute(
        "UPDATE training_positions SET status = 'closed', exit_msc = ?, "
        "exit_price = ?, exit_reason = ?, net_profit = ?, r_multiple = ?, "
        "mae = ?, mfe = ?, mae_r = ?, mfe_r = ? WHERE id = ?",
        (exit_msc, exit_price, exit_reason, net_profit, r_multiple,
         mae, mfe, mae_r, mfe_r, position_id),
    )
    conn.commit()


def _summary(rows: list[sqlite3.Row]) -> dict:
    """Aggregate CLOSED, resolved (non-null net_profit) positions. §8: rate and
    average metrics are null below _MIN_N; `n` and `total_r` always show."""
    resolved = [r for r in rows if r["net_profit"] is not None]
    n = len(resolved)
    r_vals = [r["r_multiple"] for r in resolved if r["r_multiple"] is not None]
    mae_vals = [r["mae_r"] for r in resolved if r["mae_r"] is not None]
    mfe_vals = [r["mfe_r"] for r in resolved if r["mfe_r"] is not None]
    total_r = sum(r_vals)
    if n < _MIN_N:
        return {"n": n, "win_rate": None, "avg_r": None, "total_r": total_r,
                "avg_mae_r": None, "avg_mfe_r": None}
    wins = sum(1 for r in resolved if r["net_profit"] > 0)
    return {
        "n": n,
        "win_rate": wins / n,
        "avg_r": (total_r / len(r_vals)) if r_vals else None,
        "total_r": total_r,
        "avg_mae_r": (sum(mae_vals) / len(mae_vals)) if mae_vals else None,
        "avg_mfe_r": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
    }


def session_summary(conn: sqlite3.Connection, session_id: int) -> dict:
    return _summary(list(conn.execute(
        "SELECT net_profit, r_multiple, mae_r, mfe_r FROM training_positions "
        "WHERE session_id = ? AND status = 'closed'", (session_id,),
    ).fetchall()))


def career_summary(conn: sqlite3.Connection) -> dict:
    return _summary(list(conn.execute(
        "SELECT net_profit, r_multiple, mae_r, mfe_r FROM training_positions "
        "WHERE status = 'closed'"
    ).fetchall()))
```

- [ ] **Step 4: Run to verify all pass**

Run: `uv run pytest tests/test_training_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/training_store.py tests/test_training_store.py
git commit -m "feat(chart-d): training_store (session/position CRUD, §8 summaries)"
```

---

## Task 5: Orchestration service `web/training.py`

Composes the pure evaluator, the store, the cached candle reader, and the fill queue. This is where fills/exits get money, R, and MAE/MFE and are persisted.

**Files:**
- Create: `src/journal/web/training.py`
- Test: `tests/test_training_service.py`

**Interfaces:**
- Consumes: `training_store` (Task 4), `replay_eval` (Task 2), `candles_store.load_bars` + `read_candles` (Task 3), `candle_queue.request_candles`, `domain.excursion.compute_excursion`, `domain.symbols.to_base`, `domain.resample.timeframe_ms`, `adapter.base.TIMEFRAMES`.
- Produces (all take `conn` first; all return plain dict/list, JSON-safe):
  - `create_session(conn, *, symbol, timeframe, range_start_msc, range_end_msc, cursor_start_msc=None) -> dict` — validates tf ∈ TIMEFRAMES and `range_start ≤ cursor ≤ range_end`; enqueues a candle fill; returns `{"session": <row-dict>, "pending": bool}`.
  - `session_view(conn, session_id) -> dict | None` — `{"session":..., "positions":[...]}`.
  - `list_sessions_view(conn, status=None) -> list[dict]`.
  - `open_position(conn, session_id, *, direction, volume, sl, tp) -> dict` — pending at cursor.
  - `close_position(conn, session_id, position_id) -> dict` — sets close-requested at cursor.
  - `step(conn, session_id, n) -> dict` — `{"cursor_msc":int, "events":[...], "positions":[...]}`.
  - `end_session(conn, session_id) -> dict` — eod-resolve open/pending, status ended.
  - `career_summary(conn) -> dict` (thin passthrough of `training_store.career_summary`).

- [ ] **Step 1: Write failing tests**

Create `tests/test_training_service.py`:

```python
"""training service — orchestration over cached candles + pure evaluator.
Seeds candles directly (no bridge). Verifies fills, TP resolution, USC P&L, R,
and eod handling end to end."""
from __future__ import annotations

import pytest

from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candles_store as cs
from journal.web import training as tr


def _seed_specs(conn):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, tick_size, tick_value, "
        "contract_size, fetched_at) VALUES ('XAUUSDc','XAUUSD',0.001,0.1,1.0,1)"
    )
    conn.commit()


def _seed_m15(conn, bars):
    """bars: list of (t, o, h, l, c). Records coverage so load_bars reads native."""
    for t, o, h, l, c in bars:
        cs.insert_candle(conn, "XAUUSDc", "M15",
                         Candle(time_msc=t, open=o, high=h, low=l, close=c,
                                tick_volume=1, spread=0, real_volume=0))
    cs.record_coverage(conn, "XAUUSDc", "M15", bars[0][0], bars[-1][0])
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def test_open_step_take_profit_pnl_and_r(conn):
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000  # M15 ms
    bars = [
        (t0,          4000, 4000, 4000, 4000),
        (t0 + tf,     4000, 4001, 3999, 4000),   # fill bar (open 4000)
        (t0 + 2 * tf, 4001, 4003, 4000, 4002),   # high 4003 >= tp 4002 → TP
    ]
    _seed_m15(conn, bars)
    created = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                range_start_msc=t0, range_end_msc=t0 + 2 * tf,
                                cursor_start_msc=t0)
    sid = created["session"]["id"]
    # Decide at the first bar (cursor = t0), SL 3998, TP 4002.
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=3998.0, tp=4002.0)
    tr.step(conn, sid, 1)   # reveal fill bar → fills at 4000
    out = tr.step(conn, sid, 1)   # reveal TP bar → closes at 4002
    pos = out["positions"][0]
    assert pos["status"] == "closed" and pos["exit_reason"] == "tp"
    assert abs(pos["exit_price"] - 4002.0) < 1e-9
    # +2.0 move * (1/0.001) ticks * 0.1 tick_value * 0.1 vol = 20 USC.
    assert abs(pos["net_profit"] - 20.0) < 1e-9
    # R = 2.0 / |4000 - 3998| = 1.0.
    assert abs(pos["r_multiple"] - 1.0) < 1e-9


def test_no_sl_has_null_r_but_has_pnl(conn):
    _seed_specs(conn)
    t0 = 1_700_000_000_000
    tf = 900_000
    _seed_m15(conn, [
        (t0,      4000, 4000, 4000, 4000),
        (t0 + tf, 4000, 4000, 4000, 4000),
        (t0 + 2 * tf, 4005, 4005, 4005, 4005),
    ])
    created = tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                range_start_msc=t0, range_end_msc=t0 + 2 * tf,
                                cursor_start_msc=t0)
    sid = created["session"]["id"]
    tr.open_position(conn, sid, direction="buy", volume=0.1, sl=0.0, tp=0.0)
    tr.step(conn, sid, 1)               # fills at 4000
    tr.step(conn, sid, 1)               # runs to end, still open
    out = tr.end_session(conn, sid)
    pos = out["positions"][0]
    assert pos["exit_reason"] == "eod" and pos["r_multiple"] is None
    assert pos["net_profit"] is None    # unresolved → excluded from stats


def test_create_rejects_bad_timeframe(conn):
    with pytest.raises(ValueError):
        tr.create_session(conn, symbol="XAUUSDc", timeframe="M7",
                          range_start_msc=1, range_end_msc=2, cursor_start_msc=1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_training_service.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the service**

Create `src/journal/web/training.py`:

```python
"""Chart Phase D orchestration — the impure glue that turns replay decisions into
persisted, scored fake trades. It composes the PURE evaluator (domain/replay_eval),
the pure-DB store (store/training_store), the cached candle reader
(store/candles_store.load_bars — never the bridge, M9 boundary), and the fill
queue (store/candle_queue). Money is USC; R and MAE/MFE reuse the same pure
helpers the real pipeline uses (domain/excursion).

Fill/exit TIMING and PRICE come from replay_eval.step_bar; this module adds the
money (needs symbol_specs) and the excursion (needs candle rows) at close time.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import TIMEFRAMES
from ..domain.excursion import compute_excursion
from ..domain.resample import timeframe_ms
from ..domain.symbols import to_base
from ..domain import replay_eval as ev
from ..store import candle_queue
from ..store import candles_store as cs
from ..store import training_store as ts


def _row(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}


def _positions(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    return [dict(_row(r)) for r in ts.list_positions(conn, session_id)]


def _specs(conn: sqlite3.Connection, symbol: str) -> tuple[float, float] | None:
    r = conn.execute(
        "SELECT tick_size, tick_value FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if r is None or r["tick_size"] in (None, 0) or r["tick_value"] is None:
        return None
    return float(r["tick_size"]), float(r["tick_value"])


def create_session(conn: sqlite3.Connection, *, symbol: str, timeframe: str,
                   range_start_msc: int, range_end_msc: int,
                   cursor_start_msc: int | None = None) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")
    if range_start_msc > range_end_msc:
        raise ValueError("range_start_msc must be <= range_end_msc")
    cursor = range_start_msc if cursor_start_msc is None else cursor_start_msc
    if not (range_start_msc <= cursor <= range_end_msc):
        raise ValueError("cursor_start_msc must lie within [range_start_msc, range_end_msc]")

    sid = ts.create_session(
        conn, symbol=symbol, symbol_base=to_base(symbol), timeframe=timeframe,
        range_start_msc=range_start_msc, range_end_msc=range_end_msc,
        cursor_msc=cursor,
    )
    # Ensure the whole replay range is cached; the web NEVER touches the bridge —
    # it enqueues and `journal live` drains (returns 0 when already covered).
    req = candle_queue.request_candles(conn, symbol, timeframe,
                                       range_start_msc, range_end_msc)
    return {"session": _row(ts.get_session(conn, sid)), "pending": req != 0}


def session_view(conn: sqlite3.Connection, session_id: int) -> dict | None:
    s = ts.get_session(conn, session_id)
    if s is None:
        return None
    return {"session": _row(s), "positions": _positions(conn, session_id)}


def list_sessions_view(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    return [_row(r) for r in ts.list_sessions(conn, status)]


def open_position(conn: sqlite3.Connection, session_id: int, *, direction: str,
                  volume: float, sl: float, tp: float) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    if direction not in ("buy", "sell"):
        raise ValueError("direction must be 'buy' or 'sell'")
    pid = ts.insert_position(conn, session_id=session_id, direction=direction,
                             volume=volume, decision_msc=s["cursor_msc"],
                             sl=sl, tp=tp)
    return _row(ts.get_position(conn, pid))


def close_position(conn: sqlite3.Connection, session_id: int, position_id: int) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    ts.request_close(conn, position_id, s["cursor_msc"])
    return _row(ts.get_position(conn, position_id))


def _to_state(r: sqlite3.Row) -> ev.PositionState:
    return ev.PositionState(
        id=r["id"], direction=r["direction"], volume=r["volume"],
        decision_msc=r["decision_msc"], sl=r["sl"], tp=r["tp"], status=r["status"],
        entry_msc=r["entry_msc"], entry_price=r["entry_price"],
        close_requested_msc=r["close_requested_msc"],
    )


def _resolve_close(conn: sqlite3.Connection, symbol: str, timeframe: str,
                   state: ev.PositionState) -> None:
    """Persist a just-closed position with money, R, and MAE/MFE. Reuses the same
    pure helpers the real pipeline uses; degrades to null money if no symbol_specs."""
    net = r = mae = mfe = mae_r = mfe_r = None
    specs = _specs(conn, symbol)
    if specs is not None and state.entry_price is not None and state.exit_price is not None:
        tick_size, tick_value = specs
        net = ev.net_profit_usc(state.direction, state.entry_price, state.exit_price,
                                state.volume, tick_size, tick_value)
    if state.entry_price is not None and state.exit_price is not None:
        r = ev.r_multiple(state.direction, state.entry_price, state.exit_price, state.sl)
    if state.entry_msc is not None and state.exit_msc is not None:
        rows = conn.execute(
            "SELECT time_msc, low, high FROM candles WHERE symbol = ? AND "
            "timeframe = ? AND time_msc BETWEEN ? AND ? ORDER BY time_msc",
            (symbol, timeframe, state.entry_msc, state.exit_msc),
        ).fetchall()
        mae, mfe = compute_excursion(
            [(x["time_msc"], x["low"], x["high"]) for x in rows],
            state.entry_msc, state.exit_msc, state.entry_price, state.direction,
        )
        risk = abs(state.entry_price - state.sl) if state.sl else None
        if risk:  # truthy: not None and not 0.0 (Trap 6 shape)
            if mae is not None:
                mae_r = mae / risk
            if mfe is not None:
                mfe_r = mfe / risk
    ts.mark_close(conn, state.id, exit_msc=state.exit_msc, exit_price=state.exit_price,
                  exit_reason=state.exit_reason, net_profit=net, r_multiple=r,
                  mae=mae, mfe=mfe, mae_r=mae_r, mfe_r=mfe_r)


def step(conn: sqlite3.Connection, session_id: int, n: int = 1) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    if n < 1:
        raise ValueError("n must be >= 1")
    symbol, tf = s["symbol"], s["timeframe"]
    cursor, range_end = s["cursor_msc"], s["range_end_msc"]

    # The next n revealed bars are the first n with time_msc > cursor, up to range_end.
    hi = min(range_end, cursor + timeframe_ms(tf) * (n + 2))
    upcoming = [b for b in cs.load_bars(conn, symbol, tf, cursor + 1, hi)
                if b.time_msc > cursor][:n]

    states = [_to_state(r) for r in ts.active_positions(conn, session_id)]
    by_id = {st.id: st for st in states}
    all_events: list[dict] = []
    for bar in upcoming:
        events = ev.step_bar(states, ev.Bar(bar.time_msc, bar.open, bar.high,
                                            bar.low, bar.close))
        for e in events:
            st = by_id[e.position_id]
            if e.kind == "fill":
                ts.mark_fill(conn, st.id, entry_msc=st.entry_msc, entry_price=st.entry_price)
            else:  # exit
                _resolve_close(conn, symbol, tf, st)
            all_events.append({"position_id": e.position_id, "kind": e.kind,
                               "price": e.price, "time_msc": e.time_msc, "reason": e.reason})
        cursor = bar.time_msc

    if not upcoming:
        cursor = range_end   # reached the end of the range; nothing left to reveal
    ts.update_cursor(conn, session_id, cursor)
    return {"cursor_msc": cursor, "events": all_events,
            "positions": _positions(conn, session_id)}


def end_session(conn: sqlite3.Connection, session_id: int) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    for r in ts.active_positions(conn, session_id):
        # Unresolved at end of range: exit_reason 'eod', no money/R (unknown, rule 4).
        ts.mark_close(conn, r["id"], exit_msc=s["cursor_msc"], exit_price=None,
                      exit_reason="eod", net_profit=None, r_multiple=None,
                      mae=None, mfe=None, mae_r=None, mfe_r=None)
    ts.set_session_status(conn, session_id, "ended")
    return session_view(conn, session_id)


def career_summary(conn: sqlite3.Connection) -> dict:
    return ts.career_summary(conn)
```

- [ ] **Step 4: Run to verify all pass**

Run: `uv run pytest tests/test_training_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/training.py tests/test_training_service.py
git commit -m "feat(chart-d): training orchestration service (step, fill, close, eod)"
```

---

## Task 6: API payloads + routes

**Files:**
- Modify: `src/journal/web/api.py` (add training payloads), `src/journal/web/app.py` (add routes)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `web.training` (Task 5).
- Produces routes:
  - `POST /api/training/sessions` body `{symbol, timeframe, range_start_msc, range_end_msc, cursor_start_msc?}`
  - `GET /api/training/sessions` (optional `?status=`)
  - `GET /api/training/sessions/{id}`
  - `DELETE /api/training/sessions/{id}`
  - `POST /api/training/sessions/{id}/step` body `{n?}`
  - `POST /api/training/sessions/{id}/positions` body `{direction, volume, sl?, tp?}`
  - `POST /api/training/sessions/{id}/positions/{pid}/close`
  - `POST /api/training/sessions/{id}/end`
  - `GET /api/training/summary`

- [ ] **Step 1: Write a failing payload test**

Append to `tests/test_api.py`:

```python
def test_training_session_create_and_summary(conn):
    from journal.web import api as _api
    from journal.web import training as _tr
    created = _tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                 range_start_msc=1000, range_end_msc=9000,
                                 cursor_start_msc=1000)
    sid = created["session"]["id"]
    view = _api.to_jsonable(_tr.session_view(conn, sid))
    assert view["session"]["symbol"] == "XAUUSDc"
    assert view["positions"] == []
    summary = _api.to_jsonable(_tr.career_summary(conn))
    assert summary["n"] == 0 and summary["total_r"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api.py::test_training_session_create_and_summary -v`
Expected: FAIL initially only if `training` import path differs; if it passes immediately (service already exists), proceed — this test guards the JSON-safety contract.

- [ ] **Step 3: Add the routes to `app.py`**

In `src/journal/web/app.py`, add `from . import training` **and** `from ..store import training_store` to the imports (next to `from . import api` / `from ..store import prefs_store`), and add this route group **before** the SPA catch-all (i.e. before the `# --------- SPA (React)` comment, after the chart-prefs routes):

```python
    # --------------------------------------------------------- training (Phase D)
    # Replay/training. Pure DB + cached candles; never the bridge (M9 boundary).
    # Results live in training_* tables, untouched by `journal rebuild` (rule 2).
    @app.post("/api/training/sessions")
    def api_training_create(
        symbol: str = Body(...),
        timeframe: str = Body(...),
        range_start_msc: int = Body(...),
        range_end_msc: int = Body(...),
        cursor_start_msc: int | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            out = training.create_session(
                conn, symbol=symbol, timeframe=timeframe,
                range_start_msc=range_start_msc, range_end_msc=range_end_msc,
                cursor_start_msc=cursor_start_msc,
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.get("/api/training/sessions")
    def api_training_list(status: str | None = None,
                          conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.to_jsonable(training.list_sessions_view(conn, status)))

    @app.get("/api/training/sessions/{session_id}")
    def api_training_get(session_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        view = training.session_view(conn, session_id)
        if view is None:
            return JSONResponse({"error": f"no training session {session_id}"},
                                status_code=404)
        return JSONResponse(api.to_jsonable(view))

    @app.delete("/api/training/sessions/{session_id}")
    def api_training_delete(session_id: int,
                            conn: sqlite3.Connection = Depends(get_conn)):
        training_store.delete_session(conn, session_id)
        return JSONResponse({"ok": True})

    @app.post("/api/training/sessions/{session_id}/step")
    def api_training_step(session_id: int, n: int = Body(1, embed=True),
                          conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = training.step(conn, session_id, n)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.post("/api/training/sessions/{session_id}/positions")
    def api_training_open(
        session_id: int,
        direction: str = Body(...),
        volume: float = Body(...),
        sl: float = Body(0.0),
        tp: float = Body(0.0),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            pos = training.open_position(conn, session_id, direction=direction,
                                         volume=volume, sl=sl, tp=tp)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(pos))

    @app.post("/api/training/sessions/{session_id}/positions/{pid}/close")
    def api_training_close(session_id: int, pid: int,
                           conn: sqlite3.Connection = Depends(get_conn)):
        try:
            pos = training.close_position(conn, session_id, pid)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(pos))

    @app.post("/api/training/sessions/{session_id}/end")
    def api_training_end(session_id: int,
                         conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = training.end_session(conn, session_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.get("/api/training/summary")
    def api_training_summary(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.to_jsonable(training.career_summary(conn)))
```

- [ ] **Step 4: Add a route smoke test with TestClient**

Append to `tests/test_api.py`:

```python
def test_training_routes_smoke(tmp_path):
    from fastapi.testclient import TestClient
    from journal.web.app import create_app
    db = tmp_path / "journal.db"
    client = TestClient(create_app(str(db)))
    r = client.post("/api/training/sessions", json={
        "symbol": "XAUUSDc", "timeframe": "M15",
        "range_start_msc": 1000, "range_end_msc": 9000, "cursor_start_msc": 1000,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    assert client.get(f"/api/training/sessions/{sid}").json()["positions"] == []
    assert client.get("/api/training/summary").json()["n"] == 0
    assert client.delete(f"/api/training/sessions/{sid}").json()["ok"] is True
    assert client.get(f"/api/training/sessions/{sid}").status_code == 404
```

- [ ] **Step 5: Run api tests**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/api.py src/journal/web/app.py tests/test_api.py
git commit -m "feat(chart-d): /api/training/* routes"
```

---

## Task 7: Frontend pure helpers `lib/replay.ts`

**Files:**
- Create: `frontend/src/lib/replay.ts`, `frontend/src/lib/replay.test.ts`
- Modify: `frontend/src/lib/types.ts` (add `PriceLineSpec`)

**Interfaces:**
- Consumes: `Candle` (`./types`), `Sym`, `Timeframe`, `LINE_COLORS` (`./candles`).
- Produces types `TrainingSession`, `TrainingPosition`, `StepEvent`, `TrainingSummary`, `PriceLineSpec`, and pure fns `clipToCursor`, `replayLines`, `unrealizedR`, `msPerStep`.

- [ ] **Step 1: Add `PriceLineSpec` to `types.ts`**

In `frontend/src/lib/types.ts`, add (if not already present):

```typescript
export interface PriceLineSpec {
  price: number;
  color: string;
  title: string;
}
```

- [ ] **Step 2: Write failing tests**

Create `frontend/src/lib/replay.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { clipToCursor, replayLines, unrealizedR, msPerStep, type TrainingPosition } from "./replay";
import type { Candle } from "./types";

const bar = (t: number): Candle => ({ time_msc: t, o: 1, h: 2, l: 0.5, c: 1.5, v: 1 });

function pos(over: Partial<TrainingPosition> = {}): TrainingPosition {
  return {
    id: 1, session_id: 1, direction: "buy", volume: 0.1, decision_msc: 1000,
    entry_msc: 2000, entry_price: 4000, sl: 3998, tp: 4004, close_requested_msc: null,
    exit_msc: null, exit_price: null, exit_reason: null, status: "open",
    net_profit: null, r_multiple: null, mae: null, mfe: null, mae_r: null, mfe_r: null,
    created_at_msc: 0, ...over,
  };
}

describe("clipToCursor", () => {
  it("keeps only bars at or before the cursor", () => {
    const bars = [bar(1000), bar(2000), bar(3000)];
    expect(clipToCursor(bars, 2000).map((b) => b.time_msc)).toEqual([1000, 2000]);
  });
});

describe("replayLines", () => {
  it("draws entry/sl/tp for open positions, skipping 0 (none set)", () => {
    const lines = replayLines([pos({ sl: 0 })]);
    const titles = lines.map((l) => l.title);
    expect(titles.some((t) => t.startsWith("entry"))).toBe(true);
    expect(titles.some((t) => t.startsWith("TP"))).toBe(true);
    expect(titles.some((t) => t.startsWith("SL"))).toBe(false); // sl=0 skipped
  });
  it("ignores closed positions", () => {
    expect(replayLines([pos({ status: "closed" })])).toEqual([]);
  });
});

describe("unrealizedR", () => {
  it("is null without an SL", () => {
    expect(unrealizedR(pos({ sl: 0 }), 4002)).toBeNull();
  });
  it("computes (price-entry)/risk for a long", () => {
    expect(unrealizedR(pos({ entry_price: 4000, sl: 3998 }), 4002)).toBeCloseTo(1.0);
  });
});

describe("msPerStep", () => {
  it("maps speed to a delay, faster = smaller", () => {
    expect(msPerStep(1)).toBeGreaterThan(msPerStep(10));
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `npx --prefix frontend vitest run src/lib/replay.test.ts`
Expected: FAIL (module missing).

- [ ] **Step 4: Implement `lib/replay.ts`**

Create `frontend/src/lib/replay.ts`:

```typescript
// Pure display helpers + types for Chart Phase D replay. Time stays epoch-ms;
// money is USC (format with money()). No SL/TP detection here — the backend is
// authoritative (domain/replay_eval). Rule 4: 0 = none set, null = unknown.
import { LINE_COLORS, type Sym, type Timeframe } from "./candles";
import type { Candle, PriceLineSpec } from "./types";

export interface TrainingSession {
  id: number;
  symbol: Sym;
  symbol_base: string;
  timeframe: Timeframe;
  range_start_msc: number;
  range_end_msc: number;
  cursor_msc: number;
  status: "active" | "ended";
  created_at_msc: number;
}

export interface TrainingPosition {
  id: number;
  session_id: number;
  direction: "buy" | "sell";
  volume: number;
  decision_msc: number;
  entry_msc: number | null;
  entry_price: number | null;
  sl: number;                 // 0 = none set
  tp: number;                 // 0 = none set
  close_requested_msc: number | null;
  exit_msc: number | null;
  exit_price: number | null;
  exit_reason: "tp" | "sl" | "manual" | "eod" | null;
  status: "pending" | "open" | "closed";
  net_profit: number | null;  // USC
  r_multiple: number | null;
  mae: number | null;
  mfe: number | null;
  mae_r: number | null;
  mfe_r: number | null;
  created_at_msc: number;
}

export interface StepEvent {
  position_id: number;
  kind: "fill" | "exit";
  price: number;
  time_msc: number;
  reason: "tp" | "sl" | "manual" | null;
}

export interface TrainingSummary {
  n: number;
  win_rate: number | null;
  avg_r: number | null;
  total_r: number;
  avg_mae_r: number | null;
  avg_mfe_r: number | null;
}

// Only bars at or before the reveal cursor are drawn — the future is hidden.
export function clipToCursor(candles: Candle[], cursorMsc: number): Candle[] {
  return candles.filter((c) => c.time_msc <= cursorMsc);
}

// Overlay price-lines for the OPEN/PENDING fake positions only. Mirrors
// lib/candles.ts::liveLines: skips 0 (none set) and null (unknown).
export function replayLines(positions: TrainingPosition[]): PriceLineSpec[] {
  const out: PriceLineSpec[] = [];
  const add = (v: number | null, color: string, title: string) => {
    if (v !== null && v !== undefined && Math.abs(v) > 1e-9) out.push({ price: v, color, title });
  };
  for (const p of positions) {
    if (p.status === "closed") continue;
    add(p.entry_price, LINE_COLORS.entry, `entry #${p.id}`);
    add(p.sl, LINE_COLORS.sl, `SL #${p.id}`);
    add(p.tp, LINE_COLORS.tp, `TP #${p.id}`);
  }
  return out;
}

// Live, unrealized R for an OPEN position marked to the current bar's close.
// Null without an SL (rule 4) — R needs a risk distance.
export function unrealizedR(p: TrainingPosition, currentClose: number): number | null {
  if (p.entry_price === null || !p.sl || Math.abs(p.sl) < 1e-9) return null;
  const risk = Math.abs(p.entry_price - p.sl);
  if (risk < 1e-9) return null;
  const move = p.direction === "buy" ? currentClose - p.entry_price : p.entry_price - currentClose;
  return move / risk;
}

// Playback speed (1..10) → delay between auto-steps in ms. Faster = smaller.
export function msPerStep(speed: number): number {
  const s = Math.min(10, Math.max(1, speed));
  return Math.round(1000 / s);
}
```

- [ ] **Step 5: Run to verify all pass**

Run: `npx --prefix frontend vitest run src/lib/replay.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/replay.ts frontend/src/lib/replay.test.ts frontend/src/lib/types.ts
git commit -m "feat(chart-d): pure replay helpers + types (lib/replay.ts)"
```

---

## Task 8: Frontend API client `lib/replayApi.ts`

**Files:**
- Create: `frontend/src/lib/replayApi.ts`

**Interfaces:**
- Consumes: `postJson` (`./api`), types from `./replay`.
- Produces: `createSession`, `getSession`, `listSessions`, `deleteSession`, `step`, `openPosition`, `closePosition`, `endSession`, `getSummary` — each returning a typed `{ok, data?, error?}` (reusing `postJson`) or a fetched value.

- [ ] **Step 1: Implement the client**

Create `frontend/src/lib/replayApi.ts`:

```typescript
// Typed fetch wrappers for /api/training/*. Impure; the pure display helpers are
// in lib/replay.ts. The backend is authoritative for all fills/scores.
import { postJson } from "./api";
import type { Sym, Timeframe } from "./candles";
import type { StepEvent, TrainingPosition, TrainingSession, TrainingSummary } from "./replay";

export interface SessionView { session: TrainingSession; positions: TrainingPosition[] }
export interface CreateResult { session: TrainingSession; pending: boolean }
export interface StepResult { cursor_msc: number; events: StepEvent[]; positions: TrainingPosition[] }

export function createSession(body: {
  symbol: Sym; timeframe: Timeframe;
  range_start_msc: number; range_end_msc: number; cursor_start_msc?: number;
}) {
  return postJson<CreateResult>("/api/training/sessions", body);
}

export async function getSession(id: number): Promise<SessionView | null> {
  const r = await fetch(`/api/training/sessions/${id}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error((await r.json()).error ?? `HTTP ${r.status}`);
  return (await r.json()) as SessionView;
}

export async function listSessions(status?: "active" | "ended"): Promise<TrainingSession[]> {
  const q = status ? `?status=${status}` : "";
  const r = await fetch(`/api/training/sessions${q}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as TrainingSession[];
}

export function deleteSession(id: number) {
  return postJson<{ ok: boolean }>(`/api/training/sessions/${id}`, {}).then(() =>
    fetch(`/api/training/sessions/${id}`, { method: "DELETE" }).then((r) => r.ok),
  );
}

export function step(id: number, n = 1) {
  return postJson<StepResult>(`/api/training/sessions/${id}/step`, { n });
}

export function openPosition(id: number, body: {
  direction: "buy" | "sell"; volume: number; sl: number; tp: number;
}) {
  return postJson<TrainingPosition>(`/api/training/sessions/${id}/positions`, body);
}

export function closePosition(id: number, pid: number) {
  return postJson<TrainingPosition>(`/api/training/sessions/${id}/positions/${pid}/close`, {});
}

export function endSession(id: number) {
  return postJson<SessionView>(`/api/training/sessions/${id}/end`, {});
}

export async function getSummary(): Promise<TrainingSummary> {
  const r = await fetch("/api/training/summary");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as TrainingSummary;
}
```

> Note: `deleteSession` must issue a real HTTP DELETE. Simplify to a single call:
> ```typescript
> export async function deleteSession(id: number): Promise<boolean> {
>   const r = await fetch(`/api/training/sessions/${id}`, { method: "DELETE" });
>   return r.ok;
> }
> ```
> Use this simpler form; drop the `postJson` line above.

- [ ] **Step 2: Typecheck via build**

Run: `npm --prefix frontend run build`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/replayApi.ts
git commit -m "feat(chart-d): typed /api/training client (lib/replayApi.ts)"
```

---

## Task 9: `CandleChart` gains an `overlayLines` prop

Keep CandleChart generic: when `overlayLines` is provided, draw those instead of the live-position overlay. Replay passes its fake-position lines; normal Chart passes none.

**Files:**
- Modify: `frontend/src/components/CandleChart.tsx`

**Interfaces:**
- Consumes: `PriceLineSpec` (`../lib/types`).
- Produces: optional prop `overlayLines?: PriceLineSpec[]`. When defined (even `[]`), it REPLACES the `props.live` overlay branch.

- [ ] **Step 1: Add the prop to the component type**

In `frontend/src/components/CandleChart.tsx`, add to the props object type (after `nowVisible: boolean;`):

```typescript
  overlayLines?: import("../lib/types").PriceLineSpec[];
```

- [ ] **Step 2: Draw `overlayLines` when provided**

Replace the body of the overlay effect (the `useEffect` at ~line 207 that starts with `const s = series.current;`) so that when `props.overlayLines` is defined it wins:

```typescript
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = [];

    // Replay (or any caller) supplies explicit lines → draw exactly those.
    const explicit = props.overlayLines;
    let specs: { price: number; color: string; title: string }[] = [];
    if (explicit !== undefined) {
      specs = explicit;
    } else {
      // Live SL/TP/entry overlay — only when the current symbol has open positions
      // AND "now" is in view (horizontal lines have no time).
      if (!props.settings.liveOverlay || !props.nowVisible || !props.live || props.live.live.empty) return;
      const mine = props.live.live.positions.filter((p) => p.symbol === props.symbol);
      for (const pos of mine) specs.push(...liveLines(pos));
    }

    for (const line of specs) {
      priceLines.current.push(
        s.createPriceLine({
          price: line.price,
          color: line.color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: line.title,
        }),
      );
    }
  }, [props.live, props.nowVisible, props.symbol, props.settings.liveOverlay, props.settings.chartType, props.overlayLines]);
```

- [ ] **Step 3: Verify existing Chart still builds and behaves**

Run: `npm --prefix frontend run build && npx --prefix frontend vitest run`
Expected: build 0 errors; existing tests pass (no CandleChart behavioural test should regress — the default branch is unchanged when `overlayLines` is absent).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/CandleChart.tsx
git commit -m "feat(chart-d): CandleChart optional overlayLines prop (generic overlay)"
```

---

## Task 10: `useReplaySession` hook + playback loop

**Files:**
- Create: `frontend/src/hooks/useReplaySession.ts`

**Interfaces:**
- Consumes: `replayApi` (Task 8), `replay` helpers (Task 7).
- Produces a hook: `useReplaySession()` returning
  `{ session, positions, events, status, error, start, step, play, pause, playing, jump, reset, open, close, end, discard, cursorMsc }`.
  - `start(cfg)` — create a session; `cfg = {symbol, timeframe, range_start_msc, range_end_msc, cursor_start_msc, speed}`.
  - `step(n=1)` — one backend step, merge returned positions.
  - `play()/pause()` — auto-step loop at `msPerStep(speed)`, stops at `range_end` or on any exit event (so the user reviews the fill).
  - `jump(n)` — step `n` bars in one call.
  - `reset()` — recreate a fresh session over the same range (prior closed trades remain in history).
  - `open(order)`, `close(pid)`, `end()` — thin API calls, refresh positions.
  - `discard()` — delete the current session (throwaway attempt) and clear state.

- [ ] **Step 1: Implement the hook**

Create `frontend/src/hooks/useReplaySession.ts`:

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import type { Sym, Timeframe } from "../lib/candles";
import { msPerStep, type StepEvent, type TrainingPosition, type TrainingSession } from "../lib/replay";
import * as replayApi from "../lib/replayApi";

export interface ReplayConfig {
  symbol: Sym;
  timeframe: Timeframe;
  range_start_msc: number;
  range_end_msc: number;
  cursor_start_msc: number;
  speed: number;                  // 1..10 bars/sec
}

export type ReplayStatus = "idle" | "starting" | "ready" | "ended" | "error";

export function useReplaySession() {
  const [session, setSession] = useState<TrainingSession | null>(null);
  const [positions, setPositions] = useState<TrainingPosition[]>([]);
  const [events, setEvents] = useState<StepEvent[]>([]);
  const [status, setStatus] = useState<ReplayStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);

  const cfgRef = useRef<ReplayConfig | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busy = useRef(false);      // one in-flight step at a time
  const clear = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };

  const _create = useCallback(async (cfg: ReplayConfig) => {
    setStatus("starting"); setError(null); setEvents([]); setPositions([]);
    const r = await replayApi.createSession(cfg);
    if (!r.ok || !r.data) { setError(r.error ?? "gagal membuat sesi"); setStatus("error"); return; }
    setSession(r.data.session);
    setStatus("ready");
  }, []);

  const start = useCallback((cfg: ReplayConfig) => { cfgRef.current = cfg; return _create(cfg); }, [_create]);

  const _sid = () => session?.id ?? null;

  const step = useCallback(async (n = 1): Promise<StepEvent[]> => {
    const id = _sid();
    if (id === null || busy.current) return [];
    busy.current = true;
    try {
      const r = await replayApi.step(id, n);
      if (!r.ok || !r.data) { setError(r.error ?? "gagal step"); return []; }
      setPositions(r.data.positions);
      setEvents(r.data.events);
      setSession((s) => (s ? { ...s, cursor_msc: r.data!.cursor_msc } : s));
      return r.data.events;
    } finally {
      busy.current = false;
    }
  }, [session]);

  // Auto-step loop: stop at range end, or when an exit happened (review the fill).
  useEffect(() => {
    if (!playing || !session) return;
    if (session.cursor_msc >= session.range_end_msc) { setPlaying(false); return; }
    const delay = msPerStep(cfgRef.current?.speed ?? 4);
    clear();
    timer.current = setTimeout(async () => {
      const evs = await step(1);
      if (evs.some((e) => e.kind === "exit")) setPlaying(false);
    }, delay);
    return clear;
  }, [playing, session, step]);

  const play = useCallback(() => setPlaying(true), []);
  const pause = useCallback(() => { setPlaying(false); clear(); }, []);
  const jump = useCallback((n: number) => step(n), [step]);

  const reset = useCallback(() => {
    pause();
    if (cfgRef.current) return _create(cfgRef.current);
  }, [pause, _create]);

  const refresh = useCallback(async () => {
    const id = _sid();
    if (id === null) return;
    const v = await replayApi.getSession(id);
    if (v) { setSession(v.session); setPositions(v.positions); }
  }, [session]);

  const open = useCallback(async (order: { direction: "buy" | "sell"; volume: number; sl: number; tp: number }) => {
    const id = _sid();
    if (id === null) return;
    const r = await replayApi.openPosition(id, order);
    if (!r.ok) { setError(r.error ?? "gagal buka posisi"); return; }
    await refresh();
  }, [session, refresh]);

  const close = useCallback(async (pid: number) => {
    const id = _sid();
    if (id === null) return;
    await replayApi.closePosition(id, pid);
    await refresh();
  }, [session, refresh]);

  const end = useCallback(async () => {
    const id = _sid();
    if (id === null) return;
    pause();
    const r = await replayApi.endSession(id);
    if (r.ok && r.data) { setSession(r.data.session); setPositions(r.data.positions); }
    setStatus("ended");
  }, [session, pause]);

  const discard = useCallback(async () => {
    const id = _sid();
    pause();
    if (id !== null) await replayApi.deleteSession(id);
    setSession(null); setPositions([]); setEvents([]); setStatus("idle");
  }, [session, pause]);

  useEffect(() => clear, []);

  return {
    session, positions, events, status, error, playing,
    cursorMsc: session?.cursor_msc ?? null,
    start, step, play, pause, jump, reset, open, close, end, discard,
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `npm --prefix frontend run build`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useReplaySession.ts
git commit -m "feat(chart-d): useReplaySession hook + playback loop"
```

---

## Task 11: Replay UI components

Five presentational components. All money via `money()`/`price()` from `lib/format`; §8 aggregates greyed when null.

**Files:**
- Create: `frontend/src/components/ReplayConfigModal.tsx`, `ReplayControls.tsx`, `ReplayOrderTicket.tsx`, `ReplayPositions.tsx`, `ReplaySummary.tsx`

**Interfaces:**
- Consumes: `SYMBOLS`, `TIMEFRAMES` (`../lib/candles`); `TrainingPosition`, `TrainingSummary`, `unrealizedR` (`../lib/replay`); `ReplayConfig` (`../hooks/useReplaySession`); `money`, `price`, `wib` (`../lib/format`).

- [ ] **Step 1: `ReplayConfigModal.tsx`**

Create `frontend/src/components/ReplayConfigModal.tsx`:

```typescript
import { useState } from "react";
import { SYMBOLS, TIMEFRAMES, timeframeMs, type Sym, type Timeframe } from "../lib/candles";
import type { ReplayConfig } from "../hooks/useReplaySession";

// Config for a new replay: symbol, timeframe, a start date (the reveal cursor),
// how many bars of history to show before it, and playback speed. range_start is
// cursor - historyBars*tf; range_end is "now" (reveal target).
export default function ReplayConfigModal(props: {
  onStart: (cfg: ReplayConfig) => void;
  onCancel: () => void;
}) {
  const [symbol, setSymbol] = useState<Sym>("XAUUSDc");
  const [tf, setTf] = useState<Timeframe>("M15");
  const [startDate, setStartDate] = useState<string>(""); // yyyy-mm-dd
  const [historyBars, setHistoryBars] = useState(300);
  const [speed, setSpeed] = useState(4);

  const submit = () => {
    const cursor = startDate ? new Date(startDate + "T00:00:00Z").getTime() : Date.now() - timeframeMs(tf) * 100;
    const range_start_msc = cursor - timeframeMs(tf) * historyBars;
    props.onStart({
      symbol, timeframe: tf,
      range_start_msc, range_end_msc: Date.now(),
      cursor_start_msc: cursor, speed,
    });
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50">
      <div className="glass w-[360px] p-4 space-y-3">
        <h2 className="text-sm font-semibold">Mulai Replay</h2>
        <label className="block text-xs">Simbol
          <select className="glass mt-1 w-full px-2 py-1" value={symbol}
                  onChange={(e) => setSymbol(e.target.value as Sym)}>
            {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="block text-xs">Timeframe
          <select className="glass mt-1 w-full px-2 py-1" value={tf}
                  onChange={(e) => setTf(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="block text-xs">Mulai dari tanggal (UTC)
          <input type="date" className="glass mt-1 w-full px-2 py-1" value={startDate}
                 onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="block text-xs">Bar histori sebelum mulai: {historyBars}
          <input type="range" min={100} max={1000} step={50} className="w-full" value={historyBars}
                 onChange={(e) => setHistoryBars(Number(e.target.value))} />
        </label>
        <label className="block text-xs">Kecepatan: {speed} bar/dtk
          <input type="range" min={1} max={10} className="w-full" value={speed}
                 onChange={(e) => setSpeed(Number(e.target.value))} />
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <button className="glass px-3 py-1 text-muted" onClick={props.onCancel}>Batal</button>
          <button className="glass px-3 py-1 text-cyan" onClick={submit}>Mulai</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: `ReplayControls.tsx`**

Create `frontend/src/components/ReplayControls.tsx`:

```typescript
import { wib } from "../lib/format";

export default function ReplayControls(props: {
  cursorMsc: number | null;
  playing: boolean;
  atEnd: boolean;
  onStep: () => void;
  onPlayPause: () => void;
  onJump: (n: number) => void;
  onReset: () => void;
  onExit: () => void;
}) {
  return (
    <div className="glass flex items-center gap-2 px-3 py-2 text-xs">
      <span className="rounded bg-cyan/20 px-2 py-0.5 text-cyan font-semibold">REPLAY</span>
      <button className="glass px-2 py-1" title="Reset ke awal" onClick={props.onReset}>|◀ Reset</button>
      <button className="glass px-2 py-1" onClick={props.onStep} disabled={props.atEnd}>▶| Step</button>
      <button className="glass px-2 py-1 text-cyan" onClick={props.onPlayPause} disabled={props.atEnd}>
        {props.playing ? "⏸ Pause" : "▶ Play"}
      </button>
      <button className="glass px-2 py-1" onClick={() => props.onJump(10)} disabled={props.atEnd}>⏩ +10</button>
      <span className="ml-auto text-muted">
        {props.cursorMsc ? wib(props.cursorMsc, 0) : "—"}{props.atEnd ? " · selesai" : ""}
      </span>
      <button className="glass px-2 py-1 text-neg" onClick={props.onExit}>Keluar</button>
    </div>
  );
}
```

- [ ] **Step 3: `ReplayOrderTicket.tsx`**

Create `frontend/src/components/ReplayOrderTicket.tsx`:

```typescript
import { useState } from "react";

// Fake market order at the NEXT bar's open (backend fills). SL/TP blank = none set
// (stored 0, rule 4). No signal/recommendation anywhere (rule 9).
export default function ReplayOrderTicket(props: {
  disabled: boolean;
  onSubmit: (o: { direction: "buy" | "sell"; volume: number; sl: number; tp: number }) => void;
}) {
  const [volume, setVolume] = useState(0.1);
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");

  const submit = (direction: "buy" | "sell") => {
    props.onSubmit({
      direction, volume,
      sl: sl.trim() === "" ? 0 : Number(sl),
      tp: tp.trim() === "" ? 0 : Number(tp),
    });
  };

  return (
    <div className="glass p-3 space-y-2 text-xs">
      <div className="font-semibold">Order</div>
      <label className="block">Volume (lot)
        <input type="number" step="0.01" min="0.01" className="glass mt-1 w-full px-2 py-1"
               value={volume} onChange={(e) => setVolume(Number(e.target.value))} />
      </label>
      <label className="block">SL (kosong = tidak ada)
        <input type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
               value={sl} onChange={(e) => setSl(e.target.value)} />
      </label>
      <label className="block">TP (kosong = tidak ada)
        <input type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
               value={tp} onChange={(e) => setTp(e.target.value)} />
      </label>
      <div className="flex gap-2 pt-1">
        <button className="glass flex-1 py-1 text-pos" disabled={props.disabled}
                onClick={() => submit("buy")}>Buy</button>
        <button className="glass flex-1 py-1 text-neg" disabled={props.disabled}
                onClick={() => submit("sell")}>Sell</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `ReplayPositions.tsx`**

Create `frontend/src/components/ReplayPositions.tsx`:

```typescript
import { money, price } from "../lib/format";
import { unrealizedR, type TrainingPosition } from "../lib/replay";

export default function ReplayPositions(props: {
  positions: TrainingPosition[];
  currentClose: number | null;
  currency: string;
  onClose: (pid: number) => void;
}) {
  const open = props.positions.filter((p) => p.status !== "closed");
  const closed = props.positions.filter((p) => p.status === "closed");

  return (
    <div className="glass p-3 space-y-2 text-xs">
      <div className="font-semibold">Posisi</div>
      {open.length === 0 && <div className="text-muted">Tidak ada posisi terbuka.</div>}
      {open.map((p) => {
        const uR = props.currentClose !== null ? unrealizedR(p, props.currentClose) : null;
        return (
          <div key={p.id} className="flex items-center justify-between gap-2 border-b border-white/5 pb-1">
            <span>
              #{p.id} {p.direction === "buy" ? "▲" : "▼"} {p.volume}
              {p.status === "pending" ? " · pending" : ` @ ${p.entry_price !== null ? price(p.entry_price) : "—"}`}
              {p.close_requested_msc ? " · closing…" : ""}
            </span>
            <span className="flex items-center gap-2">
              <span className={uR !== null && uR < 0 ? "text-neg" : "text-pos"}>
                {uR !== null ? `${uR.toFixed(2)}R` : "—"}
              </span>
              {p.status === "open" && !p.close_requested_msc && (
                <button className="glass px-2 py-0.5 text-neg" onClick={() => props.onClose(p.id)}>Close</button>
              )}
            </span>
          </div>
        );
      })}
      {closed.length > 0 && (
        <div className="pt-2">
          <div className="font-semibold text-muted">Selesai</div>
          {closed.map((p) => (
            <div key={p.id} className="flex justify-between">
              <span>#{p.id} {p.exit_reason ?? ""}</span>
              <span className={p.net_profit !== null && p.net_profit < 0 ? "text-neg" : "text-pos"}>
                {p.r_multiple !== null ? `${p.r_multiple.toFixed(2)}R` : "—"}
                {p.net_profit !== null ? ` · ${money(p.net_profit, props.currency)}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

> Verify `money(value, currency)` and `price(value)` signatures in `frontend/src/lib/format.ts` before use; adjust the call sites to match the actual exports (e.g. if `money` takes only a number, drop the currency arg).

- [ ] **Step 5: `ReplaySummary.tsx`**

Create `frontend/src/components/ReplaySummary.tsx`:

```typescript
import type { TrainingSummary } from "../lib/replay";

// §8: n and total_r always show; rate/average metrics are greyed (—) when null.
function Metric(props: { label: string; value: number | null; suffix?: string; pct?: boolean }) {
  const v = props.value;
  const text = v === null ? "—" : props.pct ? `${(v * 100).toFixed(0)}%` : `${v.toFixed(2)}${props.suffix ?? ""}`;
  return (
    <div className="flex justify-between">
      <span className="text-muted">{props.label}</span>
      <span className={v === null ? "text-muted/50" : ""}>{text}</span>
    </div>
  );
}

export default function ReplaySummary(props: { title: string; s: TrainingSummary | null }) {
  const s = props.s;
  return (
    <div className="glass p-3 space-y-1 text-xs">
      <div className="font-semibold">{props.title}</div>
      {!s ? <div className="text-muted">—</div> : (
        <>
          <Metric label="n" value={s.n} />
          <Metric label="Win rate" value={s.win_rate} pct />
          <Metric label="Avg R" value={s.avg_r} suffix="R" />
          <Metric label="Total R" value={s.total_r} suffix="R" />
          <Metric label="Avg MAE" value={s.avg_mae_r} suffix="R" />
          <Metric label="Avg MFE" value={s.avg_mfe_r} suffix="R" />
          {s.n < 20 && <div className="text-muted/60 pt-1">n &lt; 20 — rasio disembunyikan (§8)</div>}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Build**

Run: `npm --prefix frontend run build`
Expected: 0 errors. Fix any `money`/`price`/`wib` signature mismatches surfaced here.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ReplayConfigModal.tsx frontend/src/components/ReplayControls.tsx frontend/src/components/ReplayOrderTicket.tsx frontend/src/components/ReplayPositions.tsx frontend/src/components/ReplaySummary.tsx
git commit -m "feat(chart-d): replay UI components (config, controls, ticket, positions, summary)"
```

---

## Task 12: Wire replay mode into `/chart` (Replay button, snapshot/restore, badge)

**Files:**
- Modify: `frontend/src/components/ChartToolbar.tsx` (Replay button), `frontend/src/pages/Chart.tsx` (mode wiring)

**Interfaces:**
- Consumes: `useReplaySession` (Task 10), the five components (Task 11), `clipToCursor`, `replayLines` (Task 7), `useApi` for `/api/training/summary`.

- [ ] **Step 1: Add a Replay button to `ChartToolbar.tsx`**

In `frontend/src/components/ChartToolbar.tsx`, add an `onReplay: () => void` prop and render a button next to the existing controls:

```typescript
        <button className="glass px-3 py-1 text-cyan" onClick={props.onReplay} title="Mode replay/training">
          ▶ Replay
        </button>
```

(Add `onReplay: () => void;` to the toolbar's props type. Read the file first to match its exact prop-type and layout conventions.)

- [ ] **Step 2: Wire replay mode in `Chart.tsx`**

Modify `frontend/src/pages/Chart.tsx`:
- Import: `useReplaySession`, the five components, `clipToCursor`, `replayLines`, and `useApi`.
- Add state: `const [replayOpen, setReplayOpen] = useState(false);` and `const [configOpen, setConfigOpen] = useState(false);`
- Instantiate the hook: `const replay = useReplaySession();`
- Snapshot/restore: capture the current URL selection before entering; on exit, `replay.discard()` and restore. Because symbol/tf live in the URL and prefs are read-only here, snapshot is just remembering the URL params:

```typescript
  const snapshotRef = useRef<string>("");

  const enterReplay = () => { snapshotRef.current = params.toString(); setConfigOpen(true); };
  const exitReplay = async () => {
    await replay.discard();
    setReplayOpen(false); setConfigOpen(false);
    setParams(new URLSearchParams(snapshotRef.current), { replace: true }); // restore prior view
  };
  const onStart = (cfg: import("../hooks/useReplaySession").ReplayConfig) => {
    setConfigOpen(false); setReplayOpen(true);
    // Point the chart at the replay symbol/tf so CandleChart fetches the right series.
    setParams(new URLSearchParams({ symbol: cfg.symbol, tf: cfg.timeframe }), { replace: true });
    replay.start(cfg);
  };
```

- Pass `onReplay={enterReplay}` to `<ChartToolbar>`.
- When `replayOpen`, feed the chart clipped candles and replay overlay lines, and render the controls/ticket/positions/summary. The candle data still comes from the existing `useChartData(symbol, tf, ...)` (which fetches the cached range); replay clips it to the cursor:

```typescript
  const cursor = replay.cursorMsc;
  const shownCandles = replayOpen && cursor !== null
    ? clipToCursor(data.candles, cursor)
    : data.candles;
  const overlay = replayOpen ? replayLines(replay.positions) : undefined;
  const currentClose = shownCandles.length ? shownCandles[shownCandles.length - 1].c : null;
  const atEnd = !!replay.session && cursor !== null && cursor >= replay.session.range_end_msc;
```

- Pass to `<CandleChart>`: `candles={shownCandles}` and `overlayLines={overlay}` (add these; keep the rest).
- Render, when `configOpen`, `<ReplayConfigModal onStart={onStart} onCancel={exitReplay} />`.
- Render, when `replayOpen`, a bottom control bar `<ReplayControls .../>` and a right-hand replay panel (config-modal replaces the normal info aside, or stack below it) containing `<ReplayOrderTicket>`, `<ReplayPositions>`, per-session `<ReplaySummary title="Sesi ini" .../>` and career `<ReplaySummary title="Kumulatif" .../>`. Fetch the career summary with `const { data: career } = useApi<TrainingSummary>("/api/training/summary", replayOpen ? 3000 : undefined);` and the session summary can be derived client-side from `replay.positions` OR fetched via `getSession` — for simplicity, show career from the API and a compact per-session tally computed inline (count of closed + sum of r_multiple).

Wire the handlers: `onStep={() => replay.step(1)}`, `onPlayPause={() => replay.playing ? replay.pause() : replay.play()}`, `onJump={replay.jump}`, `onReset={replay.reset}`, `onExit={exitReplay}`, ticket `onSubmit={replay.open}`, positions `onClose={replay.close}`.

Add a visible `REPLAY` badge (already inside `ReplayControls`).

> This task is integration-heavy: read `Chart.tsx` and `ChartToolbar.tsx` fully first and follow their existing layout (the `flex` column, the `aside` panel). Keep replay state entirely within `useReplaySession`/local component state — do NOT call `update`/`reset` from `useChartPrefs` anywhere in the replay path (Phase C isolation).

- [ ] **Step 3: Build + full frontend tests**

Run: `npm --prefix frontend run build && npx --prefix frontend vitest run`
Expected: build 0 errors; all vitest pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Chart.tsx frontend/src/components/ChartToolbar.tsx
git commit -m "feat(chart-d): replay mode wired into /chart (button, clip, overlay, panels, restore)"
```

---

## Task 13: Full verification + manual smoke + graphify

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `uv run pytest`
Expected: all pass (existing + new). Paste the summary line.

- [ ] **Step 2: Frontend suite + build**

Run: `npm --prefix frontend test && npm --prefix frontend run build`
Expected: vitest all pass; build 0 errors.

- [ ] **Step 3: Rebuild-safety end to end**

Run: `uv run journal rebuild`
Expected: succeeds (training tables untouched — already asserted by test in Task 1, this confirms the CLI path).

- [ ] **Step 4: Manual smoke (optional but recommended)**

Build the SPA, then serve against a scratch DB (the gotcha: `journal serve` ignores `JOURNAL_DB`, so pass `--db`):

```bash
npm --prefix frontend run build
uv run journal serve --db "$CLAUDE_JOB_DIR/tmp/smoke.db"
```

In the browser: open `/chart`, click **▶ Replay**, pick XAUUSDc/M15 and a start date, start, step a few bars, open a Buy with an SL/TP, step until it fills and resolves, confirm the position panel and summaries update, then **Keluar** and confirm the chart returns to its pre-replay state. (If no candles are cached for the range, the fill queue needs `journal live` running to drain — expected; the replay shows whatever is cached.)

- [ ] **Step 5: Update the graph**

Run: `graphify update .`

- [ ] **Step 6: Final commit if anything changed**

```bash
git add -A
git commit -m "chore(chart-d): graphify update + verification" || echo "nothing to commit"
```

---

## Self-Review Notes (author)

- **Spec coverage:** evaluation semantics §3 → Task 2 (+ service Task 5 for money/R/MAE-MFE); schema §4 → Task 1; API §5 → Tasks 5–6; frontend §6 → Tasks 7–12; scoring/§8 → Task 4 (`_summary`) + Task 11 (`ReplaySummary`); rule-9 boundary → no signal surfaces anywhere (order ticket is manual only); testing §7 → Tasks 1–13.
- **Isolation:** replay never writes `app_prefs`; Task 12 note enforces it. Snapshot/restore = remembering URL params.
- **Type consistency:** `PositionState.close_requested_msc`, `training_positions.close_requested_msc`, and `TrainingPosition.close_requested_msc` all align; `StepEvent`/`FillEvent` fields match across Python and TS; `TrainingSummary` fields match `_summary`'s dict.
