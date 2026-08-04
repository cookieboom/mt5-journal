# Risk-Based Auto Lot Sizing + Live Position Open — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the human drag a stop-loss line on the chart, state a risk budget, and have the system derive the lot size and open a position at market — in replay and, for the first time in this project, in live.

**Architecture:** A pure sizing pair in `domain/risk.py` (the inverse of the existing `risk_amount`), a new `open` command kind that reuses every existing validation path via a synthetic position mapping, one queue row through `trade_commands` executed by `journal live`, and one `RiskSizePanel` in the chart's existing right `<aside>` that never does the arithmetic itself.

**Tech Stack:** python 3.12, sqlite3 (stdlib), FastAPI, pytest · React 18 + TypeScript, lightweight-charts 5.2.0, vitest + testing-library.

**Spec:** `docs/superpowers/specs/2026-08-04-risk-based-auto-lot-sizing-design.md`

## Global Constraints

- **No new dependencies.** Stack is fixed: python 3.12, sqlite3, pandas, mplfinance, typer, pytest; React, lightweight-charts, vitest. (CLAUDE.md rule 8)
- **Never `import MetaTrader5` outside `src/journal/adapter/`.** `web/` and `domain/` never touch the bridge. Only `ingest/live.py` may call `client.*`. (rules 1 and 12)
- **All money is USC (US cents).** Never render a bare number as `$`. `accounts.currency` is the unit of every money figure, never `symbol_specs.currency_profit`. (Trap 14)
- **`NULL` means unknown; `0.0` means "none set".** Never coerce one into the other, in Python, in SQL, or in TypeScript. (rule 4)
- **Money and volume are `REAL`; compare with tolerance `1e-9`, never `==`.** (rule 5)
- **Tests before implementation** for everything in `domain/`. (rule 7)
- **All timestamps are epoch milliseconds, integer, UTC.** (rule 3)
- **No trade signals or recommendations.** This computes a size from numbers the human supplies; it never suggests a direction, a level, or a moment. (rule 9)
- **Schema changes need a migration file.** `schema.sql` is edited to match, but the migration is what runs on an existing DB. (CLAUDE.md "Read before you edit")
- **User-facing error text is Indonesian**, matching the existing messages in `domain/commands.py`.
- Run commands with `uv run` (`uv run pytest`, `uv run journal rebuild`). Frontend commands run from `frontend/` (`npm test`, `npx tsc --noEmit`, `npm run build`).

## File Structure

**Created:**
- `src/journal/store/migrations/009_open_command.sql` — rebuilds `trade_commands` for the `open` kind.
- `frontend/src/components/RiskSizePanel.tsx` — the panel. Inputs, read-outs, one action button.
- `frontend/src/components/RiskSizePanel.test.tsx`
- `frontend/src/hooks/useRiskSizing.ts` — debounced `/api/size` caller + `app_prefs` persistence.
- `frontend/src/hooks/useRiskSizing.test.ts`

**Modified:**
- `src/journal/domain/risk.py` — `volume_for_risk`, `floor_to_step`, `direction_for_sl`.
- `src/journal/domain/commands.py` — `open` kind, `MAX_RISK_PCT`, `balance` parameter.
- `src/journal/store/schema.sql` — `trade_commands` DDL kept in step with the migration.
- `src/journal/store/db.py` — `SCHEMA_VERSION` 8 → 9.
- `src/journal/store/prefs_store.py` — `RISK_KEY` accessors.
- `src/journal/execute.py` — `load_open_context`, `enqueue_open`.
- `src/journal/ingest/live.py` — the executor's `open` branch.
- `src/journal/web/views.py` — `size_order`, `preview_open`, `_intent_text` for `open`.
- `src/journal/web/app.py` — `/api/size`, `/api/live/open/preview`, `/api/live/open`, `/api/risk-prefs`.
- `frontend/src/lib/types.ts` — `SizeResult`, `RiskPrefs`, `PlannedOrder`.
- `frontend/src/lib/sltpDrag.ts` — `PLANNED_ID` sentinel.
- `frontend/src/components/CandleChart.tsx` — planned-order lines, drag routing.
- `frontend/src/hooks/useLiveCommand.ts` — allow `position_id: null` for `open`.
- `frontend/src/pages/Chart.tsx` — mount the panel in both modes, wire the drag.

**Tests:** `tests/test_risk.py`, `tests/test_commands.py`, `tests/test_execute.py`, `tests/test_live.py`, `tests/test_web.py`, `tests/test_migrations.py` (if present; otherwise the migration test goes in `tests/test_db.py`).

---

### Task 1: Pure sizing arithmetic

**Files:**
- Modify: `src/journal/domain/risk.py`
- Test: `tests/test_risk.py`

**Interfaces:**
- Consumes: the existing `risk_amount(open_price, sl_initial, tick_size, tick_value, volume) -> float | None`.
- Produces:
  - `volume_for_risk(entry_price: float | None, sl: float | None, tick_size: float | None, tick_value: float | None, risk: float | None) -> float | None`
  - `floor_to_step(volume: float | None, step: float | None) -> float | None`
  - `direction_for_sl(entry_price: float | None, sl: float | None) -> str | None` — `"buy"`, `"sell"`, or `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk.py`:

```python
from journal.domain.risk import (
    direction_for_sl,
    floor_to_step,
    risk_amount,
    volume_for_risk,
)


def test_volume_for_risk_is_the_inverse_of_the_reference_figure():
    # §8 read backwards: to risk exactly 50 USC with entry 4035 / SL 4030 on
    # XAUUSDc, the size must be the 0.10 lot the reference figure used.
    v = volume_for_risk(4035.000, 4030.000, _TICK_SIZE, _TICK_VALUE, 50.0)
    assert v is not None
    assert abs(v - 0.10) < 1e-9
    # And it round-trips: sizing then measuring gives the budget back.
    assert abs(risk_amount(4035.000, 4030.000, _TICK_SIZE, _TICK_VALUE, v) - 50.0) < 1e-9


def test_volume_for_risk_direction_does_not_matter():
    v = volume_for_risk(4030.000, 4035.000, _TICK_SIZE, _TICK_VALUE, 50.0)
    assert v is not None and abs(v - 0.10) < 1e-9


def test_volume_for_risk_propagates_every_unknown():
    assert volume_for_risk(None, 4030.0, _TICK_SIZE, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, None, _TICK_SIZE, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, None, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, None, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, None) is None


def test_volume_for_risk_refuses_a_zero_distance():
    # entry == sl is an infinite size, not a large one. Never a ZeroDivision,
    # never inf — None, the same as every other unknown (Trap 6).
    assert volume_for_risk(4035.0, 4035.0, _TICK_SIZE, _TICK_VALUE, 50.0) is None


def test_volume_for_risk_refuses_malformed_specs():
    assert volume_for_risk(4035.0, 4030.0, 0.0, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, 0.0, 50.0) is None


def test_volume_for_risk_refuses_a_non_positive_budget():
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, 0.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, -5.0) is None


def test_floor_to_step_rounds_down_never_up():
    # 0.137 lot at a 0.01 step is 0.13, not 0.14 — rounding up would take more
    # risk than the human budgeted.
    assert abs(floor_to_step(0.137, 0.01) - 0.13) < 1e-9
    assert abs(floor_to_step(0.999, 0.01) - 0.99) < 1e-9


def test_floor_to_step_is_exact_on_exact_multiples():
    # The IEEE754 trap `commands._is_multiple` documents, in floor form:
    # 0.03 / 0.01 is 2.9999999999999996, so a raw floor() drops a whole step.
    assert abs(floor_to_step(0.03, 0.01) - 0.03) < 1e-9
    assert abs(floor_to_step(0.07, 0.01) - 0.07) < 1e-9
    assert abs(floor_to_step(1.0, 0.01) - 1.0) < 1e-9


def test_floor_to_step_below_one_step_is_zero_not_none():
    # 0.004 at a 0.01 step is a real answer: zero lots. The CALLER decides that
    # zero is unusable (it is below volume_min); this function does not guess.
    assert abs(floor_to_step(0.004, 0.01) - 0.0) < 1e-9


def test_floor_to_step_propagates_unknowns():
    assert floor_to_step(None, 0.01) is None
    assert floor_to_step(0.13, None) is None
    assert floor_to_step(0.13, 0.0) is None


def test_direction_for_sl_reads_the_side():
    # The whole gesture: an SL below the price means the human is buying.
    assert direction_for_sl(4035.0, 4030.0) == "buy"
    assert direction_for_sl(4035.0, 4040.0) == "sell"


def test_direction_for_sl_has_no_answer_at_the_price():
    assert direction_for_sl(4035.0, 4035.0) is None
    assert direction_for_sl(None, 4030.0) is None
    assert direction_for_sl(4035.0, None) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_risk.py -v`
Expected: FAIL, `ImportError: cannot import name 'volume_for_risk' from 'journal.domain.risk'`

- [ ] **Step 3: Implement**

Append to `src/journal/domain/risk.py`:

```python
def volume_for_risk(
    entry_price: float | None,
    sl: float | None,
    tick_size: float | None,
    tick_value: float | None,
    risk: float | None,
) -> float | None:
    """Lots that put exactly `risk` (account currency) at stake between
    `entry_price` and `sl` — the inverse of `risk_amount`.

    `risk / ((|entry - sl| / tick_size) * tick_value)`. Returns `None` for every
    unknown, for a malformed spec, for a non-positive budget, and for a zero
    distance (an infinite size is not a large one). Never raises, never returns
    `inf`: a coerced number here becomes a real order.
    """
    if (
        entry_price is None
        or sl is None
        or tick_size is None
        or tick_value is None
        or risk is None
    ):
        return None
    if tick_size <= 0 or tick_value <= 0 or risk <= 0:
        return None
    distance = abs(entry_price - sl)
    if distance < 1e-9:  # rule 5: tolerance, never `== 0`
        return None
    risk_per_lot = (distance / tick_size) * tick_value
    if risk_per_lot <= 0:
        return None
    return risk / risk_per_lot


def floor_to_step(volume: float | None, step: float | None) -> float | None:
    """Largest whole number of `step`s not exceeding `volume`.

    Rounds DOWN so the realised risk is never larger than the budget. `None` for
    unknowns and for a non-positive step.

    NOT `math.floor(volume / step) * step`: in IEEE754 `0.03 / 0.01` is
    2.9999999999999996, so a raw floor turns a perfectly ordinary 0.03 lot into
    0.02. The same trap `commands._is_multiple` documents. Snap to the nearest
    step first when the difference is within tolerance, and only then floor.
    """
    if volume is None or step is None:
        return None
    if step <= 0:
        return None
    n = volume / step
    nearest = round(n)
    if abs(n - nearest) < max(1e-9, abs(n) * 1e-9):
        n = nearest
    else:
        n = float(int(n))       # truncate toward zero; volume is never negative
    return max(0.0, n * step)


def direction_for_sl(entry_price: float | None, sl: float | None) -> str | None:
    """Which side the human is taking, read from where they put the stop.

    An SL BELOW the price is a buy's stop; ABOVE it is a sell's. At the price
    (or with anything unknown) there is no answer — `None`, never a default.
    This describes the human's own gesture; it does not suggest one (rule 9).
    """
    if entry_price is None or sl is None:
        return None
    if abs(entry_price - sl) < 1e-9:
        return None
    return "buy" if sl < entry_price else "sell"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_risk.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/risk.py tests/test_risk.py
git commit -m "feat(risk): volume_for_risk, floor_to_step, direction_for_sl"
```

---

### Task 2: Migration 009 — `trade_commands` accepts an open

**Files:**
- Create: `src/journal/store/migrations/009_open_command.sql`
- Modify: `src/journal/store/schema.sql:338-361` (the `trade_commands` DDL)
- Modify: `src/journal/store/db.py:20` (`SCHEMA_VERSION`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a `trade_commands` table whose `kind` CHECK includes `'open'`, whose `position_id` is nullable, and which has three new columns — `symbol TEXT`, `direction TEXT`, `price_ref REAL`. `SCHEMA_VERSION == 9`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_migration_009_allows_an_open_command(tmp_path):
    """The audit trail of real orders must survive a table rebuild, and the new
    shape must accept exactly the rows the open path needs."""
    import sqlite3
    from journal.store.db import SCHEMA_VERSION, connect, current_version

    db = tmp_path / "m009.db"

    # Build the PRE-009 table by hand and put a row in it, so the test proves
    # the migration copies data rather than just producing the right columns.
    raw = sqlite3.connect(str(db))
    raw.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
        INSERT INTO schema_version (version, applied_at) VALUES (8, 1);
        CREATE TABLE trade_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_login INTEGER NOT NULL,
            position_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN
                ('modify_sltp','close','close_partial','add_volume')),
            sl REAL, tp REAL, volume REAL,
            requested_msc INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                ('pending','claimed','sent','done','failed','rejected')),
            claimed_msc INTEGER, completed_msc INTEGER, retcode INTEGER,
            result_deal INTEGER, result_order INTEGER, result_volume REAL,
            result_price REAL, broker_comment TEXT, error TEXT, raw_json TEXT
        );
        INSERT INTO trade_commands (account_login, position_id, kind, sl, tp,
                                    volume, requested_msc, status)
        VALUES (7, 111, 'modify_sltp', 4030.0, NULL, NULL, 1234, 'done');
        """
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    assert current_version(conn) == SCHEMA_VERSION == 9

    # (a) the pre-existing row survived, intent columns untouched
    old = conn.execute("SELECT * FROM trade_commands WHERE id = 1").fetchone()
    assert old["kind"] == "modify_sltp"
    assert old["position_id"] == 111
    assert abs(old["sl"] - 4030.0) < 1e-9
    assert old["status"] == "done"
    assert old["symbol"] is None and old["direction"] is None and old["price_ref"] is None

    # (b) an open row is now insertable: NULL position_id, symbol/direction set
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, symbol, "
        "direction, sl, tp, volume, price_ref, requested_msc, status) "
        "VALUES (7, NULL, 'open', 'XAUUSDc', 'buy', 4030.0, 4045.0, 0.1, 4035.0, 5678, 'pending')",
    )
    conn.commit()
    new = conn.execute("SELECT * FROM trade_commands WHERE kind = 'open'").fetchone()
    assert new["position_id"] is None
    assert new["symbol"] == "XAUUSDc" and new["direction"] == "buy"
    assert abs(new["price_ref"] - 4035.0) < 1e-9

    # (c) the CHECK constraints still bite
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO trade_commands (account_login, position_id, kind, "
            "requested_msc) VALUES (7, NULL, 'teleport', 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO trade_commands (account_login, position_id, kind, "
            "direction, requested_msc) VALUES (7, NULL, 'open', 'sideways', 1)"
        )
    conn.close()
```

If `tests/test_db.py` does not import `pytest` or `sqlite3` at module level, add those imports at the top of the file.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py -k migration_009 -v`
Expected: FAIL — `assert 8 == 9`, because `SCHEMA_VERSION` is still 8 and no 009 migration exists.

- [ ] **Step 3: Write the migration**

Create `src/journal/store/migrations/009_open_command.sql`:

```sql
-- Migration 009: trade_commands accepts an 'open' command (M9 extension).
--
-- Three changes SQLite cannot make in place — a CHECK constraint cannot be
-- ALTERed and `position_id` is NOT NULL — so the table is rebuilt:
--
--   * `kind` CHECK gains 'open'. An open is the first command in this project
--     that creates a position rather than acting on one.
--   * `position_id` becomes nullable. An open has no position until the broker
--     answers; a sentinel 0 would collide with the audit-log queries that join
--     on it.
--   * three new columns. For an 'open' the symbol and direction cannot be read
--     off a position row, so they live here; `price_ref` records the price the
--     human sized against (evidence, and the re-validation fallback when the
--     bridge cannot supply a fresh tick). All three stay NULL for every other
--     kind, where the position row remains the source of truth.
--
-- This table is the audit log of real orders. The copy is column-explicit so a
-- future column added to one side cannot silently shift the data.
--
-- The runner wraps this file in BEGIN/COMMIT (store/db.py:migrate), so either
-- the whole rebuild lands or none of it does.

CREATE TABLE trade_commands_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login   INTEGER NOT NULL,
    position_id     INTEGER,                -- NULL for 'open' (no position yet)
    kind            TEXT NOT NULL CHECK (kind IN
                        ('modify_sltp','close','close_partial','add_volume','open')),
    symbol          TEXT,                   -- 'open' only; verbatim MT5 symbol (rule 11)
    direction       TEXT CHECK (direction IN ('buy','sell')),  -- 'open' only
    price_ref       REAL,                   -- 'open' only; price the human sized against
    sl              REAL,
    tp              REAL,
    volume          REAL,
    requested_msc   INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                        ('pending','claimed','sent','done','failed','rejected')),
    claimed_msc     INTEGER,
    completed_msc   INTEGER,
    retcode         INTEGER,
    result_deal     INTEGER,
    result_order    INTEGER,
    result_volume   REAL,
    result_price    REAL,
    broker_comment  TEXT,
    error           TEXT,
    raw_json        TEXT
);

INSERT INTO trade_commands_new
    (id, account_login, position_id, kind, sl, tp, volume, requested_msc,
     status, claimed_msc, completed_msc, retcode, result_deal, result_order,
     result_volume, result_price, broker_comment, error, raw_json)
SELECT
     id, account_login, position_id, kind, sl, tp, volume, requested_msc,
     status, claimed_msc, completed_msc, retcode, result_deal, result_order,
     result_volume, result_price, broker_comment, error, raw_json
FROM trade_commands;

DROP TABLE trade_commands;
ALTER TABLE trade_commands_new RENAME TO trade_commands;

CREATE INDEX IF NOT EXISTS ix_cmd_pending  ON trade_commands (account_login, status, id);
CREATE INDEX IF NOT EXISTS ix_cmd_position ON trade_commands (account_login, position_id, id);
```

- [ ] **Step 4: Update `schema.sql` and `SCHEMA_VERSION` to match**

In `src/journal/store/schema.sql`, replace the `trade_commands` DDL (the `CREATE TABLE IF NOT EXISTS trade_commands (...)` block) with the same column list as `trade_commands_new` above — same order, same constraints, keeping the existing comment block above it and adding to it:

```sql
--   'open': the only kind with no position yet. Carries symbol/direction/
--   price_ref instead, and a NULL position_id until the broker answers.
```

In `src/journal/store/db.py:20`:

```python
SCHEMA_VERSION = 9
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py -k migration_009 -v`
Expected: PASS

- [ ] **Step 6: Verify a fresh DB and an existing DB agree**

Run: `uv run pytest tests/ -v`
Expected: PASS. If any test asserts the old `SCHEMA_VERSION` or the old column set, update it — a fresh DB built from `schema.sql` and a migrated DB must end up identical.

Then check the real database migrates:

```bash
cp data/journal.db /tmp/journal-pre009.db
uv run journal rebuild
```
Expected: `rebuild` succeeds. (`trade_commands` is not derived, so `rebuild` does not touch it — this confirms the migration ran and nothing downstream broke.)

- [ ] **Step 7: Commit**

```bash
git add src/journal/store/migrations/009_open_command.sql src/journal/store/schema.sql src/journal/store/db.py tests/test_db.py
git commit -m "feat(store): migration 009 — trade_commands accepts an 'open' command"
```

---

### Task 3: Validate an `open` command

**Files:**
- Modify: `src/journal/domain/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `risk_amount` and (indirectly) Task 1's module.
- Produces:
  - `MAX_RISK_PCT: float = 5.0`
  - `KINDS` now contains `"open"`; `_OPENING` now contains `"open"`.
  - `validate(kind, position, spec, *, sl=None, tp=None, volume=None, balance=None) -> None` — one new keyword-only parameter, defaulting to `None` so no existing caller changes.
  - The synthetic position shape an `open` caller must supply:
    `{"position_id": None, "symbol": str, "direction": "buy"|"sell", "price_current": float, "volume": None, "sl": 0.0, "tp": 0.0}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
from journal.domain.commands import MAX_RISK_PCT

# A synthetic "position" for an open: there is no position yet, so the caller
# supplies the symbol, the direction (derived from the SL side), and the price
# the human sized against. Every existing check reads this the same way it reads
# a real open_positions row.
_OPEN_BUY = {
    "position_id": None,
    "symbol": "XAUUSDc",
    "direction": "buy",
    "price_current": 4035.0,
    "volume": None,
    "sl": 0.0,
    "tp": 0.0,
}

# XAUUSDc, measured: tick_size 0.001, tick_value 0.1 USC (docs §7). Not in the
# shared _SPEC above, which only carries the order-validation fields.
_OPEN_SPEC = dict(_SPEC, tick_size=0.001, tick_value=0.1)

# entry 4035 / SL 4030 at 0.10 lot = 50 USC (the §8 reference figure).
_BALANCE = 100_000.0   # USC = $1000. 50 USC is 0.05% of it — comfortably legal.


def _open(**over):
    return dict(_OPEN_BUY, **over)


def _ospec(**over):
    return dict(_OPEN_SPEC, **over)


def test_open_is_a_known_kind():
    from journal.domain.commands import KINDS
    assert "open" in KINDS


def test_a_valid_open_passes():
    validate("open", _open(), _ospec(), sl=4030.0, tp=4045.0, volume=0.10,
             balance=_BALANCE)


def test_an_open_without_a_stop_is_refused():
    """The whole feature is risk-first: with no SL there is no computable risk,
    so there is no size to derive and no ceiling to check against."""
    with pytest.raises(CommandError, match="SL"):
        validate("open", _open(), _ospec(), sl=None, volume=0.10, balance=_BALANCE)
    with pytest.raises(CommandError, match="SL"):
        validate("open", _open(), _ospec(), sl=0.0, volume=0.10, balance=_BALANCE)


def test_an_open_with_the_stop_on_the_wrong_side_is_refused():
    # A buy's stop above the price. `_check_level` already knows this; the test
    # is here to prove the open path routes through it.
    with pytest.raises(CommandError, match="BAWAH"):
        validate("open", _open(), _ospec(), sl=4040.0, volume=0.10, balance=_BALANCE)
    with pytest.raises(CommandError, match="ATAS"):
        validate("open", _open(direction="sell", price_current=4035.0), _ospec(),
                 sl=4030.0, volume=0.10, balance=_BALANCE)


def test_an_open_with_the_target_on_the_wrong_side_is_refused():
    with pytest.raises(CommandError, match="ATAS"):
        validate("open", _open(), _ospec(), sl=4030.0, tp=4020.0, volume=0.10,
                 balance=_BALANCE)


def test_an_open_obeys_the_lot_cap():
    with pytest.raises(CommandError, match="1"):
        validate("open", _open(), _ospec(), sl=4030.0, volume=1.01, balance=_BALANCE)


def test_an_open_obeys_the_broker_volume_rules():
    with pytest.raises(CommandError, match="minimum"):
        validate("open", _open(), _ospec(), sl=4030.0, volume=0.005, balance=_BALANCE)
    with pytest.raises(CommandError, match="kelipatan"):
        validate("open", _open(), _ospec(), sl=4030.0, volume=0.015, balance=_BALANCE)


def test_an_open_obeys_trade_mode():
    with pytest.raises(CommandError, match="short-only"):
        validate("open", _open(), _ospec(trade_mode=2), sl=4030.0, volume=0.10,
                 balance=_BALANCE)
    with pytest.raises(CommandError, match="close-only"):
        validate("open", _open(), _ospec(trade_mode=3), sl=4030.0, volume=0.10,
                 balance=_BALANCE)


def test_the_risk_ceiling_is_five_percent():
    assert MAX_RISK_PCT == 5.0


def test_an_open_just_under_the_risk_ceiling_passes():
    # 5% of 1000 USC balance is 50 USC — exactly the reference figure at 0.10
    # lot. Rule 5: at the limit is allowed, compared with tolerance.
    validate("open", _open(), _ospec(), sl=4030.0, volume=0.10, balance=1000.0)


def test_an_open_over_the_risk_ceiling_is_refused():
    # Same 50 USC risk against a 900 USC balance is 5.6% — refused, and the
    # message states both numbers so the human can act on it.
    with pytest.raises(CommandError, match="5"):
        validate("open", _open(), _ospec(), sl=4030.0, volume=0.10, balance=900.0)


def test_an_open_with_an_unknown_balance_is_refused():
    """Rule 4 where it binds. An unknown ceiling is not permission to open."""
    with pytest.raises(CommandError, match="balance|sync"):
        validate("open", _open(), _ospec(), sl=4030.0, volume=0.10, balance=None)


def test_an_open_with_an_unknown_tick_spec_is_refused():
    """Without tick_size/tick_value the risk is not computable, so the ceiling
    cannot be enforced — refuse rather than open an unmeasured position."""
    with pytest.raises(CommandError, match="sync|risiko"):
        validate("open", _open(), _ospec(tick_value=None), sl=4030.0, volume=0.10,
                 balance=_BALANCE)


def test_the_risk_ceiling_does_not_apply_to_a_close():
    """The module's standing asymmetry: nothing that would stop a human
    REDUCING exposure applies to a close."""
    validate("close", _buy(volume=5.0), _spec(), volume=None, balance=None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_commands.py -k open -v`
Expected: FAIL — `ImportError: cannot import name 'MAX_RISK_PCT'`.

- [ ] **Step 3: Implement**

In `src/journal/domain/commands.py`:

Add the import at the top, next to the adapter import:

```python
from .risk import risk_amount
```

Add the constant below `MAX_LOT`:

```python
# The second hard ceiling, and the one that scales with the account: no single
# order may put more than this share of `accounts.balance` at stake. MAX_LOT
# alone does not bound risk — one lot with a distant stop is a large loss. A
# constant here rather than a pref, for the same reason MAX_LOT is: a limit the
# UI can raise is not a limit.
MAX_RISK_PCT = 5.0
```

Extend `KINDS` and `_OPENING`:

```python
KINDS = ("modify_sltp", "close", "close_partial", "add_volume", "open")

# The kinds that can INCREASE exposure. Everything stricter applies only to
# these; `close` is deliberately absent.
_OPENING = ("add_volume", "open")
```

Add the risk check:

```python
def _check_risk(
    position: Mapping[str, Any] | Any, spec: Mapping[str, Any] | Any,
    sl: float | None, volume: float | None, balance: float | None,
) -> None:
    """The risk ceiling, for an `open` only.

    An open is the only command that both creates exposure and knows its own
    stop, so it is the only one whose risk can be bounded before it is sent.
    Every unknown here refuses rather than defaults: an unmeasurable risk is not
    a small one.
    """
    if sl is None or abs(sl) < _TOL:
        raise CommandError(
            "SL wajib diisi untuk membuka posisi — tanpa SL, risikonya tidak "
            "bisa dihitung dan ukuran lot tidak bisa diturunkan."
        )
    if balance is None:
        raise CommandError(
            "Balance akun belum diketahui — jalankan `journal sync` dulu. "
            "Membuka posisi ditolak selama batas risikonya tidak bisa dihitung."
        )

    risk = risk_amount(
        _get(position, "price_current"), sl,
        _get(spec, "tick_size"), _get(spec, "tick_value"), volume,
    )
    if risk is None:
        raise CommandError(
            "Risiko tidak bisa dihitung (tick_size/tick_value simbol atau harga "
            "terkini belum diketahui) — jalankan `journal sync` dulu. Posisi "
            "tidak dibuka tanpa risiko yang terukur."
        )

    ceiling = balance * MAX_RISK_PCT / 100.0
    if risk > ceiling + _TOL:
        pct = (risk / balance * 100.0) if balance else float("inf")
        raise CommandError(
            f"Risiko {risk:.2f} ({pct:.2f}% dari balance) melebihi batas keras "
            f"{MAX_RISK_PCT}% ({ceiling:.2f}). Perkecil lot atau dekatkan SL."
        )
```

Extend `validate`:

```python
def validate(
    kind: str,
    position: Mapping[str, Any] | Any,
    spec: Mapping[str, Any] | Any,
    *,
    sl: float | None = None,
    tp: float | None = None,
    volume: float | None = None,
    balance: float | None = None,
) -> None:
    """Raise `CommandError` if this command must not be sent. Returns None when
    it may be.

    `balance` is read only for `open` — the one kind whose risk can be bounded
    before it exists. Every other kind ignores it.
    """
    if kind not in KINDS:
        raise CommandError(f"Jenis perintah tidak dikenal: {kind!r}.")

    direction = _get(position, "direction")
    if direction not in ("buy", "sell"):
        raise CommandError(f"Arah posisi tidak diketahui: {direction!r}.")

    _check_trade_mode(kind, spec, direction)

    if kind == "modify_sltp":
        if sl is None and tp is None:
            raise CommandError("Tidak ada yang diubah — isi SL atau TP.")
        price = _get(position, "price_current")
        _check_level("sl", sl, direction, price, spec)
        _check_level("tp", tp, direction, price, spec)
        return

    if kind == "open":
        # Order matters: volume rules first (the cheapest and most specific
        # message), then the levels, then the risk — which needs both a valid
        # volume and a valid SL to mean anything.
        _check_volume(kind, position, spec, volume)
        price = _get(position, "price_current")
        _check_level("sl", sl, direction, price, spec)
        _check_level("tp", tp, direction, price, spec)
        _check_risk(position, spec, sl, volume, balance)
        return

    if kind in ("close_partial", "add_volume"):
        _check_volume(kind, position, spec, volume)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_commands.py -v`
Expected: PASS, including every pre-existing test — `_check_volume` and `_check_level` were not modified, only routed to.

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/commands.py tests/test_commands.py
git commit -m "feat(commands): validate the 'open' kind, add MAX_RISK_PCT ceiling"
```

---

### Task 4: Build the `open` request

**Files:**
- Modify: `src/journal/domain/commands.py` (`build_request`)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: Task 3's `validate` and synthetic position shape.
- Produces: `build_request("open", position, spec, sl=..., tp=..., volume=..., balance=...) -> TradeRequest` with `action=TradeAction.DEAL`, `position_id=None`, `order_type` matching the direction, and `sl`/`tp` carried through.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
def test_an_open_request_carries_no_position_id():
    """THE field. On a DEAL, `position_id` means 'close this one' — the opposite
    of opening. An open must leave it None."""
    req = build_request("open", _open(), _ospec(), sl=4030.0, tp=4045.0,
                        volume=0.10, balance=_BALANCE)
    assert req.action is TradeAction.DEAL
    assert req.position_id is None


def test_an_open_request_uses_the_same_direction_not_the_opposite():
    buy = build_request("open", _open(), _ospec(), sl=4030.0, volume=0.10,
                        balance=_BALANCE)
    assert buy.order_type is OrderType.BUY
    sell = build_request("open", _open(direction="sell"), _ospec(), sl=4040.0,
                         volume=0.10, balance=_BALANCE)
    assert sell.order_type is OrderType.SELL


def test_an_open_request_carries_the_levels_and_the_volume():
    req = build_request("open", _open(), _ospec(), sl=4030.0, tp=4045.0,
                        volume=0.10, balance=_BALANCE)
    assert abs(req.sl - 4030.0) < 1e-9
    assert abs(req.tp - 4045.0) < 1e-9
    assert abs(req.volume - 0.10) < 1e-9
    assert req.symbol == "XAUUSDc"          # verbatim MT5 symbol (rule 11)
    assert req.filling is OrderFilling.FOK  # from the spec's bitmask


def test_an_open_request_sends_no_price():
    """Execution is MARKET (trade_exemode=2, measured on all three symbols): the
    broker fills at its own price and ignores this field. A price that is
    already stale on arrival invites INVALID_PRICE and buys nothing."""
    req = build_request("open", _open(), _ospec(), sl=4030.0, volume=0.10,
                        balance=_BALANCE)
    assert req.price is None


def test_building_an_open_validates_first():
    """`build_request` must not become a way around `validate` — a caller that
    forgot still cannot produce an over-ceiling order."""
    with pytest.raises(CommandError):
        build_request("open", _open(), _ospec(), sl=4030.0, volume=0.10,
                      balance=900.0)
    with pytest.raises(CommandError):
        build_request("open", _open(), _ospec(), sl=None, volume=0.10,
                      balance=_BALANCE)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_commands.py -k open_request -v`
Expected: FAIL — the `add_volume` fallthrough at the end of `build_request` produces `position_id=None` but drops `sl`/`tp`, so `test_an_open_request_carries_the_levels_and_the_volume` fails on `req.sl is None`.

- [ ] **Step 3: Implement**

In `src/journal/domain/commands.py`, change `build_request`'s signature to accept and forward `balance`, and add the `open` branch before the `add_volume` fallthrough:

```python
def build_request(
    kind: str,
    position: Mapping[str, Any] | Any,
    spec: Mapping[str, Any] | Any,
    *,
    sl: float | None = None,
    tp: float | None = None,
    volume: float | None = None,
    balance: float | None = None,
) -> TradeRequest:
    """Turn a validated command into the request the adapter will send.

    Validates first, unconditionally: this must not become a way around
    `validate`. A caller that forgot still cannot produce an over-cap request.
    """
    validate(kind, position, spec, sl=sl, tp=tp, volume=volume, balance=balance)
```

...and, immediately before the `# add_volume.` comment block:

```python
    if kind == "open":
        # The first command in this project that CREATES a position. Same shape
        # as add_volume — a plain market DEAL with no position_id — but it
        # carries the levels, because attaching SL/TP to the opening request is
        # the only way they exist from the position's first tick. A separate
        # modify afterwards leaves a window where the position is live and
        # unprotected, and the whole point of this feature is the stop.
        return TradeRequest(
            action=TradeAction.DEAL,
            position_id=None,
            symbol=symbol,
            order_type=_same(direction),
            volume=volume,
            sl=sl,
            tp=tp,
            filling=filling,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_commands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/commands.py tests/test_commands.py
git commit -m "feat(commands): build_request for the 'open' kind"
```

---

### Task 5: Queue an open

**Files:**
- Modify: `src/journal/execute.py`
- Test: `tests/test_execute.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 4.
- Produces:
  - `load_open_context(conn, login, symbol, direction, price) -> tuple[dict, sqlite3.Row]`
  - `enqueue_open(conn, login, *, symbol, direction, sl, tp, volume, price_ref) -> int`
  - `account_balance(conn, login) -> float | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_execute.py`. If that file does not exist, create it following the fixture style of `tests/test_web.py` (`_seed_account`, `_seed_spec`); the helpers below assume a `conn` fixture that yields a migrated in-memory DB with one account seeded.

```python
import pytest

from journal.domain.commands import CommandError
from journal.execute import enqueue_open, get_command, load_open_context

_LOGIN = 7


def _seed(conn, *, balance=100_000.0, trade_mode=4):
    conn.execute(
        "INSERT OR REPLACE INTO accounts (login, currency, balance, margin_mode, "
        "first_seen_at) VALUES (?, 'USC', ?, 2, 1)", (_LOGIN, balance),
    )
    conn.execute(
        "INSERT OR REPLACE INTO symbol_specs (symbol, digits, point, tick_size, "
        "tick_value, volume_min, volume_max, volume_step, stops_level, "
        "freeze_level, trade_mode, filling_mode) VALUES "
        "('XAUUSDc', 3, 0.001, 0.001, 0.1, 0.01, 200.0, 0.01, 0, 0, ?, 3)",
        (trade_mode,),
    )
    conn.commit()


def test_enqueue_open_writes_one_pending_row(conn):
    _seed(conn)
    cmd_id = enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                          sl=4030.0, tp=4045.0, volume=0.10, price_ref=4035.0)
    row = get_command(conn, cmd_id)
    assert row["kind"] == "open"
    assert row["status"] == "pending"
    assert row["position_id"] is None
    assert row["symbol"] == "XAUUSDc"
    assert row["direction"] == "buy"
    assert abs(row["price_ref"] - 4035.0) < 1e-9
    assert abs(row["sl"] - 4030.0) < 1e-9
    assert abs(row["tp"] - 4045.0) < 1e-9
    assert abs(row["volume"] - 0.10) < 1e-9


def test_a_refused_open_writes_nothing(conn):
    # 50 USC risk against a 100 USC balance is 50% — far over the ceiling.
    _seed(conn, balance=100.0)
    with pytest.raises(CommandError):
        enqueue_open(conn, _LOGIN, symbol="XAUUSDc", direction="buy",
                     sl=4030.0, tp=0.0, volume=0.10, price_ref=4035.0)
    n = conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0]
    assert n == 0


def test_an_open_on_an_unknown_symbol_is_refused(conn):
    _seed(conn)
    with pytest.raises(CommandError, match="sync|spesifikasi|Spesifikasi"):
        enqueue_open(conn, _LOGIN, symbol="GBPUSDc", direction="buy",
                     sl=1.2, tp=0.0, volume=0.10, price_ref=1.25)


def test_load_open_context_builds_a_synthetic_position(conn):
    _seed(conn)
    pos, spec = load_open_context(conn, _LOGIN, "XAUUSDc", "buy", 4035.0)
    assert pos["position_id"] is None
    assert pos["symbol"] == "XAUUSDc"
    assert pos["direction"] == "buy"
    assert abs(pos["price_current"] - 4035.0) < 1e-9
    assert spec["symbol"] == "XAUUSDc"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_execute.py -v`
Expected: FAIL — `ImportError: cannot import name 'enqueue_open' from 'journal.execute'`

- [ ] **Step 3: Implement**

In `src/journal/execute.py`, add below `load_context`:

```python
def account_balance(conn: sqlite3.Connection, login: int) -> float | None:
    """`accounts.balance` in account currency (USC), or None if unknown.

    A SNAPSHOT from the last `journal sync`, not a live figure — which is why
    the risk ceiling it feeds is a hard 5% rather than a knife-edge limit.
    """
    row = conn.execute(
        "SELECT balance FROM accounts WHERE login = ?", (login,)
    ).fetchone()
    return None if row is None or row["balance"] is None else float(row["balance"])


def load_open_context(
    conn: sqlite3.Connection, login: int, symbol: str, direction: str, price: float,
) -> tuple[dict, sqlite3.Row]:
    """The (position, spec) pair for an OPEN, where no position exists yet.

    The position is synthesised from what the human chose, in exactly the shape
    `domain/commands` reads off a real `open_positions` row — which is why the
    open path needs no new branch inside `_check_trade_mode`, `_check_volume`,
    or `_check_level`. Same rules, same messages, one code path.
    """
    spec = _spec(conn, symbol)
    if spec is None:
        raise CommandError(
            f"Spesifikasi simbol {symbol} belum ada di database — "
            f"jalankan `journal sync` dulu."
        )
    pos = {
        "position_id": None,
        "symbol": symbol,
        "direction": direction,
        "price_current": price,
        "volume": None,
        "sl": 0.0,
        "tp": 0.0,
    }
    return pos, spec


def enqueue_open(
    conn: sqlite3.Connection,
    login: int,
    *,
    symbol: str,
    direction: str,
    sl: float | None,
    tp: float | None,
    volume: float | None,
    price_ref: float | None,
) -> int:
    """Validate, then queue an open. Returns the new command id.

    A refused open writes NOTHING, exactly as `enqueue` does. `price_ref` is
    stored as evidence of the price the human sized against — it is NOT sent to
    the broker (execution is MARKET) and it is not the fill price.
    """
    pos, spec = load_open_context(conn, login, symbol, direction, price_ref)
    validate("open", pos, spec, sl=sl, tp=tp, volume=volume,
             balance=account_balance(conn, login))

    cur = conn.execute(
        "INSERT INTO trade_commands "
        "(account_login, position_id, kind, symbol, direction, price_ref, "
        " sl, tp, volume, requested_msc, status) "
        "VALUES (?, NULL, 'open', ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (login, symbol, direction, price_ref, sl, tp, volume, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_execute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/journal/execute.py tests/test_execute.py
git commit -m "feat(execute): enqueue_open queues an open command"
```

---

### Task 6: Execute an open

**Files:**
- Modify: `src/journal/ingest/live.py:189-244` (`_execute_one_command`)
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: Tasks 4 and 5.
- Produces: `_execute_one_command` handles `kind == "open"` — fetches a fresh tick, re-validates, sends or rejects. No signature change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live.py`, following the existing `FakeLiveClient` style:

```python
class FakeOpenClient(FakeLiveClient):
    """Records what was sent and lets a test control the tick."""

    def __init__(self, *a, tick=None, tick_raises=False, **kw):
        super().__init__(*a, **kw)
        self._tick = tick
        self._tick_raises = tick_raises
        self.sent = []
        self.checked = []

    def symbol_info_tick(self, symbol):
        if self._tick_raises:
            raise RuntimeError("bridge down")
        return self._tick

    def order_check(self, req):
        self.checked.append(req)
        return None

    def order_send(self, req):
        self.sent.append(req)
        from journal.adapter.base import TradeResult, TradeRetcode
        return TradeResult(retcode=TradeRetcode.DONE, deal=1, order=2,
                           volume=req.volume, price=4035.0, comment="ok", raw={})


def _seed_open_command(conn, login=7, *, sl=4030.0, tp=4045.0, volume=0.10,
                       price_ref=4035.0):
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, symbol, "
        "direction, price_ref, sl, tp, volume, requested_msc, status) "
        "VALUES (?, NULL, 'open', 'XAUUSDc', 'buy', ?, ?, ?, ?, 1, 'pending')",
        (login, price_ref, sl, tp, volume),
    )
    conn.commit()


def test_an_open_is_sent_using_a_fresh_tick(conn):
    from journal.adapter.base import Tick
    from journal.ingest.live import _execute_one_command
    _seed_specs_and_account(conn)          # existing helper in this file
    _seed_open_command(conn)
    client = FakeOpenClient(tick=Tick(bid=4036.0, ask=4036.2))

    cmd_id, status = _execute_one_command(client, conn, 7)

    assert status == "done"
    assert len(client.sent) == 1
    req = client.sent[0]
    assert req.position_id is None
    assert abs(req.volume - 0.10) < 1e-9
    assert abs(req.sl - 4030.0) < 1e-9


def test_an_open_falls_back_to_price_ref_when_the_tick_is_unavailable(conn):
    """A stale reference price is worse than a fresh one and better than no
    side-check at all. The order still goes out."""
    from journal.ingest.live import _execute_one_command
    _seed_specs_and_account(conn)
    _seed_open_command(conn)
    client = FakeOpenClient(tick_raises=True)

    cmd_id, status = _execute_one_command(client, conn, 7)

    assert status == "done"
    assert len(client.sent) == 1


def test_an_open_is_rejected_when_the_market_crossed_the_stop(conn):
    """The market moved through the SL between enqueue and send: a buy's stop is
    now ABOVE the price. Refuse WITHOUT sending — this is the case the fresh
    tick exists for."""
    from journal.adapter.base import Tick
    from journal.ingest.live import _execute_one_command
    _seed_specs_and_account(conn)
    _seed_open_command(conn, sl=4030.0, price_ref=4035.0)
    client = FakeOpenClient(tick=Tick(bid=4025.0, ask=4025.2))

    cmd_id, status = _execute_one_command(client, conn, 7)

    assert status == "rejected"
    assert client.sent == []
    row = conn.execute(
        "SELECT error FROM trade_commands WHERE id = ?", (cmd_id,)
    ).fetchone()
    assert "BAWAH" in row["error"]


def test_the_volume_is_not_recomputed_at_send_time(conn):
    """The stored volume IS the intent, the same as add_volume. The SL is an
    absolute level, so the only error the queue delay introduces is entry
    slippage — which MARKET execution has regardless."""
    from journal.adapter.base import Tick
    from journal.ingest.live import _execute_one_command
    _seed_specs_and_account(conn)
    _seed_open_command(conn, volume=0.10, price_ref=4035.0)
    client = FakeOpenClient(tick=Tick(bid=4033.0, ask=4033.2))

    _execute_one_command(client, conn, 7)

    assert abs(client.sent[0].volume - 0.10) < 1e-9
```

If `_seed_specs_and_account` does not already exist in `tests/test_live.py`, write it to insert the same `accounts` and `symbol_specs` rows as `_seed` in Task 5.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_live.py -k open -v`
Expected: FAIL — `load_context` raises `CommandError` for a NULL `position_id`, so every open is rejected and `test_an_open_is_sent_using_a_fresh_tick` fails on `status == "rejected"`.

- [ ] **Step 3: Implement**

In `src/journal/ingest/live.py`, add above `_execute_one_command`:

```python
def _open_price_for(client: MT5Client, symbol: str, price_ref: float | None) -> float | None:
    """The price an OPEN is re-validated against at send time.

    A fresh tick, because the market has moved since the human sized the order
    and the SL may now be on the wrong side — the one failure this re-check
    exists to catch. Falls back to the stored `price_ref` when the bridge cannot
    answer: a stale price still catches a gross error, and refusing every order
    whenever a tick call hiccups would be its own kind of trap.

    This is the ingest layer, so calling the client here is allowed (rules 1
    and 12 bind `web/` and `domain/`).
    """
    try:
        tick = client.symbol_info_tick(symbol)
    except Exception:
        log.warning("live: no fresh tick for %s — re-validating against price_ref", symbol)
        return price_ref
    if tick is None:
        return price_ref
    # Mid of bid/ask; either alone biases the side-check by the spread. Falls
    # back through last, then price_ref — rule 4 all the way down.
    if tick.bid is not None and tick.ask is not None:
        return (tick.bid + tick.ask) / 2.0
    if tick.last is not None:
        return tick.last
    return price_ref
```

Then, inside `_execute_one_command`, replace the context-loading block:

```python
    try:
        if row["kind"] == "open":
            price = _open_price_for(client, row["symbol"], row["price_ref"])
            pos, spec = load_open_context(
                conn, login, row["symbol"], row["direction"], price
            )
            req = build_request(
                "open", pos, spec, sl=row["sl"], tp=row["tp"], volume=row["volume"],
                balance=account_balance(conn, login),
            )
        else:
            pos, spec = load_context(conn, login, row["position_id"])
            req = build_request(
                row["kind"], pos, spec, sl=row["sl"], tp=row["tp"], volume=row["volume"]
            )
    except CommandError as e:
        # Valid when queued, not now. Refuse WITHOUT sending.
        reject(conn, cmd_id, str(e))
        log.info("live: command %d rejected — %s", cmd_id, e)
        return cmd_id, "rejected"
```

Extend the module's imports from `..execute` to include `account_balance` and `load_open_context`, and update the docstring's lifecycle notes with one line:

```
      * an `open` has no position to load, so it re-validates against a FRESH
        tick — if the market crossed the stop while the command sat in the
        queue, the SL is now on the wrong side and the order is rejected, not
        sent.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS. This task changed a shared execution path, so the full suite is the check, not just `test_live.py`.

- [ ] **Step 6: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "feat(live): execute an 'open' command against a fresh tick"
```

---

### Task 7: Server-side sizing — `/api/size`

**Files:**
- Modify: `src/journal/web/views.py`
- Modify: `src/journal/web/app.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 5.
- Produces:
  - `views.size_order(conn, login, *, symbol, entry, sl, tp, risk_mode, risk_value) -> dict` with keys `volume`, `risk_usc`, `risk_pct`, `distance`, `rr`, `direction`, `error`.
  - `POST /api/size` returning that dict as JSON, status 200 even when `error` is set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`, using the existing `client` fixture and `_seed_account` / `_seed_spec` helpers (extend `_seed_spec` so it writes `tick_size=0.001` and `tick_value=0.1` if it does not already):

```python
def test_size_returns_the_reference_lot(client, conn):
    _seed_account(conn, balance=100_000.0)   # 100000 USC = $1000
    _seed_spec(conn)
    r = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": 4045.0,
        "risk_mode": "usc", "risk_value": 50.0,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["error"] is None
    assert abs(d["volume"] - 0.10) < 1e-9
    assert abs(d["risk_usc"] - 50.0) < 1e-6
    assert d["direction"] == "buy"
    assert abs(d["distance"] - 5.0) < 1e-9
    assert abs(d["rr"] - 2.0) < 1e-9          # (4045-4035) / (4035-4030)


def test_size_percent_mode_reads_the_balance(client, conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    # 0.05% of 100000 USC = 50 USC -> the same 0.10 lot.
    r = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "pct", "risk_value": 0.05,
    })
    d = r.json()
    assert d["error"] is None
    assert abs(d["volume"] - 0.10) < 1e-9
    assert abs(d["risk_pct"] - 0.05) < 1e-6
    assert d["rr"] is None                     # no TP set


def test_size_reports_the_realised_risk_of_the_rounded_lot(client, conn):
    """The lot is floored to the broker's step, so the risk actually taken is
    slightly BELOW the budget. Report what will happen, not what was asked."""
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    r = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "usc", "risk_value": 68.0,     # -> 0.136 lot -> floor 0.13
    })
    d = r.json()
    assert abs(d["volume"] - 0.13) < 1e-9
    assert abs(d["risk_usc"] - 65.0) < 1e-6         # 0.13 lot, not 68
    assert d["risk_usc"] <= 68.0 + 1e-9


def test_size_refuses_a_stop_at_the_price(client, conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    d = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4035.0, "tp": None,
        "risk_mode": "usc", "risk_value": 50.0,
    }).json()
    assert d["volume"] is None
    assert d["error"]                            # human-readable, non-empty
    assert d["direction"] is None


def test_size_refuses_a_budget_too_small_for_the_distance(client, conn):
    """0.4 USC over a 5-point XAUUSDc stop sizes to 0.0008 lot, which floors to
    0 — below volume_min. Say so; do not clamp up to the minimum."""
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    d = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "usc", "risk_value": 0.4,
    }).json()
    assert d["volume"] is None
    assert "minimum" in d["error"] or "kecil" in d["error"]


def test_size_refuses_over_the_risk_ceiling(client, conn):
    _seed_account(conn, balance=1000.0)          # 1000 USC; 5% = 50 USC
    _seed_spec(conn)
    d = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "usc", "risk_value": 80.0,
    }).json()
    assert d["volume"] is None
    assert "5" in d["error"]


def test_size_refuses_an_unknown_symbol(client, conn):
    _seed_account(conn, balance=100_000.0)
    d = client.post("/api/size", json={
        "symbol": "GBPUSDc", "entry": 1.25, "sl": 1.24, "tp": None,
        "risk_mode": "usc", "risk_value": 50.0,
    }).json()
    assert d["volume"] is None
    assert "sync" in d["error"]


def test_size_refuses_percent_mode_with_an_unknown_balance(client, conn):
    _seed_account(conn, balance=None)
    _seed_spec(conn)
    d = client.post("/api/size", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "pct", "risk_value": 1.0,
    }).json()
    assert d["volume"] is None
    assert "sync" in d["error"] or "balance" in d["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web.py -k size -v`
Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Implement `size_order`**

In `src/journal/web/views.py`, add:

```python
def size_order(
    conn: sqlite3.Connection, login: int, *,
    symbol: str, entry: float | None, sl: float | None, tp: float | None,
    risk_mode: str, risk_value: float | None,
) -> dict:
    """Derive a lot size from a stop distance and a risk budget.

    Writes nothing. Returns the numbers the panel shows PLUS the reason it
    cannot, so a half-finished drag renders an explanation instead of an HTTP
    error. `error` non-null always means `volume` is null: there is no partial
    answer here, and a number the human could act on must never appear beside a
    refusal.

    The reported `risk_usc` is the risk of the ROUNDED lot — what will actually
    be at stake — which is always at or below the budget, never above it.

    This is arithmetic on numbers the human supplied. It does not choose the
    symbol, the side, the stop, or the moment (rule 9).
    """
    out = {
        "volume": None, "risk_usc": None, "risk_pct": None,
        "distance": None, "rr": None, "direction": None, "error": None,
    }

    spec = conn.execute(
        "SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if spec is None:
        out["error"] = (
            f"Spesifikasi simbol {symbol} belum ada di database — "
            f"jalankan `journal sync` dulu."
        )
        return out

    balance = execute.account_balance(conn, login)

    direction = risk.direction_for_sl(entry, sl)
    if direction is None:
        out["error"] = (
            "SL harus berada di atas atau di bawah harga sekarang — "
            "tarik garisnya menjauh dari harga."
        )
        return out
    out["direction"] = direction
    out["distance"] = abs(entry - sl)

    # Budget in account currency (USC). Percent mode needs a balance; fixed
    # mode does not — but the ceiling check downstream needs one either way.
    if risk_mode == "pct":
        if balance is None:
            out["error"] = (
                "Balance akun belum diketahui — jalankan `journal sync` dulu, "
                "atau isi risiko dalam USC."
            )
            return out
        if risk_value is None or risk_value <= 0:
            out["error"] = "Risiko harus lebih besar dari 0."
            return out
        budget = balance * risk_value / 100.0
    elif risk_mode == "usc":
        if risk_value is None or risk_value <= 0:
            out["error"] = "Risiko harus lebih besar dari 0."
            return out
        budget = risk_value
    else:
        out["error"] = f"Mode risiko tidak dikenal: {risk_mode!r}."
        return out

    raw = risk.volume_for_risk(
        entry, sl, spec["tick_size"], spec["tick_value"], budget
    )
    volume = risk.floor_to_step(raw, spec["volume_step"])
    if volume is None:
        out["error"] = (
            "Ukuran lot tidak bisa dihitung — tick_size/tick_value/volume_step "
            "simbol belum diketahui. Jalankan `journal sync` dulu."
        )
        return out

    # One refusal path for everything a real order would be refused for, so the
    # panel can never show a lot the confirm step would then reject.
    pos, _ = execute.load_open_context(conn, login, symbol, direction, entry)
    try:
        validate("open", pos, spec, sl=sl, tp=tp, volume=volume, balance=balance)
    except CommandError as e:
        out["error"] = str(e)
        return out

    realised = risk_amount(entry, sl, spec["tick_size"], spec["tick_value"], volume)
    out["volume"] = volume
    out["risk_usc"] = realised
    out["risk_pct"] = (realised / balance * 100.0) if balance else None
    if tp is not None and abs(tp) > 1e-9:
        out["rr"] = abs(tp - entry) / out["distance"]
    return out
```

Add the imports `views.py` needs at the top of the file: `from ..domain import risk`, `from ..domain.risk import risk_amount`, `from ..domain.commands import CommandError, validate` (extend the existing import if one is already there), and `from .. import execute` if not already imported.

- [ ] **Step 4: Add the route**

In `src/journal/web/app.py`, add near the other `/api/live` routes and **before** `@app.post("/api/live/{position_id}/{action}/preview")`:

```python
    # --- risk-based sizing (writes nothing). Shared by replay and live: the
    # panel asks here on every drag, and the live open path recomputes from the
    # same function, so a lot the client "knows" is never trusted.
    @app.post("/api/size")
    def api_size(
        symbol: str = Body(...),
        entry: float | None = Body(None),
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        risk_mode: str = Body("pct"),
        risk_value: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            login = views.account_header(conn)["login"]
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(views.size_order(
            conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
            risk_mode=risk_mode, risk_value=risk_value,
        )))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web.py -k size -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/views.py src/journal/web/app.py tests/test_web.py
git commit -m "feat(web): POST /api/size derives a lot from a stop distance and a risk budget"
```

---

### Task 8: Live open endpoints

**Files:**
- Modify: `src/journal/web/views.py` (`_intent_text`, `preview_open`)
- Modify: `src/journal/web/app.py`
- Modify: `src/journal/store/prefs_store.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Tasks 5 and 7.
- Produces:
  - `views.preview_open(conn, login, *, symbol, entry, sl, tp, risk_mode, risk_value) -> dict` with keys `intent`, `position_id` (always `None`), `kind` (`"open"`), `symbol`, `fields` (`{sl, tp, volume}`), `sizing` (the `size_order` dict).
  - `POST /api/live/open/preview` → that dict, 400 on refusal.
  - `POST /api/live/open` → `{"ok": true, "command_id": int}`, 400 on refusal.
  - `GET /api/risk-prefs` → `{"prefs": <blob|null>}`; `PUT /api/risk-prefs` → `{"ok": true, "updated_ms": int}`.
  - `prefs_store.RISK_KEY = "risk_sizing"`, `get_risk_prefs`, `set_risk_prefs`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
def test_open_preview_writes_nothing_and_returns_the_sized_lot(client, conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    r = client.post("/api/live/open/preview", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": 4045.0,
        "risk_mode": "usc", "risk_value": 50.0,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["kind"] == "open"
    assert d["position_id"] is None
    assert abs(d["fields"]["volume"] - 0.10) < 1e-9
    assert "XAUUSDc" in d["intent"] and "0.1" in d["intent"]
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_open_preview_refuses_over_the_ceiling_without_writing(client, conn):
    _seed_account(conn, balance=1000.0)
    _seed_spec(conn)
    r = client.post("/api/live/open/preview", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "usc", "risk_value": 80.0,
    })
    assert r.status_code == 400
    assert "5" in r.json()["error"]
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_open_enqueues_one_row_with_a_server_computed_volume(client, conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    r = client.post("/api/live/open", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": 4045.0,
        "risk_mode": "usc", "risk_value": 50.0,
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    row = conn.execute("SELECT * FROM trade_commands").fetchone()
    assert row["kind"] == "open"
    assert row["direction"] == "buy"
    assert abs(row["volume"] - 0.10) < 1e-9      # derived here, never sent by the client
    assert abs(row["price_ref"] - 4035.0) < 1e-9


def test_a_literal_open_never_hits_the_position_id_route(client, conn):
    """Route ordering. `/api/live/open/preview` must match the open route, not
    `/api/live/{position_id}/{action}/preview` — which would 422 on parsing
    'open' as an int."""
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    r = client.post("/api/live/open/preview", json={
        "symbol": "XAUUSDc", "entry": 4035.0, "sl": 4030.0, "tp": None,
        "risk_mode": "usc", "risk_value": 50.0,
    })
    assert r.status_code != 422


def test_risk_prefs_round_trip(client, conn):
    assert client.get("/api/risk-prefs").json()["prefs"] is None
    body = {"mode": "pct", "value": 1.0}
    put = client.put("/api/risk-prefs", json=body)
    assert put.status_code == 200 and put.json()["ok"] is True
    assert client.get("/api/risk-prefs").json()["prefs"] == body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web.py -k "open_preview or open_enqueues or literal_open or risk_prefs" -v`
Expected: FAIL — 404 / 422, no such routes.

- [ ] **Step 3: Implement `preview_open` and the intent text**

In `src/journal/web/views.py`, extend `_intent_text` to handle `open` before the `add_volume` fallthrough:

```python
    if kind == "open":
        # No position_id to name — this command CREATES one. The sentence says
        # the size, the side, and the stop, because those three are the whole
        # decision.
        direction = pos["direction"].upper()
        return (
            f"BUKA {direction} {volume} lot {symbol} di harga pasar, "
            f"SL {_level_word(sl)}, TP {_level_word(tp)}"
        )
```

...and add:

```python
def preview_open(
    conn: sqlite3.Connection, login: int, *,
    symbol: str, entry: float | None, sl: float | None, tp: float | None,
    risk_mode: str, risk_value: float | None,
) -> dict:
    """The CONFIRM-step data for an open. Writes NOTHING.

    Sizes the order server-side and runs `build_request` — which validates — so
    an order that would be refused is refused HERE. The client never sends a
    volume and never needs to: the same `size_order` call runs again at enqueue,
    from the same inputs.
    """
    sizing = size_order(conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
                        risk_mode=risk_mode, risk_value=risk_value)
    if sizing["error"] is not None:
        raise CommandError(sizing["error"])

    pos, spec = execute.load_open_context(conn, login, symbol, sizing["direction"], entry)
    build_request("open", pos, spec, sl=sl, tp=tp, volume=sizing["volume"],
                  balance=execute.account_balance(conn, login))  # validates; may raise
    return {
        "intent": _intent_text("open", pos, sl=sl, tp=tp, volume=sizing["volume"]),
        "position_id": None,
        "kind": "open",
        "symbol": symbol,
        "fields": {"sl": sl, "tp": tp, "volume": sizing["volume"]},
        "sizing": sizing,
    }
```

`_intent_text` reads `pos["symbol"]` and `pos["position_id"]`, both of which the synthetic dict carries, so it needs no other change. Import `build_request` in `views.py` if it is not already imported.

- [ ] **Step 4: Add the routes and the prefs accessors**

In `src/journal/store/prefs_store.py`:

```python
RISK_KEY = "risk_sizing"


def get_risk_prefs(conn: sqlite3.Connection) -> Any | None:
    """Risk-sizing panel prefs (mode + value), or None if never saved."""
    raw = get_pref(conn, RISK_KEY)
    return None if raw is None else json.loads(raw)


def set_risk_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    """Upsert the risk-sizing prefs blob. Returns the updated_ms stamp."""
    return set_pref(conn, RISK_KEY, json.dumps(prefs), now_ms())
```

In `src/journal/web/app.py`, immediately after the `/api/size` route (and still before the `{position_id}` routes):

```python
    @app.post("/api/live/open/preview")
    def api_open_preview(
        symbol: str = Body(...),
        entry: float | None = Body(None),
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        risk_mode: str = Body("pct"),
        risk_value: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            login = views.account_header(conn)["login"]
            preview = views.preview_open(
                conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
                risk_mode=risk_mode, risk_value=risk_value,
            )
        except (RuntimeError, CommandError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(preview))

    @app.post("/api/live/open")
    def api_open(
        symbol: str = Body(...),
        entry: float | None = Body(None),
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        risk_mode: str = Body("pct"),
        risk_value: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """Enqueue ONE pending open. The volume is derived here, from the same
        `size_order` the preview used — a lot computed in the browser is never
        accepted, and the second derivation is what makes a stale preview
        harmless."""
        try:
            login = views.account_header(conn)["login"]
            sizing = views.size_order(
                conn, login, symbol=symbol, entry=entry, sl=sl, tp=tp,
                risk_mode=risk_mode, risk_value=risk_value,
            )
            if sizing["error"] is not None:
                raise CommandError(sizing["error"])
            cmd_id = enqueue_open(
                conn, login, symbol=symbol, direction=sizing["direction"],
                sl=sl, tp=tp, volume=sizing["volume"], price_ref=entry,
            )
        except (RuntimeError, CommandError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "command_id": cmd_id})

    @app.get("/api/risk-prefs")
    def api_get_risk_prefs(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse({"prefs": prefs_store.get_risk_prefs(conn)})

    @app.put("/api/risk-prefs")
    def api_put_risk_prefs(
        prefs=Body(...), conn: sqlite3.Connection = Depends(get_conn),
    ):
        ts = prefs_store.set_risk_prefs(conn, prefs)
        return JSONResponse({"ok": True, "updated_ms": ts})
```

Import `enqueue_open` alongside the existing `enqueue` import in `app.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web.py -v`
Expected: PASS

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS. The backend is now complete; the rest of the plan is frontend.

- [ ] **Step 7: Commit**

```bash
git add src/journal/web/views.py src/journal/web/app.py src/journal/store/prefs_store.py tests/test_web.py
git commit -m "feat(web): live open preview/enqueue endpoints and risk-sizing prefs"
```

---

### Task 9: Frontend sizing hook

**Files:**
- Create: `frontend/src/hooks/useRiskSizing.ts`
- Create: `frontend/src/hooks/useRiskSizing.test.ts`
- Modify: `frontend/src/lib/types.ts`

**Interfaces:**
- Consumes: `POST /api/size`, `GET/PUT /api/risk-prefs` from Tasks 7 and 8; the existing `postJson` / `useApi` helpers in `frontend/src/lib/api.ts`.
- Produces:
  ```ts
  export interface SizeResult {
    volume: number | null; risk_usc: number | null; risk_pct: number | null;
    distance: number | null; rr: number | null;
    direction: "buy" | "sell" | null; error: string | null;
  }
  export interface RiskPrefs { mode: "pct" | "usc"; value: number }
  export function useRiskSizing(input: {
    symbol: string; entry: number | null; sl: number | null; tp: number | null;
  }): {
    prefs: RiskPrefs; setPrefs: (p: RiskPrefs) => void;
    result: SizeResult | null; loading: boolean;
  }
  ```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/useRiskSizing.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRiskSizing } from "./useRiskSizing";

const size = {
  volume: 0.1, risk_usc: 50, risk_pct: 0.05, distance: 5, rr: 2,
  direction: "buy" as const, error: null,
};

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => ({
    ok: true, status: 200, json: async () => handler(url, init),
  })) as unknown as typeof fetch;
}

describe("useRiskSizing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockFetch((url) => (url.includes("risk-prefs") ? { prefs: null } : size));
  });

  it("does not call /api/size until an SL exists", async () => {
    renderHook(() => useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: null, tp: null }));
    await act(async () => { vi.advanceTimersByTime(500); });
    const calls = (globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls;
    expect(calls.some((c) => String(c[0]).includes("/api/size"))).toBe(false);
  });

  it("debounces: three rapid SL changes produce one request", async () => {
    const { rerender } = renderHook(
      (p: { sl: number }) => useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: p.sl, tp: null }),
      { initialProps: { sl: 4030 } },
    );
    rerender({ sl: 4031 });
    rerender({ sl: 4032 });
    await act(async () => { vi.advanceTimersByTime(500); });
    const calls = (globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls
      .filter((c) => String(c[0]).includes("/api/size"));
    expect(calls.length).toBe(1);
  });

  it("exposes the server result verbatim, including a refusal", async () => {
    mockFetch((url) =>
      url.includes("risk-prefs")
        ? { prefs: null }
        : { ...size, volume: null, error: "Risiko 60.00 melebihi batas keras 5%" });
    const { result } = renderHook(() =>
      useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: 4030, tp: null }));
    await act(async () => { vi.advanceTimersByTime(500); });
    await waitFor(() => expect(result.current.result?.error).toContain("5%"));
    expect(result.current.result?.volume).toBeNull();
  });

  it("loads saved prefs and persists a change", async () => {
    const put = vi.fn();
    mockFetch((url, init) => {
      if (url.includes("risk-prefs")) {
        if (init?.method === "PUT") { put(JSON.parse(String(init.body))); return { ok: true }; }
        return { prefs: { mode: "usc", value: 2500 } };
      }
      return size;
    });
    const { result } = renderHook(() =>
      useRiskSizing({ symbol: "XAUUSDc", entry: 4035, sl: 4030, tp: null }));
    await waitFor(() => expect(result.current.prefs.mode).toBe("usc"));
    expect(result.current.prefs.value).toBe(2500);

    act(() => { result.current.setPrefs({ mode: "pct", value: 1 }); });
    await act(async () => { vi.advanceTimersByTime(500); });
    await waitFor(() => expect(put).toHaveBeenCalledWith({ mode: "pct", value: 1 }));
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npm test -- useRiskSizing`
Expected: FAIL — cannot resolve `./useRiskSizing`.

- [ ] **Step 3: Add the types**

Append to `frontend/src/lib/types.ts`:

```ts
// Server-derived sizing. `error` non-null always means `volume` is null: the
// server never returns a number the confirm step would then refuse.
export interface SizeResult {
  volume: number | null;
  risk_usc: number | null;      // USC (account currency), never "$"
  risk_pct: number | null;      // of accounts.balance
  distance: number | null;      // |entry - sl| in price units
  rr: number | null;            // |tp - entry| / distance; null when no TP
  direction: "buy" | "sell" | null;
  error: string | null;
}

export interface RiskPrefs {
  mode: "pct" | "usc";
  value: number;
}

// A not-yet-existing order drawn on the chart. `entry` is the live/cursor price
// the human is sizing against; sl/tp are null until dragged (rule 4: null is
// "not set", and 0 would be a price).
export interface PlannedOrder {
  entry: number;
  sl: number | null;
  tp: number | null;
  direction: "buy" | "sell" | null;
}
```

- [ ] **Step 4: Implement the hook**

Create `frontend/src/hooks/useRiskSizing.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { postJson } from "../lib/api";
import type { RiskPrefs, SizeResult } from "../lib/types";

const DEBOUNCE_MS = 150;
const DEFAULT_PREFS: RiskPrefs = { mode: "pct", value: 1 };

// Sizing lives on the server and ONLY on the server. Mirroring the formula here
// would give instant feedback and a second source of truth that drifts from the
// first — and the number it produces is a lot size on a real account.
export function useRiskSizing(input: {
  symbol: string;
  entry: number | null;
  sl: number | null;
  tp: number | null;
}) {
  const [prefs, setPrefsState] = useState<RiskPrefs>(DEFAULT_PREFS);
  const [result, setResult] = useState<SizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    let live = true;
    fetch("/api/risk-prefs")
      .then((r) => r.json())
      .then((d) => { if (live && d?.prefs) setPrefsState(d.prefs as RiskPrefs); })
      .catch(() => { /* prefs are a convenience; defaults are fine */ });
    return () => { live = false; };
  }, []);

  const setPrefs = useCallback((p: RiskPrefs) => {
    setPrefsState(p);
    fetch("/api/risk-prefs", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }).catch(() => { /* a failed save must not block sizing */ });
  }, []);

  const { symbol, entry, sl, tp } = input;

  useEffect(() => {
    // No stop, no risk, no size. Not an error state — nothing has been asked yet.
    if (entry === null || sl === null) { setResult(null); return; }
    const mine = ++seq.current;
    setLoading(true);
    const t = setTimeout(async () => {
      const r = await postJson<SizeResult>("/api/size", {
        symbol, entry, sl, tp,
        risk_mode: prefs.mode, risk_value: prefs.value,
      });
      // A drag fires many of these; only the newest answer may win, or the
      // panel shows the lot for a price the line has already left.
      if (mine !== seq.current) return;
      setLoading(false);
      setResult(r.ok ? (r.data ?? null) : {
        volume: null, risk_usc: null, risk_pct: null, distance: null,
        rr: null, direction: null, error: r.error ?? "gagal menghitung ukuran",
      });
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [symbol, entry, sl, tp, prefs.mode, prefs.value]);

  return { prefs, setPrefs, result, loading };
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm test -- useRiskSizing`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useRiskSizing.ts frontend/src/hooks/useRiskSizing.test.ts frontend/src/lib/types.ts
git commit -m "feat(frontend): useRiskSizing hook — debounced server-side sizing"
```

---

### Task 10: The panel

**Files:**
- Create: `frontend/src/components/RiskSizePanel.tsx`
- Create: `frontend/src/components/RiskSizePanel.test.tsx`

**Interfaces:**
- Consumes: `SizeResult`, `RiskPrefs` from Task 9.
- Produces:
  ```ts
  export default function RiskSizePanel(props: {
    disabled: boolean;
    currency: string;
    prefs: RiskPrefs;
    onPrefsChange: (p: RiskPrefs) => void;
    entry: number | null;
    sl: number | null;
    tp: number | null;
    onSlChange: (v: number | null) => void;
    onTpChange: (v: number | null) => void;
    result: SizeResult | null;
    loading: boolean;
    onSubmit: (o: { direction: "buy" | "sell"; volume: number }) => void;
  }): JSX.Element
  ```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/RiskSizePanel.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RiskSizePanel from "./RiskSizePanel";
import type { RiskPrefs, SizeResult } from "../lib/types";

const PREFS: RiskPrefs = { mode: "pct", value: 1 };
const OK: SizeResult = {
  volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: 2,
  direction: "buy", error: null,
};

function setup(over: Partial<React.ComponentProps<typeof RiskSizePanel>> = {}) {
  const onSubmit = vi.fn();
  const onPrefsChange = vi.fn();
  const onSlChange = vi.fn();
  render(
    <RiskSizePanel
      disabled={false} currency="USC" prefs={PREFS} onPrefsChange={onPrefsChange}
      entry={4035} sl={4030} tp={4045}
      onSlChange={onSlChange} onTpChange={vi.fn()}
      result={OK} loading={false} onSubmit={onSubmit}
      {...over}
    />,
  );
  return { onSubmit, onPrefsChange, onSlChange };
}

describe("RiskSizePanel", () => {
  it("labels the action button from the derived direction", () => {
    setup();
    expect(screen.getByRole("button", { name: /buy/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /sell/i })).toBeNull();
  });

  it("labels it Sell when the stop sits above the price", () => {
    setup({ sl: 4040, result: { ...OK, direction: "sell" } });
    expect(screen.getByRole("button", { name: /sell/i })).toBeTruthy();
  });

  it("shows the lot, the realised risk in the account currency, and R:R", () => {
    setup();
    expect(screen.getByTestId("lot").textContent).toContain("0.13");
    const risk = screen.getByTestId("risk").textContent ?? "";
    expect(risk).toContain("65");
    expect(risk).toContain("USC");
    expect(risk).not.toContain("$");   // USC is not dollars (Trap 14)
    expect(screen.getByTestId("rr").textContent).toContain("2.00");
  });

  it("submits the server's volume and direction, never its own", () => {
    const { onSubmit } = setup();
    fireEvent.click(screen.getByRole("button", { name: /buy/i }));
    expect(onSubmit).toHaveBeenCalledWith({ direction: "buy", volume: 0.13 });
  });

  it("disables the action and shows the server's reason on a refusal", () => {
    setup({ result: { ...OK, volume: null, direction: null,
                      error: "Risiko 60.00 melebihi batas keras 5%" } });
    const btn = screen.getByRole("button", { name: /buka posisi/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("size-error").textContent).toContain("5%");
  });

  it("disables the action while no stop has been placed", () => {
    setup({ sl: null, result: null });
    const btn = screen.getByRole("button", { name: /buka posisi/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("size-hint").textContent).toMatch(/SL/i);
  });

  it("switching the risk mode reports the new prefs upward", () => {
    const { onPrefsChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "USC" }));
    expect(onPrefsChange).toHaveBeenCalledWith({ mode: "usc", value: 1 });
  });

  it("typing an SL reports null when the field is cleared", () => {
    const { onSlChange } = setup();
    fireEvent.change(screen.getByLabelText(/SL/i), { target: { value: "" } });
    expect(onSlChange).toHaveBeenCalledWith(null);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- RiskSizePanel`
Expected: FAIL — cannot resolve `./RiskSizePanel`.

- [ ] **Step 3: Implement**

Create `frontend/src/components/RiskSizePanel.tsx`:

```tsx
import type { RiskPrefs, SizeResult } from "../lib/types";

// Risk-first order panel. The human sets a stop and a budget; the SERVER
// derives the size. Nothing here computes a lot, and nothing here suggests a
// level, a side, or a moment (rule 9) — the side shown is read off the stop the
// human already placed.
export default function RiskSizePanel(props: {
  disabled: boolean;
  currency: string;
  prefs: RiskPrefs;
  onPrefsChange: (p: RiskPrefs) => void;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  onSlChange: (v: number | null) => void;
  onTpChange: (v: number | null) => void;
  result: SizeResult | null;
  loading: boolean;
  onSubmit: (o: { direction: "buy" | "sell"; volume: number }) => void;
}) {
  const r = props.result;
  const direction = r?.direction ?? null;
  const ready = !props.disabled && !!r && r.error === null
    && r.volume !== null && direction !== null;

  // "" -> null, not 0: an empty field is "not set", and 0 is a price (rule 4).
  const num = (s: string): number | null => (s.trim() === "" ? null : Number(s));

  const label = direction === "buy" ? "Buy" : direction === "sell" ? "Sell" : "Buka posisi";
  const tone = direction === "buy" ? "text-pos" : direction === "sell" ? "text-neg" : "";

  return (
    <div className="glass p-3 space-y-2 text-xs">
      <div className="font-semibold">Ukuran otomatis</div>

      <div className="flex gap-1">
        <button
          className={`glass flex-1 py-1 ${props.prefs.mode === "pct" ? "font-semibold" : "opacity-60"}`}
          onClick={() => props.onPrefsChange({ ...props.prefs, mode: "pct" })}
        >%</button>
        <button
          className={`glass flex-1 py-1 ${props.prefs.mode === "usc" ? "font-semibold" : "opacity-60"}`}
          onClick={() => props.onPrefsChange({ ...props.prefs, mode: "usc" })}
        >USC</button>
      </div>

      <label className="block">
        Risiko ({props.prefs.mode === "pct" ? "% balance" : props.currency})
        <input
          type="number" step={props.prefs.mode === "pct" ? "0.1" : "1"} min="0"
          className="glass mt-1 w-full px-2 py-1"
          value={props.prefs.value}
          onChange={(e) => props.onPrefsChange({ ...props.prefs, value: Number(e.target.value) })}
        />
      </label>

      <label className="block">
        SL (tarik garis di chart, atau ketik)
        <input
          type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
          value={props.sl ?? ""}
          onChange={(e) => props.onSlChange(num(e.target.value))}
        />
      </label>

      <label className="block">
        TP (kosong = tidak ada)
        <input
          type="number" step="0.001" className="glass mt-1 w-full px-2 py-1"
          value={props.tp ?? ""}
          onChange={(e) => props.onTpChange(num(e.target.value))}
        />
      </label>

      <div className="space-y-1 border-t border-white/10 pt-2">
        <Row label="Harga" value={props.entry === null ? "—" : props.entry.toFixed(3)} />
        <Row label="Jarak SL" value={r?.distance == null ? "—" : r.distance.toFixed(3)} />
        <Row label="Lot" value={r?.volume == null ? "—" : r.volume.toFixed(2)} testId="lot" />
        <Row
          label="Risiko"
          testId="risk"
          value={r?.risk_usc == null
            ? "—"
            : `${r.risk_usc.toFixed(2)} ${props.currency}` +
              (r.risk_pct == null ? "" : ` (${r.risk_pct.toFixed(2)}%)`)}
        />
        <Row label="R:R" value={r?.rr == null ? "—" : r.rr.toFixed(2)} testId="rr" />
      </div>

      {r?.error && (
        <div data-testid="size-error" className="text-neg">{r.error}</div>
      )}
      {!r && !props.loading && (
        <div data-testid="size-hint" className="text-muted">
          Tarik garis SL dari harga sekarang untuk mulai menghitung lot.
        </div>
      )}

      <button
        className={`glass w-full py-1 ${tone}`}
        disabled={!ready}
        onClick={() => {
          if (r?.volume != null && direction !== null) {
            props.onSubmit({ direction, volume: r.volume });
          }
        }}
      >
        {label}
      </button>

      {/* MARKET execution: the broker fills at its own price, so realised risk
          can differ from the target by the entry slippage. Say it once, here,
          rather than pretending the number is exact. */}
      <div className="text-muted">
        Eksekusi pasar — harga isi bisa bergeser dari harga acuan.
      </div>
    </div>
  );
}

function Row(props: { label: string; value: string; testId?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{props.label}</span>
      <span data-testid={props.testId}>{props.value}</span>
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- RiskSizePanel`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RiskSizePanel.tsx frontend/src/components/RiskSizePanel.test.tsx
git commit -m "feat(frontend): RiskSizePanel — risk-first order panel"
```

---

### Task 11: Draggable planned-order lines

**Files:**
- Modify: `frontend/src/lib/sltpDrag.ts`
- Modify: `frontend/src/components/CandleChart.tsx:523-570` (the price-line effect) and the pointer handlers around `:276-340`
- Test: `frontend/src/components/CandleChart.test.tsx`

**Interfaces:**
- Consumes: `PlannedOrder` from Task 9.
- Produces:
  - `sltpDrag.PLANNED_ID = -1` — the sentinel `positionId` for a not-yet-existing order.
  - `CandleChart` prop `plannedOrder?: PlannedOrder | null`. Its lines are draggable through the existing `onSlTpChange` callback, which receives `PLANNED_ID` as the position id.

**Deviation from the spec, deliberate:** the spec described a separate
`onPlannedChange` callback. One prop and a sentinel id is a smaller change to a
26KB component that already routes every drag through `onSlTpChange` — the
caller branches on the id it already receives. Same behaviour, less surface.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/CandleChart.test.tsx`, following its existing lightweight-charts mocking style:

```tsx
import { PLANNED_ID } from "../lib/sltpDrag";

describe("planned-order lines", () => {
  it("draws entry, SL and TP lines for a planned order", () => {
    const { priceLines } = renderChart({
      plannedOrder: { entry: 4035, sl: 4030, tp: 4045, direction: "buy" },
    });
    const prices = priceLines.map((l) => l.price).sort((a, b) => a - b);
    expect(prices).toEqual([4030, 4035, 4045]);
  });

  it("omits an unset SL and TP rather than drawing them at 0", () => {
    const { priceLines } = renderChart({
      plannedOrder: { entry: 4035, sl: null, tp: null, direction: null },
    });
    expect(priceLines.map((l) => l.price)).toEqual([4035]);
  });

  it("reports a planned-line drag under the PLANNED_ID sentinel", () => {
    const onSlTpChange = vi.fn();
    const { dragLineTo } = renderChart({
      plannedOrder: { entry: 4035, sl: 4030, tp: null, direction: "buy" },
      onSlTpChange,
    });
    dragLineTo(4030, 4028);
    expect(onSlTpChange).toHaveBeenCalledWith(PLANNED_ID, { sl: 4028 });
  });

  it("a drag from the planned ENTRY line becomes the SL while no side is known", () => {
    const onSlTpChange = vi.fn();
    const { dragLineTo } = renderChart({
      plannedOrder: { entry: 4035, sl: null, tp: null, direction: null },
      onSlTpChange,
    });
    dragLineTo(4035, 4030);
    expect(onSlTpChange).toHaveBeenCalledWith(PLANNED_ID, { sl: 4030 });
  });

  it("planned lines coexist with real position lines without colliding", () => {
    const { priceLines } = renderChart({
      plannedOrder: { entry: 4035, sl: 4030, tp: null, direction: "buy" },
      draggablePositions: [
        { id: 1, direction: "buy", entry_price: 4000, sl: 3990, tp: 0 },
      ],
    });
    expect(priceLines.map((l) => l.price).sort((a, b) => a - b))
      .toEqual([3990, 4000, 4030, 4035]);
  });
});
```

`renderChart` is the existing helper in `CandleChart.test.tsx`; extend it to accept and forward the `plannedOrder` prop and to expose `priceLines` and a `dragLineTo(fromPrice, toPrice)` helper if it does not already.

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- CandleChart`
Expected: FAIL — `PLANNED_ID` is not exported, and no planned lines are drawn.

- [ ] **Step 3: Add the sentinel**

Append to `frontend/src/lib/sltpDrag.ts`:

```ts
// The `positionId` used for an order that does not exist yet. Real position ids
// are MT5 tickets — always positive — so a negative sentinel can never collide
// with one, and the whole existing hit-test/drag/ghost machinery works on a
// planned line with no branching inside it.
export const PLANNED_ID = -1;
```

- [ ] **Step 4: Draw the planned lines**

In `frontend/src/components/CandleChart.tsx`, add the prop to the props type:

```ts
  plannedOrder?: PlannedOrder | null;
```

...import `PlannedOrder` from `../lib/types` and `PLANNED_ID` from `../lib/sltpDrag`, and in the price-line effect insert the planned block immediately after `linesMeta.current = [];` and the `addLine` definition — **before** the `if (props.draggablePositions !== undefined)` branch, so planned lines survive every path:

```tsx
    // A planned order draws on top of whatever else the chart is showing: it is
    // not a position yet, so it belongs to none of the branches below, and each
    // of those returns early. `direction` is null until the human's stop picks a
    // side; an entry-line drag then resolves to "sl" by default, which is
    // exactly the gesture that decides it.
    if (props.plannedOrder) {
      const p = props.plannedOrder;
      const dir = p.direction ?? "buy";
      addLine(PLANNED_ID, "entry", p.entry, LINE_COLORS.entry, "harga", dir, p.entry);
      addLine(PLANNED_ID, "sl", p.sl, LINE_COLORS.sl, "SL rencana", dir, p.entry);
      addLine(PLANNED_ID, "tp", p.tp, LINE_COLORS.tp, "TP rencana", dir, p.entry);
    }
```

Add `props.plannedOrder` to that effect's dependency array.

`addLine` already skips a `null` price and a price within `1e-9` of zero, so an
unset SL or TP draws nothing without a further guard.

- [ ] **Step 5: Route the entry-line drag for a directionless plan**

In the pointer-move handler where the drag kind is resolved (around
`CandleChart.tsx:310-317`), the `entry` case calls `resolveDragTarget`, which
needs a direction. For a planned order with no side chosen yet, the drag must
always mean "SL":

```tsx
        const kind = drag.kind === "entry"
          ? (drag.positionId === PLANNED_ID && cbs.current.plannedDirection === null
              ? "sl"
              : resolveDragTarget(
                  { id: drag.positionId, direction: drag.direction,
                    entry_price: drag.entryPrice, sl: 0, tp: 0 },
                  pt.price,
                ))
          : (drag.kind as "sl" | "tp");
```

Add `plannedDirection: props.plannedOrder?.direction ?? null` to the `cbs` ref
object that the component already keeps in sync with props, next to
`lastBarMs` and `candles`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm test -- CandleChart`
Expected: PASS, including the pre-existing SL/TP drag tests — nothing in the real-position path changed.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/sltpDrag.ts frontend/src/components/CandleChart.tsx frontend/src/components/CandleChart.test.tsx
git commit -m "feat(chart): draggable planned-order lines under a PLANNED_ID sentinel"
```

---

### Task 12: Wire the panel into the chart page

**Files:**
- Modify: `frontend/src/pages/Chart.tsx:278-411`
- Modify: `frontend/src/hooks/useLiveCommand.ts`
- Test: `frontend/src/pages/Chart.test.tsx`

**Interfaces:**
- Consumes: Tasks 9, 10, 11.
- Produces: `RiskSizePanel` mounted in the `<aside>` in both replay and live mode; `useLiveCommand.request(position_id: number | null, action, body)` accepting `null` for an open.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/pages/Chart.test.tsx`:

```tsx
describe("risk sizing panel", () => {
  it("is mounted in replay mode", async () => {
    renderChartPage({ replayOpen: true });
    expect(await screen.findByText(/Ukuran otomatis/i)).toBeTruthy();
  });

  it("is mounted in live (non-replay) mode", async () => {
    renderChartPage({ replayOpen: false });
    expect(await screen.findByText(/Ukuran otomatis/i)).toBeTruthy();
  });

  it("a replay submit sends the server-derived volume to the replay open API", async () => {
    const { openPosition } = renderChartPage({ replayOpen: true, sizeResult: {
      volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: null,
      direction: "buy", error: null,
    } });
    fireEvent.click(await screen.findByRole("button", { name: /buy/i }));
    await waitFor(() => expect(openPosition).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ direction: "buy", volume: 0.13 }),
    ));
  });

  it("a live submit goes through the preview/confirm flow, not straight to the order", async () => {
    const { postJson } = renderChartPage({ replayOpen: false, sizeResult: {
      volume: 0.13, risk_usc: 65, risk_pct: 0.065, distance: 5, rr: null,
      direction: "buy", error: null,
    } });
    fireEvent.click(await screen.findByRole("button", { name: /buy/i }));
    await waitFor(() => expect(postJson).toHaveBeenCalledWith(
      "/api/live/open/preview", expect.anything()));
    expect(postJson).not.toHaveBeenCalledWith("/api/live/open", expect.anything());
  });
});
```

Extend the file's existing `renderChartPage` helper to accept `replayOpen` and `sizeResult` and to expose the mocked `openPosition` / `postJson` spies, following whatever mocking the file already does for `replayApi` and `lib/api`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- Chart`
Expected: FAIL — "Ukuran otomatis" is not in the document.

- [ ] **Step 3: Let `useLiveCommand` carry an open**

In `frontend/src/hooks/useLiveCommand.ts`, change `request` and `confirm` to
accept a null position id:

```ts
  // An `open` has no position yet, so the URL is keyed on the action alone.
  // Everything else — preview writes nothing, confirm is the only write — is
  // unchanged, which is the point: an open gets exactly the same two-step
  // confirmation an SL/TP edit does.
  const urlFor = (position_id: number | null, action: ActionKind, suffix = "") =>
    position_id === null
      ? `/api/live/${action}${suffix}`
      : `/api/live/${position_id}/${action}${suffix}`;

  const request = async (
    position_id: number | null, action: ActionKind, body: CommandBody,
  ) => {
    setError(null);
    const r = await postJson<PreviewResult>(urlFor(position_id, action, "/preview"), body);
    if (!r.ok) { setToast(null); setError(r.error ?? "gagal"); setPreview(null); return; }
    setPending({ action, body, position_id });
    setPreview(r.data ?? null);
  };
```

...and in `confirm`, take the id from `pending` rather than from `preview`
(an open's `preview.position_id` is null by design):

```ts
    const r = await postJson<{ ok: boolean; command_id: number }>(
      urlFor(pending.position_id, pending.action), pending.body);
```

Widen the `pending` state type to `{ action: ActionKind; body: CommandBody; position_id: number | null }`, and add `"open"` to the `ActionKind` union in `frontend/src/lib/types.ts`.

- [ ] **Step 4: Wire the page**

In `frontend/src/pages/Chart.tsx`:

```tsx
import RiskSizePanel from "../components/RiskSizePanel";
import { useRiskSizing } from "../hooks/useRiskSizing";
import { PLANNED_ID } from "../lib/sltpDrag";
```

Add the planned-order state near the other chart state:

```tsx
  // The order being sized. `entry` is whatever price the chart is showing now:
  // the forming bar's close in live, the cursor bar's close in replay. Both are
  // already computed for the info panel — this reuses them rather than adding a
  // second notion of "current price".
  const [plannedSl, setPlannedSl] = useState<number | null>(null);
  const [plannedTp, setPlannedTp] = useState<number | null>(null);
  const plannedEntry = currentClose ?? null;
  const sizing = useRiskSizing({
    symbol, entry: plannedEntry, sl: plannedSl, tp: plannedTp,
  });
  const plannedOrder = plannedEntry === null ? null : {
    entry: plannedEntry, sl: plannedSl, tp: plannedTp,
    direction: sizing.result?.direction ?? null,
  };
```

Route planned drags inside the existing `handleSlTpChange`, at the top:

```tsx
    // A planned order is not a position: its "commit" is local state, and the
    // real command only leaves on the button. Instant, no dialog — the human is
    // still choosing.
    if (positionId === PLANNED_ID) {
      if (change.sl !== undefined) setPlannedSl(change.sl === 0 ? null : change.sl);
      if (change.tp !== undefined) setPlannedTp(change.tp === 0 ? null : change.tp);
      return;
    }
```

Pass the prop to the chart:

```tsx
              plannedOrder={plannedOrder}
```

And mount the panel in the `<aside>`. In the replay branch, replace
`<ReplayOrderTicket ... />` with:

```tsx
              <RiskSizePanel
                disabled={!replay.session || atEnd}
                currency={currency}
                prefs={sizing.prefs}
                onPrefsChange={sizing.setPrefs}
                entry={plannedEntry}
                sl={plannedSl}
                tp={plannedTp}
                onSlChange={setPlannedSl}
                onTpChange={setPlannedTp}
                result={sizing.result}
                loading={sizing.loading}
                onSubmit={(o) => replay.open({
                  direction: o.direction, volume: o.volume,
                  sl: plannedSl ?? 0, tp: plannedTp ?? 0,
                })}
              />
```

In the non-replay branch, add the same panel above `<ChartInfoPanel …>`, with:

```tsx
              <RiskSizePanel
                disabled={!live || live.live.empty === undefined}
                currency={currency}
                prefs={sizing.prefs}
                onPrefsChange={sizing.setPrefs}
                entry={plannedEntry}
                sl={plannedSl}
                tp={plannedTp}
                onSlChange={setPlannedSl}
                onTpChange={setPlannedTp}
                result={sizing.result}
                loading={sizing.loading}
                onSubmit={() => liveCmd.request(null, "open", {
                  symbol, entry: plannedEntry, sl: plannedSl, tp: plannedTp,
                  risk_mode: sizing.prefs.mode, risk_value: sizing.prefs.value,
                })}
              />
```

The live submit deliberately drops the panel's `volume`: the server derives it
again from the same inputs, and sending a number that would be ignored invites
the belief that it was used.

`ReplayOrderTicket` is now unreferenced. Delete
`frontend/src/components/ReplayOrderTicket.tsx` and its import — the panel
replaces it, and a dead second order form is exactly the thing someone
maintains by accident at 3am.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test`
Expected: PASS, the whole frontend suite.

- [ ] **Step 6: Typecheck and build**

Run: `npx tsc --noEmit` then `npm run build`
Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Chart.tsx frontend/src/pages/Chart.test.tsx frontend/src/hooks/useLiveCommand.ts frontend/src/lib/types.ts
git rm frontend/src/components/ReplayOrderTicket.tsx
git commit -m "feat(chart): mount RiskSizePanel in replay and live, retire ReplayOrderTicket"
```

---

### Task 13: Verification and documentation

**Files:**
- Modify: `CLAUDE.md` (the "Currently on" milestone line)
- Modify: `docs/HANDOFF.md`

- [ ] **Step 1: Run every gate**

```bash
uv run pytest
cd frontend && npm test && npx tsc --noEmit && npm run build && cd ..
uv run journal rebuild
graphify update .
```

Expected: pytest all-pass, vitest all-pass, `tsc` exit 0, `build` exit 0,
`rebuild` succeeds. Paste the actual output — a task is done when the output is
pasted, not when the code looks right (CLAUDE.md "Definition of done").

- [ ] **Step 2: Record what a human still has to check**

Nothing in this plan proves the order reaches the broker: the bridge is not
running in CI and `FakeOpenClient` is a fake. Add to `docs/HANDOFF.md` under a
new heading:

```markdown
## PENDING HUMAN — risk-based auto lot sizing (2026-08-04)

Automated gates are green; none of them touched a real broker. Before trusting
this with size:

1. Start the MT5 bridge and `uv run journal live`. Confirm `journal doctor`
   reports the account and a recent tick.
2. On `/chart`, drag the SL line below the current price. Confirm the panel
   shows a lot, a risk in USC, and a Buy label — and that dragging above the
   price flips it to Sell.
3. Set the risk to the smallest workable value and open ONE position on the
   smallest symbol. Confirm: the ConfirmModal shows the intent sentence; the
   command appears in the audit log; `journal live` sends it; MT5 shows the
   position WITH the SL attached from the first tick.
4. Confirm the realised risk matches the panel's figure within the entry
   slippage, using `risk_amount` on the resulting trade after `journal sync`.
5. Try to open with an SL far enough away to exceed 5% of balance. Confirm the
   panel refuses and no command row is written.
```

- [ ] **Step 3: Update the milestone line**

In `CLAUDE.md`, extend the "Currently on" block with:

```
Risk-based auto lot sizing + live position open (command kind `open`,
`RiskSizePanel`, migration 009) MERGED <date>; in-browser pass with the MT5
bridge running still pending a human run — see docs/HANDOFF.md.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/HANDOFF.md graphify-out/
git commit -m "docs: record the pending human verification for auto lot sizing"
```

- [ ] **Step 5: Request review**

Use the `superpowers:requesting-code-review` skill on the whole branch, then
`superpowers:receiving-code-review`, then a fix wave, then re-review until
clean. Only after that, `superpowers:finishing-a-development-branch`.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `volume_for_risk` / `floor_to_step` | 1 |
| `direction_for_sl` (spec's "direction derived from SL side") | 1, used in 7 |
| Migration 009, `SCHEMA_VERSION` 9 | 2 |
| `open` in `KINDS`/`_OPENING`, synthetic position, `MAX_RISK_PCT`, SL mandatory, NULL balance refuses | 3 |
| `build_request` for `open` | 4 |
| `enqueue_open`, `load_open_context`, `account_balance` | 5 |
| Executor branch, fresh tick, `price_ref` fallback, volume not recomputed | 6 |
| `POST /api/size` | 7 |
| `/api/live/open/preview`, `/api/live/open`, route ordering, `app_prefs` key | 8 |
| `useRiskSizing`, no TS-side arithmetic, 150 ms debounce | 9 |
| `RiskSizePanel`, %/USC toggle, read-outs, direction-labelled button, USC never `$` | 10 |
| `plannedOrder` lines, `PLANNED_ID`, reuse of the existing drag machine | 11 |
| Panel in both modes, `useLiveCommand` with a null position id, replay path unchanged server-side | 12 |
| Error-handling table (every refusal) | 3, 6, 7 tests |
| Testing section | 1–12, gathered in 13 |

**Deviations, both deliberate and flagged in place:**
1. Task 11 uses one `plannedOrder` prop plus a sentinel id instead of the
   spec's separate `onPlannedChange` callback — smaller change to a large
   component, identical behaviour.
2. Task 12 deletes `ReplayOrderTicket.tsx`, which the spec did not mention. The
   panel fully replaces it; leaving a second order form in the tree is a
   maintenance trap.

**Type consistency:** `SizeResult` is produced by `views.size_order` (Task 7),
typed in `lib/types.ts` (Task 9), consumed by `useRiskSizing` (9) and
`RiskSizePanel` (10) with the same seven keys throughout. `PlannedOrder` is
defined in Task 9 and consumed in 11 and 12. `PLANNED_ID` is exported in Task 11
and imported in Task 12. `validate`'s new `balance` keyword (Task 3) is passed by
`build_request` (4), `enqueue_open` (5), the executor (6), and `size_order` (7).
