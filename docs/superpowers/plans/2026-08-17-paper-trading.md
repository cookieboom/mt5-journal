# Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A virtual trading account on `/chart` — named accounts with a balance, leverage and stop-out level the human sets — filled against the live tick feed with the real spread, evaluated per tick by `journal live`.

**Architecture:** Two new tables (`paper_accounts`, `paper_positions`) plus a latest-tick cache (`live_quotes`). All fill, money, margin and stop-out logic lives in one pure module, `domain/paper_eval.py`, with no DB and no bridge. The `journal live` daemon owns evaluation between clicks (triggers, SL/TP, stop-out); the web executes market actions instantly against the freshest stored quote, refusing a stale one. Paper data never enters `deals_raw` or `trades`.

**Tech Stack:** Python 3.12 + SQLite (WAL) + FastAPI backend; React + TypeScript + Vite + `lightweight-charts` frontend; `pytest` and `vitest`. No new dependencies (CLAUDE.md rule 8).

**Spec:** `docs/superpowers/specs/2026-08-17-paper-trading-design.md` — read it before Task 1. It records what was decided, and two architectures that were rejected with reasons.

## Global Constraints

- **Money is `USC`** (US cents) everywhere. Never print a bare number as `$`. R-multiple is unit-free and preferred in statistics.
- **All timestamps are epoch milliseconds, integer, UTC.** Never naive datetimes, never local time. This broker's `server_utc_offset_s = 0`.
- **`NULL` means unknown; `0` means "none set".** Every unknown propagates to `None` — never a coerced `0`. This is load-bearing for `sl`/`tp` and for every money figure.
- **Money and prices are `REAL`.** Compare with tolerance (`abs(a - b) < 1e-9`), never `==`.
- **Never `import MetaTrader5` outside `src/journal/adapter/`**, and no MT5 constants outside `adapter/live.py`. `domain/` must contain no bridge import and no magic enum value.
- **The web never touches the bridge.** Prices reach the web only through the DB.
- **`deals_raw` / `orders_raw` are append-only and are the broker's source of truth.** Paper writes nothing to them, and nothing to `trades`.
- **`journal rebuild` must keep succeeding** and must never touch `paper_*`. Say so in the schema comment.
- **Symbols are stored twice:** `symbol` verbatim (`XAUUSDc`), `symbol_base` normalised through `domain/symbols.to_base` (`XAUUSD`).
- **Tests before implementation** for everything in `domain/` (rule 7). Fixtures, never live MT5.
- **No new dependencies.** No `TestClient`: service functions are tested directly against a seeded DB, the discipline `tests/test_web.py` states.
- **Frontend:** colours only from `lib/theme.ts`, type sizes only from `lib/type.ts`. A pasted hex or a `text-[13px]` is a defect.
- **Definition of done per task:** the task's tests pass, `uv run pytest` is green, and the actual output is pasted. Not when the code "looks right".

## File Structure

**Created:**

| Path | Responsibility |
| --- | --- |
| `src/journal/store/migrations/013_paper.sql` | The three new tables and their indexes |
| `src/journal/domain/paper_eval.py` | Pure evaluator: money, margin, fills, SL/TP, stop-out. No DB, no bridge |
| `src/journal/domain/sim_stats.py` | The shared simulated-account summary, moved out of `training_store` |
| `src/journal/store/paper_store.py` | Pure DB access for `paper_accounts` / `paper_positions` |
| `src/journal/web/paper.py` | Impure glue: reads specs and quotes, calls the evaluator, persists, builds views |
| `tests/test_paper_eval.py` | The evaluator, fixture-based |
| `tests/test_paper_store.py` | Store CRUD and the partial-close split |
| `tests/test_paper_web.py` | Service functions against a seeded DB |
| `frontend/src/lib/paperApi.ts` | Typed fetch wrappers for `/api/paper/*` |
| `frontend/src/hooks/usePaperAccount.ts` | Polling hook for the selected account |
| `frontend/src/components/PaperAccountBar.tsx` | Balance / equity / margin level strip |
| `frontend/src/components/PaperPositions.tsx` | Open positions, pending orders, closed history |
| `frontend/src/components/PaperOrderPanel.tsx` | Order entry: direction, sizing, SL/TP, kind |
| `frontend/src/components/PaperAccountDialog.tsx` | Pick / create / archive an account |

**Modified:**

| Path | Change |
| --- | --- |
| `src/journal/store/schema.sql` | The same three tables, for a fresh DB |
| `src/journal/store/db.py:23` | `SCHEMA_VERSION = 12` → `13` |
| `src/journal/store/live_store.py` | `upsert_quote` / `read_quote` |
| `src/journal/store/training_store.py` | `_summary` moves to `domain/sim_stats.py`; import it |
| `src/journal/store/prefs_store.py` | `get_paper_prefs` / `set_paper_prefs` under key `paper` |
| `src/journal/domain/commands.py` | `_check_volume` → `check_volume`, `_check_level` → `check_level` |
| `src/journal/ingest/live.py` | The paper step inside `live_cycle`, after the beacon |
| `src/journal/web/app.py` | The `/api/paper/*` route declarations |
| `frontend/src/components/ChartToolbar.tsx` | The `REAL`/`PAPER` toggle |
| `frontend/src/pages/Chart.tsx` | Paper mode wiring, badge, accent border, side panel |
| `tests/test_live.py` | The paper step's four daemon guarantees |

---

## Phase 1 — schema, evaluator, store

### Task 1: Migration and schema

**Files:**
- Create: `src/journal/store/migrations/013_paper.sql`
- Modify: `src/journal/store/schema.sql`, `src/journal/store/db.py:23`
- Test: `tests/test_migrations.py` (exists; it compares a fresh DB against a migrated one and fails on any drift)

**Interfaces:**
- Consumes: nothing.
- Produces: tables `paper_accounts`, `paper_positions`, `live_quotes`; `SCHEMA_VERSION == 13`.

- [ ] **Step 1: Write the migration**

Create `src/journal/store/migrations/013_paper.sql`:

```sql
-- 013: paper trading — a virtual account with a balance the human sets.
-- Money is USC (accounts.currency), all *_msc are epoch ms server-UTC.
-- NOT derived from raw and NOT a chart cache: `journal rebuild` never touches
-- these tables, exactly like the training_* group. No fictional deal ever
-- reaches deals_raw (rule 2) — that is the whole reason this group exists.

CREATE TABLE IF NOT EXISTS paper_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    initial_balance REAL    NOT NULL,          -- USC
    balance         REAL    NOT NULL,          -- USC, REALIZED only
    leverage        INTEGER NOT NULL,          -- e.g. 500 for 1:500
    stopout_pct     REAL    NOT NULL,          -- margin level % that liquidates
    status          TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','archived')),
    created_at_msc  INTEGER NOT NULL,
    archived_at_msc INTEGER
);

-- One order is one position: this account is margin_mode = 2 (HEDGING), so a
-- pending order is just a row with status='pending'. A partial close SPLITS —
-- the closed slice becomes a new row with parent_id set and the parent's
-- volume shrinks — so every closed row is a complete trade record and no
-- statistic needs a special case.
-- sl/tp: 0 = none set (rule 4). sl_initial is written ONCE at fill so R stays
-- honest after the stop is moved, the same discipline `trades` uses.
CREATE TABLE IF NOT EXISTS paper_positions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL
                       REFERENCES paper_accounts(id) ON DELETE CASCADE,
    symbol         TEXT    NOT NULL,           -- verbatim MT5 symbol (rule 11)
    symbol_base    TEXT    NOT NULL,           -- normalised (rule 11)
    direction      TEXT    NOT NULL CHECK (direction IN ('buy','sell')),
    order_kind     TEXT    NOT NULL CHECK (order_kind IN ('market','limit','stop')),
    request_price  REAL,                       -- limit/stop trigger; NULL for market
    volume         REAL    NOT NULL,
    sl             REAL    NOT NULL DEFAULT 0,
    tp             REAL    NOT NULL DEFAULT 0,
    sl_initial     REAL,                       -- write-once at fill; NULL = unknown
    expires_msc    INTEGER,                    -- NULL = good till cancelled
    status         TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','open','closed','cancelled','expired')),
    requested_msc  INTEGER NOT NULL,
    entry_msc      INTEGER,
    entry_price    REAL,
    exit_msc       INTEGER,
    exit_price     REAL,
    exit_reason    TEXT CHECK (exit_reason IN ('tp','sl','manual','stopout','reverse')),
    net_profit     REAL,                       -- USC, signed; NULL until resolved
    r_multiple     REAL,                       -- NULL when no SL (rule 4)
    mae            REAL,
    mfe            REAL,
    mae_r          REAL,
    mfe_r          REAL,
    parent_id      INTEGER REFERENCES paper_positions(id) ON DELETE CASCADE,
    created_at_msc INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_account
    ON paper_positions (account_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_symbol
    ON paper_positions (symbol, status);

-- Latest tick per symbol. Overwritten freely — a latest-value cache like
-- live_candles, NOT part of the candles append-only contract. `tick_msc` is the
-- broker's tick time; `updated_msc` is true UTC of the overwrite and is what
-- staleness is judged against.
CREATE TABLE IF NOT EXISTS live_quotes (
    symbol      TEXT PRIMARY KEY,
    bid         REAL    NOT NULL,
    ask         REAL    NOT NULL,
    tick_msc    INTEGER NOT NULL,
    updated_msc INTEGER NOT NULL
) WITHOUT ROWID;
```

- [ ] **Step 2: Copy the same DDL into `schema.sql`**

Append the identical statements to `src/journal/store/schema.sql` — put `live_quotes` in the existing "live monitor (Spec C)" section and the two `paper_*` tables in a new "paper trading" section at the end. Both paths must produce the same schema; `tests/test_migrations.py` fails on any drift, including a column-order difference.

- [ ] **Step 3: Bump the version**

In `src/journal/store/db.py:23`:

```python
SCHEMA_VERSION = 13
```

- [ ] **Step 4: Run the drift test**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS. A failure here names the exact table or column that differs between `schema.sql` and the migration — fix the DDL, not the test.

- [ ] **Step 5: Verify a real migration applies**

Run: `uv run journal status`
Expected: exit 0, and no complaint about schema version. This runs against the live store, read-only apart from the migration itself, and proves 013 applies to a populated 62 MB DB rather than only to a fresh one.

- [ ] **Step 6: Commit**

```bash
git add src/journal/store/migrations/013_paper.sql src/journal/store/schema.sql src/journal/store/db.py
git commit -m "feat(paper): tables for a virtual account, its positions, and the latest tick"
```

---

### Task 2: Money and margin primitives

**Files:**
- Create: `src/journal/domain/paper_eval.py`
- Test: `tests/test_paper_eval.py`

**Interfaces:**
- Consumes: `replay_eval.net_profit_usc(direction, entry, exit, volume, tick_size, tick_value) -> float`.
- Produces:
  - `Quote(symbol: str, bid: float, ask: float, time_msc: int)`
  - `Specs(tick_size: float, tick_value: float, contract_size: float, currency_profit: str)`
  - `PaperPos(id, symbol, direction, order_kind, request_price, volume, sl, tp, status, entry_price, entry_msc, expires_msc)`
  - `Event(position_id: int, kind: str, price: float | None, time_msc: int, reason: str | None)`
  - `AccountState(equity, margin, free_margin, margin_level, floating)`
  - `entry_side(direction, quote) -> float`, `exit_side(direction, quote) -> float`
  - `usc_per_quote_unit(specs) -> float | None`
  - `margin_usc(volume, price, specs, leverage) -> float | None`
  - `floating_usc(pos, quote, specs) -> float | None`
  - `account_state(positions, quotes, specs_by_symbol, balance, leverage) -> AccountState`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_eval.py`:

```python
"""The pure paper-trading evaluator. No DB, no bridge, no MT5 — every case here
is a plain dataclass, per CLAUDE.md rules 1, 7 and 12.

The reference figures are hand-computed for XAUUSDc as measured on this account:
tick_size=0.001, tick_value=0.1 USC, contract_size=1.0 (1 lot = 1 oz). 0.10 lot
at 4030 is 0.1 oz worth 403 USD = 40300 USC; at 1:500 that is 80.6 USC of margin.
"""
from __future__ import annotations

import pytest

from journal.domain import paper_eval as pe

XAU = pe.Specs(tick_size=0.001, tick_value=0.1, contract_size=1.0,
               currency_profit="USD")


def q(bid=4030.0, ask=4030.5, t=1_700_000_000_000, symbol="XAUUSDc"):
    return pe.Quote(symbol=symbol, bid=bid, ask=ask, time_msc=t)


def pos(**kw):
    base = dict(id=1, symbol="XAUUSDc", direction="buy", order_kind="market",
                request_price=None, volume=0.10, sl=0.0, tp=0.0, status="open",
                entry_price=4030.0, entry_msc=1_700_000_000_000, expires_msc=None)
    base.update(kw)
    return pe.PaperPos(**base)


def test_usc_per_quote_unit_is_derived_from_the_specs():
    # 0.1 USC per 0.001 USD of price = 100 USC per USD. Never a literal 100.
    assert pe.usc_per_quote_unit(XAU) == pytest.approx(100.0)


def test_margin_matches_the_hand_computed_figure():
    assert pe.margin_usc(0.10, 4030.0, XAU, 500) == pytest.approx(80.6)


def test_margin_is_unknown_for_a_non_usd_quote_currency():
    eur_quoted = pe.Specs(0.001, 0.1, 1.0, "EUR")
    assert pe.margin_usc(0.10, 4030.0, eur_quoted, 500) is None


def test_margin_is_unknown_for_a_malformed_spec_or_leverage():
    assert pe.margin_usc(0.10, 4030.0, pe.Specs(0.0, 0.1, 1.0, "USD"), 500) is None
    assert pe.margin_usc(0.10, 4030.0, XAU, 0) is None


def test_a_buy_enters_at_the_ask_and_exits_at_the_bid():
    assert pe.entry_side("buy", q()) == 4030.5
    assert pe.exit_side("buy", q()) == 4030.0
    assert pe.entry_side("sell", q()) == 4030.0
    assert pe.exit_side("sell", q()) == 4030.5


def test_floating_pnl_of_a_fresh_buy_is_negative_by_the_spread():
    # Entered at the ask (4030.5), marked at the bid (4030.0): 0.5 USD against
    # 0.1 oz = 5 USC. A simulator that showed 0 here would be flattering.
    p = pos(entry_price=4030.5)
    assert pe.floating_usc(p, q(), XAU) == pytest.approx(-5.0)


def test_floating_pnl_is_unknown_when_the_position_never_filled():
    assert pe.floating_usc(pos(status="pending", entry_price=None), q(), XAU) is None


def test_account_state_of_a_flat_account_has_no_margin_level():
    st = pe.account_state([], {}, {"XAUUSDc": XAU}, balance=1_000_000.0,
                          leverage=500)
    assert st.equity == pytest.approx(1_000_000.0)
    assert st.margin == pytest.approx(0.0)
    assert st.margin_level is None      # no margin to divide by — not infinity


def test_account_state_adds_floating_to_balance_and_divides_for_the_level():
    p = pos(entry_price=4030.5)
    st = pe.account_state([p], {"XAUUSDc": q()}, {"XAUUSDc": XAU},
                          balance=1_000_000.0, leverage=500)
    assert st.floating == pytest.approx(-5.0)
    assert st.equity == pytest.approx(999_995.0)
    assert st.margin == pytest.approx(80.61)      # 0.10 lot at the 4030.5 entry
    assert st.free_margin == pytest.approx(999_995.0 - 80.61)
    assert st.margin_level == pytest.approx(999_995.0 / 80.61 * 100)


def test_account_state_reports_unknown_rather_than_guessing_a_missing_quote():
    st = pe.account_state([pos()], {}, {"XAUUSDc": XAU}, balance=1_000_000.0,
                          leverage=500)
    assert st.floating is None
    assert st.equity is None
    assert st.margin_level is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'journal.domain.paper_eval'`.

- [ ] **Step 3: Write the implementation**

Create `src/journal/domain/paper_eval.py`:

```python
"""Pure paper-trading evaluator — the single source of truth for a virtual
account's fills, SL/TP resolution, margin, and stop-out. No DB, no bridge, no
MT5 (CLAUDE.md rules 1, 7, 12): plain dataclasses in, events out, fixture-
testable with nothing running.

Money is USC (account currency); R is unit-free. Every unknown propagates to
`None` and is NEVER coerced to 0 (rule 4) — a coerced margin here would liquidate
an account over a missing spec.

Fill model: a market order fills at the CURRENT quote — a buy at the ask, a sell
at the bid — so a fresh position starts down by the spread, as it really does. A
pending order also fills at the current quote and not at its requested level: tick
data is discrete, and handing out a better price than was observed is a fabricated
gift. SL/TP fill AT the level (slippage across a tick gap is not modelled, the same
choice `replay_eval` makes). When one tick reaches both levels, the STOP fills
first — pessimistic, because tick granularity cannot reveal the true order and an
honest simulator never flatters.
"""
from __future__ import annotations

from dataclasses import dataclass

from .replay_eval import net_profit_usc

_TOL = 1e-9


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    time_msc: int


@dataclass
class Specs:
    tick_size: float
    tick_value: float          # per lot per tick, in ACCOUNT currency (USC)
    contract_size: float
    currency_profit: str       # the QUOTE currency, not the unit of tick_value


@dataclass
class PaperPos:
    id: int
    symbol: str
    direction: str             # "buy" | "sell"
    order_kind: str            # "market" | "limit" | "stop"
    request_price: float | None
    volume: float
    sl: float                  # 0.0 = none set (rule 4)
    tp: float                  # 0.0 = none set (rule 4)
    status: str                # pending | open | closed | cancelled | expired
    entry_price: float | None
    entry_msc: int | None
    expires_msc: int | None    # None = good till cancelled


@dataclass
class Event:
    position_id: int
    kind: str                  # "fill" | "exit" | "expire"
    price: float | None        # None for "expire"
    time_msc: int
    reason: str | None         # exit: "tp"|"sl"|"stopout"; otherwise None


@dataclass
class AccountState:
    equity: float | None
    margin: float | None
    free_margin: float | None
    margin_level: float | None
    floating: float | None


def entry_side(direction: str, quote: Quote) -> float:
    """The price you PAY to open: a buy lifts the ask, a sell hits the bid."""
    return quote.ask if direction == "buy" else quote.bid


def exit_side(direction: str, quote: Quote) -> float:
    """The price you GET to close: a buy exits into the bid, a sell into the ask."""
    return quote.bid if direction == "buy" else quote.ask


def usc_per_quote_unit(specs: Specs) -> float | None:
    """Account-currency units per one unit of quoted price, PER LOT, derived from
    the symbol's own specs rather than typed in. For XAUUSDc, 0.1 USC per 0.001
    USD is 100 USC per USD — the same 100 a literal would have hardcoded, except
    this one self-corrects per symbol and refuses when the specs are malformed.
    """
    if specs.tick_size is None or specs.tick_value is None:
        return None
    if specs.tick_size <= _TOL or specs.tick_value <= _TOL:
        return None
    return specs.tick_value / specs.tick_size


def margin_usc(volume: float | None, price: float | None, specs: Specs,
               leverage: int | None) -> float | None:
    """Margin required, in USC. `volume * price * tick_value / tick_size / leverage`.

    Valid only while the QUOTE currency is USD and the account currency is USC —
    the caller checks the account, this checks the symbol. Anything else is
    `None`: unknown, never a coerced 0 (rule 4, Trap 14).
    """
    if volume is None or price is None or leverage is None:
        return None
    if specs.currency_profit != "USD":
        return None
    if leverage <= 0 or volume <= _TOL or price <= _TOL:
        return None
    per_unit = usc_per_quote_unit(specs)
    if per_unit is None:
        return None
    return volume * price * per_unit / leverage


def floating_usc(pos: PaperPos, quote: Quote, specs: Specs) -> float | None:
    """Unrealised P&L in USC, marked at the side the position would exit on.
    `None` while the position has no entry price — an unfilled order has no P&L,
    and 0 would read as breakeven."""
    if pos.entry_price is None or pos.status != "open":
        return None
    return net_profit_usc(pos.direction, pos.entry_price, exit_side(pos.direction, quote),
                          pos.volume, specs.tick_size, specs.tick_value)


def account_state(positions: list[PaperPos], quotes: dict[str, Quote],
                  specs_by_symbol: dict[str, Specs], balance: float,
                  leverage: int) -> AccountState:
    """Equity, margin and margin level across EVERY open position, on every
    symbol — an account is cross-symbol and its margin is too.

    One missing quote or spec makes the whole account state unknown rather than
    partial. A margin level computed from some of the positions is not a smaller
    truth, it is a wrong number, and this one decides liquidation.
    """
    floating = 0.0
    margin = 0.0
    for p in positions:
        if p.status != "open":
            continue
        quote = quotes.get(p.symbol)
        specs = specs_by_symbol.get(p.symbol)
        if quote is None or specs is None:
            return AccountState(None, None, None, None, None)
        f = floating_usc(p, quote, specs)
        m = margin_usc(p.volume, p.entry_price, specs, leverage)
        if f is None or m is None:
            return AccountState(None, None, None, None, None)
        floating += f
        margin += m

    equity = balance + floating
    level = None if margin <= _TOL else equity / margin * 100.0
    return AccountState(equity=equity, margin=margin,
                        free_margin=equity - margin, margin_level=level,
                        floating=floating)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_eval.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/paper_eval.py tests/test_paper_eval.py
git commit -m "feat(paper): margin and equity derived from each symbol's own specs"
```

---

### Task 3: Fills, triggers and SL/TP per tick

**Files:**
- Modify: `src/journal/domain/paper_eval.py`
- Test: `tests/test_paper_eval.py`

**Interfaces:**
- Consumes: `Quote`, `Specs`, `PaperPos`, `Event`, `entry_side`, `exit_side` from Task 2.
- Produces: `step_tick(positions: list[PaperPos], quote: Quote, now_msc: int) -> list[Event]`. Mutates `positions` in place and returns the events that happened on this tick, in position order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paper_eval.py`:

```python
def test_a_pending_market_order_fills_at_the_ask_for_a_buy():
    p = pos(status="pending", entry_price=None, entry_msc=None)
    events = pe.step_tick([p], q(), now_msc=1_700_000_001_000)
    assert [(e.kind, e.price) for e in events] == [("fill", 4030.5)]
    assert p.status == "open" and p.entry_price == 4030.5


def test_a_buy_limit_triggers_only_once_the_ask_reaches_it():
    p = pos(status="pending", order_kind="limit", request_price=4025.0,
            entry_price=None, entry_msc=None)
    assert pe.step_tick([p], q(bid=4029.5, ask=4030.0), 1) == []
    assert p.status == "pending"
    events = pe.step_tick([p], q(bid=4024.0, ask=4024.5), 2)
    # Filled at the observed ask, NOT at the 4025 that was asked for.
    assert [(e.kind, e.price) for e in events] == [("fill", 4024.5)]


def test_a_buy_stop_triggers_once_the_ask_rises_through_it():
    p = pos(status="pending", order_kind="stop", request_price=4035.0,
            entry_price=None, entry_msc=None)
    assert pe.step_tick([p], q(bid=4030.0, ask=4030.5), 1) == []
    events = pe.step_tick([p], q(bid=4035.5, ask=4036.0), 2)
    assert [(e.kind, e.price) for e in events] == [("fill", 4036.0)]


def test_a_sell_limit_triggers_once_the_bid_rises_to_it():
    p = pos(direction="sell", status="pending", order_kind="limit",
            request_price=4035.0, entry_price=None, entry_msc=None)
    assert pe.step_tick([p], q(bid=4030.0, ask=4030.5), 1) == []
    events = pe.step_tick([p], q(bid=4036.0, ask=4036.5), 2)
    assert [(e.kind, e.price) for e in events] == [("fill", 4036.0)]


def test_a_sell_stop_triggers_once_the_bid_falls_through_it():
    p = pos(direction="sell", status="pending", order_kind="stop",
            request_price=4025.0, entry_price=None, entry_msc=None)
    assert pe.step_tick([p], q(bid=4030.0, ask=4030.5), 1) == []
    events = pe.step_tick([p], q(bid=4024.0, ask=4024.5), 2)
    assert [(e.kind, e.price) for e in events] == [("fill", 4024.0)]


def test_a_pending_order_expires_unfilled_and_never_fills_late():
    p = pos(status="pending", order_kind="limit", request_price=4025.0,
            entry_price=None, entry_msc=None, expires_msc=1_000)
    events = pe.step_tick([p], q(bid=4024.0, ask=4024.5), now_msc=1_001)
    assert [(e.kind, e.price) for e in events] == [("expire", None)]
    assert p.status == "expired"


def test_a_buys_stop_fires_when_the_bid_reaches_it_and_exits_at_the_level():
    p = pos(sl=4025.0)
    events = pe.step_tick([p], q(bid=4024.0, ask=4024.5), 2)
    assert [(e.kind, e.price, e.reason) for e in events] == [("exit", 4025.0, "sl")]
    assert p.status == "closed"


def test_a_buys_target_fires_when_the_bid_reaches_it():
    p = pos(tp=4040.0)
    events = pe.step_tick([p], q(bid=4041.0, ask=4041.5), 2)
    assert [(e.kind, e.price, e.reason) for e in events] == [("exit", 4040.0, "tp")]


def test_a_sells_levels_are_measured_against_the_ask():
    p = pos(direction="sell", entry_price=4030.0, sl=4035.0, tp=4025.0)
    assert pe.step_tick([p], q(bid=4034.0, ask=4034.5), 2) == []   # neither yet
    events = pe.step_tick([p], q(bid=4035.5, ask=4036.0), 3)
    assert [(e.kind, e.price, e.reason) for e in events] == [("exit", 4035.0, "sl")]


def test_the_stop_fills_first_when_one_tick_reaches_both_levels():
    # A tick cannot reveal the order in which the two were touched, so the
    # pessimistic reading is the only honest one.
    p = pos(sl=4025.0, tp=4040.0)
    events = pe.step_tick([p], q(bid=4020.0, ask=4041.0), 2)
    assert [(e.kind, e.price, e.reason) for e in events] == [("exit", 4025.0, "sl")]


def test_a_position_on_another_symbol_is_left_alone():
    p = pos(sl=4025.0, symbol="BTCUSDc")
    assert pe.step_tick([p], q(bid=4000.0, ask=4000.5, symbol="XAUUSDc"), 2) == []
    assert p.status == "open"


def test_an_order_that_fills_can_be_stopped_out_on_the_same_tick():
    # A gap through the stop must not be a free ride: the entry bar can kill you.
    p = pos(status="pending", entry_price=None, entry_msc=None, sl=4029.0)
    events = pe.step_tick([p], q(bid=4028.0, ask=4030.5), 2)
    assert [(e.kind, e.reason) for e in events] == [("fill", None), ("exit", "sl")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_eval.py -k step_tick -v`
Expected: FAIL — `AttributeError: module 'journal.domain.paper_eval' has no attribute 'step_tick'`.

- [ ] **Step 3: Write the implementation**

Append to `src/journal/domain/paper_eval.py`:

```python
def _triggered(pos: PaperPos, quote: Quote) -> bool:
    """Whether a pending order's condition is met by this tick, measured on the
    side it would actually enter on. A market order is always triggered."""
    if pos.order_kind == "market":
        return True
    if pos.request_price is None:
        return False
    price = entry_side(pos.direction, quote)
    if pos.direction == "buy":
        # A buy limit waits for the ask to come DOWN to it; a buy stop for the
        # ask to rise THROUGH it.
        return price <= pos.request_price + _TOL if pos.order_kind == "limit" \
            else price >= pos.request_price - _TOL
    return price >= pos.request_price - _TOL if pos.order_kind == "limit" \
        else price <= pos.request_price + _TOL


def _exit(pos: PaperPos, price: float, time_msc: int, reason: str) -> Event:
    pos.status = "closed"
    return Event(pos.id, "exit", price, time_msc, reason)


def step_tick(positions: list[PaperPos], quote: Quote,
              now_msc: int) -> list[Event]:
    """Advance every position on `quote.symbol` by one tick. Mutates `positions`
    in place and returns this tick's events in position order.

    Order per position, and the order matters:
      1. expire a pending order whose `expires_msc` has passed — an expired order
         must never fill late;
      2. fill a triggered pending order at the current quote;
      3. resolve SL/TP against the exit side, stop-first when both are reached.

    A position filled at (2) is evaluated at (3) on the SAME tick: a gap through
    the stop can end the trade on the tick it started, and pretending otherwise
    would hand out a free ride the market never gave.
    """
    events: list[Event] = []
    for p in positions:
        if p.symbol != quote.symbol:
            continue
        if p.status not in ("pending", "open"):
            continue

        if p.status == "pending":
            if p.expires_msc is not None and now_msc > p.expires_msc:
                p.status = "expired"
                events.append(Event(p.id, "expire", None, now_msc, None))
                continue
            if not _triggered(p, quote):
                continue
            p.status = "open"
            p.entry_price = entry_side(p.direction, quote)
            p.entry_msc = quote.time_msc
            events.append(Event(p.id, "fill", p.entry_price, quote.time_msc, None))

        price = exit_side(p.direction, quote)
        if p.direction == "buy":
            sl_hit = p.sl > _TOL and price <= p.sl + _TOL
            tp_hit = p.tp > _TOL and price >= p.tp - _TOL
        else:
            sl_hit = p.sl > _TOL and price >= p.sl - _TOL
            tp_hit = p.tp > _TOL and price <= p.tp + _TOL

        if sl_hit:                       # stop-first when both are reached
            events.append(_exit(p, p.sl, quote.time_msc, "sl"))
        elif tp_hit:
            events.append(_exit(p, p.tp, quote.time_msc, "tp"))

    return events
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_eval.py -v`
Expected: PASS, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/paper_eval.py tests/test_paper_eval.py
git commit -m "feat(paper): tick fills, pending triggers, and stop-first SL/TP"
```

---

### Task 4: The stop-out cascade

**Files:**
- Modify: `src/journal/domain/paper_eval.py`
- Test: `tests/test_paper_eval.py`

**Interfaces:**
- Consumes: `account_state` (Task 2), `_exit` and `Event` (Task 3).
- Produces: `resolve_stopout(positions, quotes, specs_by_symbol, balance, stopout_pct, leverage, now_msc) -> list[Event]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paper_eval.py`:

```python
def _stopout(positions, balance, pct=20.0, quote=None):
    return pe.resolve_stopout(
        positions, {"XAUUSDc": quote or q(bid=4000.0, ask=4000.5)},
        {"XAUUSDc": XAU}, balance=balance, stopout_pct=pct, leverage=500,
        now_msc=9_000,
    )


def test_no_stopout_while_the_margin_level_is_healthy():
    assert _stopout([pos(entry_price=4030.0)], balance=1_000_000.0) == []


def test_a_flat_account_is_never_stopped_out():
    # No margin means no level to compare — the check is skipped, not divided by.
    assert _stopout([], balance=1.0) == []


def test_the_worst_loser_is_closed_first_and_the_cascade_stops_when_it_can():
    # Two buys, both under water at bid 4000; the 4060 entry is the worse one.
    small = pos(id=1, entry_price=4035.0)
    large = pos(id=2, entry_price=4060.0)
    events = _stopout([small, large], balance=800.0)
    assert [(e.position_id, e.reason) for e in events] == [(2, "stopout")]
    assert large.status == "closed" and small.status == "open"


def test_the_cascade_keeps_closing_until_the_level_recovers():
    a = pos(id=1, entry_price=4055.0)
    b = pos(id=2, entry_price=4060.0)
    events = _stopout([a, b], balance=100.0)
    assert [(e.position_id, e.reason) for e in events] == [(2, "stopout"), (1, "stopout")]


def test_a_stopped_out_position_exits_at_the_exit_side_of_the_quote():
    p = pos(entry_price=4060.0)
    events = _stopout([p], balance=100.0)
    assert events[0].price == pytest.approx(4000.0)   # the bid, for a buy


def test_a_missing_quote_stops_nothing_rather_than_liquidating_on_a_guess():
    p = pos(entry_price=4060.0, symbol="BTCUSDc")
    events = pe.resolve_stopout([p], {}, {"BTCUSDc": XAU}, balance=1.0,
                                stopout_pct=20.0, leverage=500, now_msc=9_000)
    assert events == []
    assert p.status == "open"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_eval.py -k stopout -v`
Expected: FAIL — `AttributeError: ... has no attribute 'resolve_stopout'`.

- [ ] **Step 3: Write the implementation**

Append to `src/journal/domain/paper_eval.py`:

```python
def resolve_stopout(positions: list[PaperPos], quotes: dict[str, Quote],
                    specs_by_symbol: dict[str, Specs], balance: float,
                    stopout_pct: float, leverage: int,
                    now_msc: int) -> list[Event]:
    """Liquidate while the margin level sits below `stopout_pct`, worst loser
    first, recomputing after each close — MT5's own order, not a guess.

    Returns `[]` when there is nothing to liquidate, when the account is flat
    (no margin, so no level), and — deliberately — whenever the account state is
    unknown. A missing quote or spec must never liquidate anything: the one
    number that decides this is exactly the number we do not have.

    `balance` is the REALIZED balance and is not updated here. The caller
    persists each closed slice's P&L; this function only decides who goes.
    """
    events: list[Event] = []
    while True:
        state = account_state(positions, quotes, specs_by_symbol, balance, leverage)
        if state.margin_level is None or state.margin_level >= stopout_pct:
            return events

        open_now = [p for p in positions if p.status == "open"]
        losses = [
            (floating_usc(p, quotes[p.symbol], specs_by_symbol[p.symbol]), p)
            for p in open_now
        ]
        if not losses:
            return events
        _, worst = min(losses, key=lambda pair: pair[0])
        price = exit_side(worst.direction, quotes[worst.symbol])
        events.append(_exit(worst, price, now_msc, "stopout"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_eval.py -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/paper_eval.py tests/test_paper_eval.py
git commit -m "feat(paper): stop-out closes the worst loser first, and refuses to guess"
```

---

### Task 5: The store

**Files:**
- Create: `src/journal/store/paper_store.py`
- Test: `tests/test_paper_store.py`

**Interfaces:**
- Consumes: `store.db.now_ms`, the Task 1 tables.
- Produces:
  - `create_account(conn, *, name, initial_balance, leverage, stopout_pct) -> int`
  - `get_account(conn, account_id) -> sqlite3.Row | None`
  - `list_accounts(conn, status=None) -> list[sqlite3.Row]`
  - `archive_account(conn, account_id) -> None`
  - `add_balance(conn, account_id, delta) -> None`
  - `insert_position(conn, *, account_id, symbol, symbol_base, direction, order_kind, request_price, volume, sl, tp, status, entry_price, entry_msc, expires_msc) -> int`
  - `list_positions(conn, account_id, statuses=None) -> list[sqlite3.Row]`
  - `get_position(conn, position_id) -> sqlite3.Row | None`
  - `open_or_pending_symbols(conn) -> list[str]`
  - `update_status(conn, position_id, status) -> None`
  - `mark_fill(conn, position_id, *, entry_msc, entry_price, sl_initial) -> None`
  - `set_sltp(conn, position_id, *, sl, tp) -> None`
  - `mark_close(conn, position_id, *, exit_msc, exit_price, exit_reason, net_profit, r_multiple, mae, mfe, mae_r, mfe_r) -> None`
  - `split_for_partial(conn, position_id, volume) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_store.py`:

```python
"""Pure DB access for the paper tables. No bridge, no MT5. The invariant worth a
test here is the partial-close SPLIT: the closed slice becomes its own complete
row so no statistic ever needs to understand a half-realised position."""
from __future__ import annotations

import pytest

from journal.store import paper_store as ps
from journal.store.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


@pytest.fixture
def account(conn):
    return ps.create_account(conn, name="Scalping XAU", initial_balance=1_000_000.0,
                             leverage=500, stopout_pct=20.0)


def _pos(conn, account, **kw):
    base = dict(account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
                direction="buy", order_kind="market", request_price=None,
                volume=0.10, sl=0.0, tp=0.0, status="open", entry_price=4030.0,
                entry_msc=1_000, expires_msc=None)
    base.update(kw)
    return ps.insert_position(conn, **base)


def test_an_account_starts_with_its_balance_equal_to_what_was_funded(conn, account):
    row = ps.get_account(conn, account)
    assert row["balance"] == pytest.approx(1_000_000.0)
    assert row["initial_balance"] == pytest.approx(1_000_000.0)
    assert row["status"] == "active"


def test_two_accounts_cannot_share_a_name(conn, account):
    with pytest.raises(ValueError, match="sudah dipakai"):
        ps.create_account(conn, name="Scalping XAU", initial_balance=1.0,
                          leverage=500, stopout_pct=20.0)


def test_archiving_keeps_the_row_and_stamps_when(conn, account):
    ps.archive_account(conn, account)
    row = ps.get_account(conn, account)
    assert row["status"] == "archived"
    assert row["archived_at_msc"] is not None


def test_listing_filters_by_status(conn, account):
    other = ps.create_account(conn, name="Swing", initial_balance=1.0,
                              leverage=100, stopout_pct=50.0)
    ps.archive_account(conn, other)
    assert [r["id"] for r in ps.list_accounts(conn, status="active")] == [account]


def test_balance_moves_by_a_signed_delta(conn, account):
    ps.add_balance(conn, account, -250.5)
    assert ps.get_account(conn, account)["balance"] == pytest.approx(999_749.5)


def test_symbols_needing_a_quote_are_the_open_and_pending_ones_only(conn, account):
    _pos(conn, account, symbol="XAUUSDc", status="open")
    _pos(conn, account, symbol="BTCUSDc", status="pending", entry_price=None,
         order_kind="limit", request_price=50_000.0)
    closed = _pos(conn, account, symbol="EURUSDc", status="open")
    ps.mark_close(conn, closed, exit_msc=2_000, exit_price=4031.0,
                  exit_reason="manual", net_profit=10.0, r_multiple=None,
                  mae=None, mfe=None, mae_r=None, mfe_r=None)
    assert sorted(ps.open_or_pending_symbols(conn)) == ["BTCUSDc", "XAUUSDc"]


def test_marking_a_fill_writes_the_initial_stop_once(conn, account):
    pid = _pos(conn, account, status="pending", entry_price=None, entry_msc=None,
               sl=4025.0)
    ps.mark_fill(conn, pid, entry_msc=1_500, entry_price=4030.5, sl_initial=4025.0)
    row = ps.get_position(conn, pid)
    assert row["status"] == "open"
    assert row["entry_price"] == pytest.approx(4030.5)
    assert row["sl_initial"] == pytest.approx(4025.0)

    # Moving the stop later must not rewrite the initial one — R depends on it.
    ps.set_sltp(conn, pid, sl=4029.0, tp=0.0)
    row = ps.get_position(conn, pid)
    assert row["sl"] == pytest.approx(4029.0)
    assert row["sl_initial"] == pytest.approx(4025.0)


def test_a_partial_close_splits_into_a_closed_child_and_a_smaller_parent(conn, account):
    parent = _pos(conn, account, volume=0.10)
    child = ps.split_for_partial(conn, parent, 0.04)

    parent_row = ps.get_position(conn, parent)
    child_row = ps.get_position(conn, child)
    assert parent_row["volume"] == pytest.approx(0.06)
    assert parent_row["status"] == "open"
    assert child_row["volume"] == pytest.approx(0.04)
    assert child_row["parent_id"] == parent
    # The child is a COMPLETE trade record: it carries the parent's entry.
    assert child_row["entry_price"] == pytest.approx(4030.0)
    assert child_row["entry_msc"] == 1_000
    assert child_row["status"] == "open"       # the caller then closes it


def test_splitting_more_than_is_held_is_refused(conn, account):
    parent = _pos(conn, account, volume=0.10)
    with pytest.raises(ValueError, match="lebih besar"):
        ps.split_for_partial(conn, parent, 0.10)


def test_deleting_an_account_takes_its_positions_with_it(conn, account):
    _pos(conn, account)
    conn.execute("DELETE FROM paper_accounts WHERE id = ?", (account,))
    conn.commit()
    assert ps.list_positions(conn, account) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'journal.store.paper_store'`.

- [ ] **Step 3: Write the implementation**

Create `src/journal/store/paper_store.py`:

```python
"""paper_store — pure DB access for the paper-trading tables. No MT5 adapter
import (rules 1/12), no evaluation logic (that is `domain/paper_eval`), and
nothing near `trades`/raw (rule 2). Money is USC.

`split_for_partial` is the one function with a real invariant: a partial close
inserts a child row carrying the parent's entry and reduces the parent's volume,
so every closed row is a complete trade record.
"""
from __future__ import annotations

import sqlite3

from .db import now_ms


def create_account(conn: sqlite3.Connection, *, name: str, initial_balance: float,
                   leverage: int, stopout_pct: float) -> int:
    try:
        cur = conn.execute(
            "INSERT INTO paper_accounts "
            "(name, initial_balance, balance, leverage, stopout_pct, status, "
            " created_at_msc) VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (name, initial_balance, initial_balance, leverage, stopout_pct, now_ms()),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Nama akun '{name}' sudah dipakai.") from e
    conn.commit()
    return int(cur.lastrowid)


def get_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
    ).fetchone()


def list_accounts(conn: sqlite3.Connection,
                  status: str | None = None) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute("SELECT * FROM paper_accounts ORDER BY id DESC").fetchall()
    return conn.execute(
        "SELECT * FROM paper_accounts WHERE status = ? ORDER BY id DESC", (status,)
    ).fetchall()


def archive_account(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "UPDATE paper_accounts SET status = 'archived', archived_at_msc = ? "
        "WHERE id = ?", (now_ms(), account_id),
    )
    conn.commit()


def add_balance(conn: sqlite3.Connection, account_id: int, delta: float) -> None:
    """Move the REALIZED balance by a signed amount, in USC."""
    conn.execute(
        "UPDATE paper_accounts SET balance = balance + ? WHERE id = ?",
        (delta, account_id),
    )
    conn.commit()


def insert_position(conn: sqlite3.Connection, *, account_id: int, symbol: str,
                    symbol_base: str, direction: str, order_kind: str,
                    request_price: float | None, volume: float, sl: float,
                    tp: float, status: str, entry_price: float | None,
                    entry_msc: int | None, expires_msc: int | None) -> int:
    ts = now_ms()
    cur = conn.execute(
        "INSERT INTO paper_positions "
        "(account_id, symbol, symbol_base, direction, order_kind, request_price, "
        " volume, sl, tp, sl_initial, expires_msc, status, requested_msc, "
        " entry_msc, entry_price, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, symbol, symbol_base, direction, order_kind, request_price,
         volume, sl, tp, (sl if status == "open" and sl > 0 else None),
         expires_msc, status, ts, entry_msc, entry_price, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_positions(conn: sqlite3.Connection, account_id: int,
                   statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
    if statuses is None:
        return conn.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? ORDER BY id",
            (account_id,),
        ).fetchall()
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT * FROM paper_positions WHERE account_id = ? "
        f"AND status IN ({marks}) ORDER BY id",
        (account_id, *statuses),
    ).fetchall()


def get_position(conn: sqlite3.Connection, position_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE id = ?", (position_id,)
    ).fetchone()


def open_or_pending_symbols(conn: sqlite3.Connection) -> list[str]:
    """Every symbol that some ACTIVE account still has live exposure on — the
    exact set the daemon needs a tick for. No exposure means no bridge call."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT p.symbol FROM paper_positions p "
        "JOIN paper_accounts a ON a.id = p.account_id "
        "WHERE p.status IN ('pending','open') AND a.status = 'active'"
    ).fetchall()]


def update_status(conn: sqlite3.Connection, position_id: int, status: str) -> None:
    conn.execute(
        "UPDATE paper_positions SET status = ? WHERE id = ?", (status, position_id)
    )
    conn.commit()


def mark_fill(conn: sqlite3.Connection, position_id: int, *, entry_msc: int,
              entry_price: float, sl_initial: float | None) -> None:
    """Open the position and record the stop it was born with. `sl_initial` is
    written here and nowhere else, which is what keeps R honest after a move."""
    conn.execute(
        "UPDATE paper_positions SET status = 'open', entry_msc = ?, "
        "entry_price = ?, sl_initial = ? WHERE id = ?",
        (entry_msc, entry_price, sl_initial, position_id),
    )
    conn.commit()


def set_sltp(conn: sqlite3.Connection, position_id: int, *, sl: float,
             tp: float) -> None:
    """Move the live stop/target. Never touches `sl_initial`."""
    conn.execute(
        "UPDATE paper_positions SET sl = ?, tp = ? WHERE id = ?",
        (sl, tp, position_id),
    )
    conn.commit()


def mark_close(conn: sqlite3.Connection, position_id: int, *, exit_msc: int,
               exit_price: float | None, exit_reason: str,
               net_profit: float | None, r_multiple: float | None,
               mae: float | None, mfe: float | None, mae_r: float | None,
               mfe_r: float | None) -> None:
    conn.execute(
        "UPDATE paper_positions SET status = 'closed', exit_msc = ?, "
        "exit_price = ?, exit_reason = ?, net_profit = ?, r_multiple = ?, "
        "mae = ?, mfe = ?, mae_r = ?, mfe_r = ? WHERE id = ?",
        (exit_msc, exit_price, exit_reason, net_profit, r_multiple,
         mae, mfe, mae_r, mfe_r, position_id),
    )
    conn.commit()


def split_for_partial(conn: sqlite3.Connection, position_id: int,
                      volume: float) -> int:
    """Carve `volume` off an open position into a new child row and return its id.

    The child inherits the parent's symbol, direction, entry price and entry
    time, so once the caller closes it, it is a complete trade record on its own.
    Refuses a slice that is not strictly smaller than what is held — closing the
    whole thing is `mark_close`, not a split.
    """
    parent = get_position(conn, position_id)
    if parent is None:
        raise ValueError(f"tidak ada posisi paper {position_id}")
    if parent["status"] != "open":
        raise ValueError("hanya posisi terbuka yang bisa ditutup sebagian")
    if volume >= parent["volume"] - 1e-9:
        raise ValueError(
            f"volume {volume} lebih besar atau sama dengan volume posisi "
            f"{parent['volume']} — pakai close penuh"
        )
    ts = now_ms()
    cur = conn.execute(
        "INSERT INTO paper_positions "
        "(account_id, symbol, symbol_base, direction, order_kind, request_price, "
        " volume, sl, tp, sl_initial, expires_msc, status, requested_msc, "
        " entry_msc, entry_price, parent_id, created_at_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'open', ?, ?, ?, ?, ?)",
        (parent["account_id"], parent["symbol"], parent["symbol_base"],
         parent["direction"], parent["order_kind"], parent["request_price"],
         volume, parent["sl"], parent["tp"], parent["sl_initial"],
         ts, parent["entry_msc"], parent["entry_price"], position_id, ts),
    )
    conn.execute(
        "UPDATE paper_positions SET volume = volume - ? WHERE id = ?",
        (volume, position_id),
    )
    conn.commit()
    return int(cur.lastrowid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_store.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/paper_store.py tests/test_paper_store.py
git commit -m "feat(paper): store where a partial close splits into a complete trade record"
```

---

### Task 6: The shared simulated-account summary

**Files:**
- Create: `src/journal/domain/sim_stats.py`
- Modify: `src/journal/store/training_store.py`
- Test: `tests/test_training_store.py` (exists — it must keep passing unchanged, which is the point)

**Interfaces:**
- Consumes: nothing.
- Produces: `sim_stats.summary(rows) -> dict` with keys `n`, `win_rate`, `avg_r`, `total_r`, `avg_mae_r`, `avg_mfe_r`. `rows` are mappings carrying `net_profit`, `r_multiple`, `mae_r`, `mfe_r`.

- [ ] **Step 1: Move the function, byte-for-byte in behaviour**

Create `src/journal/domain/sim_stats.py` and move `_summary` out of
`src/journal/store/training_store.py` into it as `summary`, keeping the body and
the docstring's reasoning intact:

```python
"""Summary statistics shared by every SIMULATED account — replay/training and
paper trading. Pure: mappings in, a dict out.

NOT §8-gated, unlike `analytics/report`. A replay session or a paper account is a
handful of trades, so a 20-sample floor blanked every rate permanently and the
panel carried no information at all. Every metric ships with its own `n`; the
reader judges the sample.

Only CLOSED, resolved rows (non-null `net_profit`) count. An unfilled or
unresolved position is excluded — unknown outcome, rule 4.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def summary(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Aggregate resolved rows. A metric is null only when it has NO input
    (rule 4 — unknown, never zero)."""
    resolved = [r for r in rows if r["net_profit"] is not None]
    n = len(resolved)
    r_vals = [r["r_multiple"] for r in resolved if r["r_multiple"] is not None]
    mae_vals = [r["mae_r"] for r in resolved if r["mae_r"] is not None]
    mfe_vals = [r["mfe_r"] for r in resolved if r["mfe_r"] is not None]
    total_r = sum(r_vals)
    wins = sum(1 for r in resolved if r["net_profit"] > 0)
    return {
        "n": n,
        "win_rate": (wins / n) if n else None,
        "avg_r": (total_r / len(r_vals)) if r_vals else None,
        "total_r": total_r,
        "avg_mae_r": (sum(mae_vals) / len(mae_vals)) if mae_vals else None,
        "avg_mfe_r": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
    }
```

- [ ] **Step 2: Point `training_store` at it**

In `src/journal/store/training_store.py`, delete the `_summary` function and add
the import, leaving `session_summary` and `career_summary` otherwise untouched:

```python
from ..domain.sim_stats import summary as _summary
```

Keep the module docstring's paragraph about summaries being ungated, but add a
line saying the aggregator itself now lives in `domain/sim_stats.py` and is
shared with paper trading.

- [ ] **Step 3: Run the existing tests to prove nothing moved**

Run: `uv run pytest tests/test_training_store.py tests/test_web.py -v`
Expected: PASS with no test changes. A behaviour difference here means the move
was not a move.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — the same count as before this task, since nothing was added.

- [ ] **Step 5: Commit**

```bash
git add src/journal/domain/sim_stats.py src/journal/store/training_store.py
git commit -m "refactor: one simulated-account summary, shared by replay and paper"
```

---

## Phase 2 — the daemon

### Task 7: Quote storage

**Files:**
- Modify: `src/journal/store/live_store.py`
- Test: `tests/test_live_store.py` (create if absent; otherwise append)

**Interfaces:**
- Consumes: the `live_quotes` table (Task 1).
- Produces:
  - `upsert_quote(conn, symbol, *, bid, ask, tick_msc, now_msc) -> None`
  - `read_quote(conn, symbol) -> sqlite3.Row | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live_store.py`:

```python
def test_a_quote_is_overwritten_in_place_and_stamps_when_it_was_written(conn):
    live_store.upsert_quote(conn, "XAUUSDc", bid=4030.0, ask=4030.5,
                            tick_msc=1_000, now_msc=1_100)
    live_store.upsert_quote(conn, "XAUUSDc", bid=4031.0, ask=4031.5,
                            tick_msc=2_000, now_msc=2_100)
    row = live_store.read_quote(conn, "XAUUSDc")
    assert (row["bid"], row["ask"]) == (4031.0, 4031.5)
    assert row["tick_msc"] == 2_000
    assert row["updated_msc"] == 2_100
    assert conn.execute("SELECT COUNT(*) FROM live_quotes").fetchone()[0] == 1


def test_an_unseen_symbol_has_no_quote_rather_than_a_zero_one(conn):
    assert live_store.read_quote(conn, "BTCUSDc") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_live_store.py -k quote -v`
Expected: FAIL — `AttributeError: module 'journal.store.live_store' has no attribute 'upsert_quote'`.

- [ ] **Step 3: Write the implementation**

Append to `src/journal/store/live_store.py`:

```python
def upsert_quote(conn: sqlite3.Connection, symbol: str, *, bid: float, ask: float,
                 tick_msc: int, now_msc: int) -> None:
    """Overwrite the single latest-tick row for `symbol`. A latest-value cache,
    like the forming bar — never an append log. `tick_msc` is the broker's tick
    time; `now_msc` is true UTC and is what staleness is judged against."""
    conn.execute(
        "INSERT INTO live_quotes (symbol, bid, ask, tick_msc, updated_msc) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET bid = excluded.bid, "
        "ask = excluded.ask, tick_msc = excluded.tick_msc, "
        "updated_msc = excluded.updated_msc",
        (symbol, bid, ask, tick_msc, now_msc),
    )
    conn.commit()


def read_quote(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    """The latest stored tick for `symbol`, or None if none was ever stored.
    None means unknown — the caller refuses, it does not substitute a price."""
    return conn.execute(
        "SELECT * FROM live_quotes WHERE symbol = ?", (symbol,)
    ).fetchone()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_live_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/journal/store/live_store.py tests/test_live_store.py
git commit -m "feat(paper): store the latest tick per symbol for the paper evaluator"
```

---

### Task 8: The paper step inside `live_cycle`

**Files:**
- Create: nothing
- Modify: `src/journal/ingest/live.py` (add `paper_step`, call it from `live_cycle` right after the step-4 beacon)
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `client.symbol_info_tick(symbol) -> Tick | None`; `paper_store.open_or_pending_symbols`, `list_positions`, `mark_fill`, `mark_close`, `update_status`, `add_balance`, `get_account`, `list_accounts`; `live_store.upsert_quote`; `paper_eval.step_tick`, `resolve_stopout`, `Quote`, `Specs`, `PaperPos`; `replay_eval.net_profit_usc`, `r_multiple`.
- Produces: `paper_step(client, conn, *, now_msc) -> int` — the number of positions it resolved. `LiveReport` gains a `paper_resolved: int` field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live.py`:

```python
def test_the_paper_step_makes_no_bridge_call_when_nothing_is_open(conn):
    # The cost of a feature nobody is using must be zero, not small.
    client = FakeLiveClient(positions=[])
    live.paper_step(client, conn, now_msc=1_000)
    assert client.tick_calls == []


def test_the_paper_step_fills_a_pending_order_and_stores_the_quote(conn):
    account = paper_store.create_account(conn, name="T", initial_balance=1_000_000.0,
                                         leverage=500, stopout_pct=20.0)
    pid = paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="pending", entry_price=None, entry_msc=None,
        expires_msc=None,
    )
    client = FakeLiveClient(positions=[], tick=_tick(bid=4030.0, ask=4030.5))

    live.paper_step(client, conn, now_msc=1_000)

    row = paper_store.get_position(conn, pid)
    assert row["status"] == "open"
    assert row["entry_price"] == pytest.approx(4030.5)
    assert live_store.read_quote(conn, "XAUUSDc")["bid"] == pytest.approx(4030.0)


def test_a_stop_hit_credits_the_realized_balance_and_records_r(conn):
    account = paper_store.create_account(conn, name="T", initial_balance=1_000_000.0,
                                         leverage=500, stopout_pct=20.0)
    _seed_specs(conn)      # XAUUSDc: tick_size .001, tick_value .1, contract 1.0
    pid = paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=4025.0, tp=0.0, status="open", entry_price=4030.0, entry_msc=500,
        expires_msc=None,
    )
    conn.execute("UPDATE paper_positions SET sl_initial = 4025.0 WHERE id = ?", (pid,))
    conn.commit()
    client = FakeLiveClient(positions=[], tick=_tick(bid=4024.0, ask=4024.5))

    live.paper_step(client, conn, now_msc=2_000)

    row = paper_store.get_position(conn, pid)
    assert row["status"] == "closed" and row["exit_reason"] == "sl"
    # 5 USD adverse on 0.1 oz = 500 USC lost; R is exactly -1 at the initial stop.
    assert row["net_profit"] == pytest.approx(-500.0)
    assert row["r_multiple"] == pytest.approx(-1.0)
    assert paper_store.get_account(conn, account)["balance"] == pytest.approx(999_500.0)


def test_a_raising_bridge_does_not_kill_the_paper_step(conn):
    paper_store.create_account(conn, name="T", initial_balance=1_000.0,
                               leverage=500, stopout_pct=20.0)
    account = paper_store.list_accounts(conn)[0]["id"]
    paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="open", entry_price=4030.0, entry_msc=1,
        expires_msc=None,
    )
    client = FakeLiveClient(positions=[], tick_raises=RuntimeError("bridge gone"))
    # Losing the loop loses unrecoverable live SL history. Play money never wins
    # that trade-off.
    assert live.paper_step(client, conn, now_msc=1_000) == 0


def test_live_cycle_runs_the_paper_step_even_with_trading_off(conn):
    account = paper_store.create_account(conn, name="T", initial_balance=1_000_000.0,
                                         leverage=500, stopout_pct=20.0)
    paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="pending", entry_price=None, entry_msc=None,
        expires_msc=None,
    )
    client = FakeLiveClient(positions=[], tick=_tick(bid=4030.0, ask=4030.5))
    report = live.live_cycle(client, conn, _LOGIN, trading=False)
    assert report.paper_resolved == 1
```

Extend the existing `FakeLiveClient` in `tests/test_live.py` with a tick surface,
recording calls so the zero-cost test can assert on them:

```python
    def __init__(self, positions, tick=None, tick_raises=None):
        ...                       # keep the existing body
        self._tick = tick
        self._tick_raises = tick_raises
        self.tick_calls: list[str] = []

    def symbol_info_tick(self, symbol):
        self.tick_calls.append(symbol)
        if self._tick_raises is not None:
            raise self._tick_raises
        return self._tick


def _tick(*, bid, ask, time_msc=1_000):
    from journal.adapter.base import Tick
    return Tick(time=time_msc // 1000, time_msc=time_msc, bid=bid, ask=ask)


def _seed_specs(conn):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, digits, point, tick_size, "
        "tick_value, contract_size, currency_profit, fetched_at, volume_min, "
        "volume_max, volume_step, stops_level, freeze_level, trade_mode, "
        "filling_mode) VALUES ('XAUUSDc', 'XAUUSD', 3, 0.001, 0.001, 0.1, 1.0, "
        "'USD', 1, 0.01, 100.0, 0.01, 0, 0, 4, 1)"
    )
    conn.commit()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_live.py -k paper -v`
Expected: FAIL — `AttributeError: module 'journal.ingest.live' has no attribute 'paper_step'`.

- [ ] **Step 3: Write the implementation**

Add to `src/journal/ingest/live.py`:

```python
def _paper_specs(conn: sqlite3.Connection, symbol: str) -> pe.Specs | None:
    row = conn.execute(
        "SELECT tick_size, tick_value, contract_size, currency_profit "
        "FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row is None or row["tick_size"] in (None, 0) or row["tick_value"] in (None, 0):
        return None
    return pe.Specs(
        tick_size=float(row["tick_size"]), tick_value=float(row["tick_value"]),
        contract_size=float(row["contract_size"] or 1.0),
        currency_profit=row["currency_profit"] or "",
    )


def _as_state(row: sqlite3.Row) -> pe.PaperPos:
    return pe.PaperPos(
        id=row["id"], symbol=row["symbol"], direction=row["direction"],
        order_kind=row["order_kind"], request_price=row["request_price"],
        volume=row["volume"], sl=row["sl"] or 0.0, tp=row["tp"] or 0.0,
        status=row["status"], entry_price=row["entry_price"],
        entry_msc=row["entry_msc"], expires_msc=row["expires_msc"],
    )


def _persist_exit(conn: sqlite3.Connection, row: sqlite3.Row, ev: pe.Event,
                  specs: pe.Specs) -> None:
    """Write one closed slice: money from the specs, R from `sl_initial`, and the
    account's realized balance moved by exactly that money. MAE/MFE stay NULL
    here — they need cached candle rows, and the close path must not block the
    daemon on a candle read; `web/paper.py` fills them when the panel asks."""
    entry = row["entry_price"]
    net = None if entry is None else rev.net_profit_usc(
        row["direction"], entry, ev.price, row["volume"],
        specs.tick_size, specs.tick_value,
    )
    r = None
    if entry is not None and row["sl_initial"] is not None:
        r = rev.r_multiple(row["direction"], entry, ev.price, row["sl_initial"])
    paper_store.mark_close(
        conn, row["id"], exit_msc=ev.time_msc, exit_price=ev.price,
        exit_reason=ev.reason, net_profit=net, r_multiple=r,
        mae=None, mfe=None, mae_r=None, mfe_r=None,
    )
    if net is not None:
        paper_store.add_balance(conn, row["account_id"], net)


def paper_step(client: MT5Client, conn: sqlite3.Connection, *,
               now_msc: int) -> int:
    """Advance every live paper position by one tick, and return how many were
    resolved (filled, expired, or closed).

    Zero exposure means zero bridge calls: the symbol list comes from the DB
    first. A bridge failure is logged and the step returns — losing the loop
    loses unrecoverable live SL history, and no simulated account is worth that.

    Runs regardless of `trading`: paper is not real trading.
    """
    symbols = paper_store.open_or_pending_symbols(conn)
    if not symbols:
        return 0

    quotes: dict[str, pe.Quote] = {}
    specs: dict[str, pe.Specs] = {}
    for symbol in symbols:
        try:
            tick = client.symbol_info_tick(symbol)
        except Exception:
            log.exception("paper: tick fetch failed for %s — step skipped", symbol)
            return 0
        if tick is None or tick.bid is None or tick.ask is None:
            continue
        spec = _paper_specs(conn, symbol)
        if spec is None:
            continue          # rule 4: no specs, no money math, no guess
        tick_msc = tick.time_msc or now_msc
        live_store.upsert_quote(conn, symbol, bid=float(tick.bid),
                                ask=float(tick.ask), tick_msc=tick_msc,
                                now_msc=now_msc)
        quotes[symbol] = pe.Quote(symbol=symbol, bid=float(tick.bid),
                                  ask=float(tick.ask), time_msc=tick_msc)
        specs[symbol] = spec

    resolved = 0
    for account in paper_store.list_accounts(conn, status="active"):
        rows = {r["id"]: r for r in paper_store.list_positions(
            conn, account["id"], statuses=("pending", "open"))}
        states = [_as_state(r) for r in rows.values()]
        if not states:
            continue

        events: list[pe.Event] = []
        for quote in quotes.values():
            events.extend(pe.step_tick(states, quote, now_msc))
        events.extend(pe.resolve_stopout(
            states, quotes, specs, balance=float(account["balance"]),
            stopout_pct=float(account["stopout_pct"]),
            leverage=int(account["leverage"]), now_msc=now_msc,
        ))

        for ev in events:
            row = rows[ev.position_id]
            if ev.kind == "fill":
                paper_store.mark_fill(
                    conn, ev.position_id, entry_msc=ev.time_msc,
                    entry_price=ev.price,
                    sl_initial=(row["sl"] if row["sl"] and row["sl"] > 0 else None),
                )
            elif ev.kind == "expire":
                paper_store.update_status(conn, ev.position_id, "expired")
            else:
                # Re-read: a fill earlier in this same loop wrote the entry price
                # this exit's money depends on.
                fresh = paper_store.get_position(conn, ev.position_id)
                _persist_exit(conn, fresh, ev, specs[fresh["symbol"]])
            resolved += 1

    return resolved
```

Add the imports at the top of `src/journal/ingest/live.py`:

```python
from ..domain import paper_eval as pe
from ..domain import replay_eval as rev
from ..store import paper_store
```

Add the field to `LiveReport` (keep the existing fields and their order):

```python
    paper_resolved: int = 0
```

And call it in `live_cycle`, immediately after the step-4 beacon and before the
close-detection ingest:

```python
    # (4b) paper trading. AHEAD of the ingest pipeline and the order send, both
    # of which can block for seconds on a bridge round trip: a paper SL has a
    # deadline the same way an order does. Zero cost when no paper position is
    # live, and it runs with `trading` off — paper is not real trading.
    try:
        paper_resolved = paper_step(client, conn, now_msc=observed_msc)
    except Exception:
        log.exception("paper: step failed — loop continues")
        paper_resolved = 0
```

Then pass `paper_resolved=paper_resolved` into the `LiveReport(...)` construction
at the end of `live_cycle`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS, including the five new paper tests.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/ingest/live.py tests/test_live.py
git commit -m "feat(paper): the daemon evaluates paper positions per tick, ahead of blocking work"
```

---

## Phase 3 — the web service

### Task 9: Make the order validators public

**Files:**
- Modify: `src/journal/domain/commands.py`
- Test: `tests/test_commands.py` (exists)

**Interfaces:**
- Produces: `check_volume(kind, position, spec, volume) -> None` and `check_level(name, level, direction, price, spec) -> None`, both raising `CommandError`. Same bodies, same messages.

- [ ] **Step 1: Rename, and update every call site**

In `src/journal/domain/commands.py`, rename `_check_volume` → `check_volume` and
`_check_level` → `check_level`, and update the calls inside `validate()`. Leave
`_is_multiple`, `_reduces_exposure` and `_check_trade_mode` private — paper does
not call them. Add one line to each docstring saying paper trading is now a second
caller, and that `price` is the entry price for a market order and the requested
price for a pending one.

- [ ] **Step 2: Run the existing tests**

Run: `uv run pytest tests/test_commands.py -v`
Expected: PASS. Any test referring to the old private names updates to the new
ones; no assertion changes.

- [ ] **Step 3: Confirm nothing else referenced the old names**

Run: `rg -n "_check_volume|_check_level" src tests`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src/journal/domain/commands.py tests/test_commands.py
git commit -m "refactor: expose the volume and level validators for paper trading"
```

---

### Task 10: Accounts and the account view

**Files:**
- Create: `src/journal/web/paper.py`
- Test: `tests/test_paper_web.py`

**Interfaces:**
- Consumes: `paper_store` (Task 5), `paper_eval.account_state` (Task 2), `live_store.read_quote` (Task 7), `sim_stats.summary` (Task 6), `analytics.report.sequence_stats`.
- Produces:
  - `create_account(conn, *, name, initial_balance, leverage, stopout_pct) -> dict`
  - `list_accounts_view(conn, status=None) -> list[dict]`
  - `archive_account(conn, account_id) -> dict`
  - `account_view(conn, account_id) -> dict | None` with keys `account`, `header`, `open`, `pending`, `closed`, `summary`, `equity_curve`
  - `PaperError(Exception)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_web.py`:

```python
"""The paper-trading service functions, called directly against a seeded DB with
no HTTP layer — the discipline `tests/test_web.py` states, and why this project
carries no TestClient dependency.

What a UI can silently violate, and is therefore tested here: money always
carries its unit, an unknown never reads as 0 (rule 4), and a stale feed refuses
rather than resizes.
"""
from __future__ import annotations

import pytest

from journal.store import live_store, paper_store
from journal.store.db import connect, now_ms
from journal.web import paper


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    _seed_specs(c)
    yield c
    c.close()


def _seed_specs(conn):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, digits, point, tick_size, "
        "tick_value, contract_size, currency_profit, fetched_at, volume_min, "
        "volume_max, volume_step, stops_level, freeze_level, trade_mode, "
        "filling_mode) VALUES ('XAUUSDc', 'XAUUSD', 3, 0.001, 0.001, 0.1, 1.0, "
        "'USD', 1, 0.01, 100.0, 0.01, 0, 0, 4, 1)"
    )
    conn.commit()


def _fresh_quote(conn, bid=4030.0, ask=4030.5):
    live_store.upsert_quote(conn, "XAUUSDc", bid=bid, ask=ask,
                            tick_msc=now_ms(), now_msc=now_ms())


@pytest.fixture
def account(conn):
    return paper.create_account(conn, name="Scalping XAU",
                                initial_balance=1_000_000.0, leverage=500,
                                stopout_pct=20.0)["id"]


def test_a_new_account_is_flat_and_says_its_currency(conn, account):
    view = paper.account_view(conn, account)
    assert view["header"]["currency"] == "USC"
    assert view["header"]["balance"] == pytest.approx(1_000_000.0)
    assert view["header"]["equity"] == pytest.approx(1_000_000.0)
    assert view["header"]["margin_level"] is None      # flat, not infinite
    assert view["open"] == [] and view["pending"] == []


def test_an_account_that_does_not_exist_is_none_not_an_empty_account(conn):
    assert paper.account_view(conn, 999) is None


def test_a_duplicate_name_is_refused_with_a_readable_message(conn, account):
    with pytest.raises(paper.PaperError, match="sudah dipakai"):
        paper.create_account(conn, name="Scalping XAU", initial_balance=1.0,
                             leverage=500, stopout_pct=20.0)


def test_a_nonsense_account_is_refused_rather_than_created(conn):
    for kwargs in (
        dict(initial_balance=0.0, leverage=500, stopout_pct=20.0),
        dict(initial_balance=1_000.0, leverage=0, stopout_pct=20.0),
        dict(initial_balance=1_000.0, leverage=500, stopout_pct=-1.0),
    ):
        with pytest.raises(paper.PaperError):
            paper.create_account(conn, name=f"x{kwargs}", **kwargs)


def test_the_header_marks_an_open_position_at_the_stored_quote(conn, account):
    _fresh_quote(conn)
    paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="open", entry_price=4030.5, entry_msc=1,
        expires_msc=None,
    )
    header = paper.account_view(conn, account)["header"]
    assert header["floating"] == pytest.approx(-5.0)      # the spread, honestly
    assert header["equity"] == pytest.approx(999_995.0)
    assert header["margin"] == pytest.approx(80.61)
    assert header["margin_level"] is not None


def test_the_header_reports_unknown_when_no_quote_has_ever_arrived(conn, account):
    paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="open", entry_price=4030.5, entry_msc=1,
        expires_msc=None,
    )
    header = paper.account_view(conn, account)["header"]
    assert header["equity"] is None and header["margin_level"] is None


def test_the_equity_curve_and_drawdown_read_closed_slices_in_exit_order(conn, account):
    for net, exit_msc in ((-200.0, 3_000), (500.0, 1_000), (-100.0, 2_000)):
        pid = paper_store.insert_position(
            conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
            direction="buy", order_kind="market", request_price=None, volume=0.01,
            sl=0.0, tp=0.0, status="open", entry_price=4030.0, entry_msc=1,
            expires_msc=None,
        )
        paper_store.mark_close(conn, pid, exit_msc=exit_msc, exit_price=4031.0,
                               exit_reason="manual", net_profit=net,
                               r_multiple=None, mae=None, mfe=None,
                               mae_r=None, mfe_r=None)
        paper_store.add_balance(conn, account, net)

    view = paper.account_view(conn, account)
    curve = view["equity_curve"]
    assert [p["exit_msc"] for p in curve] == [1_000, 2_000, 3_000]
    assert [p["balance"] for p in curve] == pytest.approx(
        [1_000_500.0, 1_000_400.0, 1_000_200.0])
    assert view["summary"]["n"] == 3
    assert view["summary"]["win_rate"] == pytest.approx(1 / 3)
    assert view["max_drawdown"] == pytest.approx(300.0)   # 1_000_500 → 1_000_200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_web.py -v`
Expected: FAIL — `ImportError: cannot import name 'paper' from 'journal.web'`.

- [ ] **Step 3: Write the implementation**

Create `src/journal/web/paper.py`:

```python
"""Paper trading orchestration — the impure glue between the pure evaluator
(`domain/paper_eval`), the pure store (`store/paper_store`), and the latest tick
the daemon left in `live_quotes`. Never the bridge (M9 boundary): the web reads
prices from the DB or refuses.

Money is USC and every payload says so. R is unit-free. An unknown is `None` all
the way to the browser — a 0 equity would read as a wiped account.
"""
from __future__ import annotations

import sqlite3

from ..analytics.report import sequence_stats
from ..domain import paper_eval as pe
from ..domain.sim_stats import summary as sim_summary
from ..store import live_store, paper_store

CURRENCY = "USC"


class PaperError(Exception):
    """A refusal the human should read. Routes turn it into a 400."""


def _row(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}


def _specs(conn: sqlite3.Connection, symbol: str) -> pe.Specs | None:
    r = conn.execute(
        "SELECT tick_size, tick_value, contract_size, currency_profit "
        "FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if r is None or r["tick_size"] in (None, 0) or r["tick_value"] in (None, 0):
        return None
    return pe.Specs(tick_size=float(r["tick_size"]), tick_value=float(r["tick_value"]),
                    contract_size=float(r["contract_size"] or 1.0),
                    currency_profit=r["currency_profit"] or "")


def _quote(conn: sqlite3.Connection, symbol: str) -> pe.Quote | None:
    r = live_store.read_quote(conn, symbol)
    if r is None:
        return None
    return pe.Quote(symbol=symbol, bid=float(r["bid"]), ask=float(r["ask"]),
                    time_msc=int(r["tick_msc"]))


def _state(row: sqlite3.Row) -> pe.PaperPos:
    return pe.PaperPos(
        id=row["id"], symbol=row["symbol"], direction=row["direction"],
        order_kind=row["order_kind"], request_price=row["request_price"],
        volume=row["volume"], sl=row["sl"] or 0.0, tp=row["tp"] or 0.0,
        status=row["status"], entry_price=row["entry_price"],
        entry_msc=row["entry_msc"], expires_msc=row["expires_msc"],
    )


def create_account(conn: sqlite3.Connection, *, name: str, initial_balance: float,
                   leverage: int, stopout_pct: float) -> dict:
    if not name or not name.strip():
        raise PaperError("Nama akun wajib diisi.")
    if initial_balance <= 0:
        raise PaperError("Balance awal harus lebih besar dari 0 (USC).")
    if leverage <= 0:
        raise PaperError("Leverage harus lebih besar dari 0.")
    if stopout_pct < 0:
        raise PaperError("Stop-out level tidak boleh negatif.")
    try:
        account_id = paper_store.create_account(
            conn, name=name.strip(), initial_balance=float(initial_balance),
            leverage=int(leverage), stopout_pct=float(stopout_pct),
        )
    except ValueError as e:
        raise PaperError(str(e)) from e
    return _row(paper_store.get_account(conn, account_id))


def list_accounts_view(conn: sqlite3.Connection,
                       status: str | None = None) -> list[dict]:
    return [_row(r) for r in paper_store.list_accounts(conn, status)]


def archive_account(conn: sqlite3.Connection, account_id: int) -> dict:
    if paper_store.get_account(conn, account_id) is None:
        raise PaperError(f"Tidak ada akun paper {account_id}.")
    paper_store.archive_account(conn, account_id)
    return _row(paper_store.get_account(conn, account_id))


def account_view(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """Everything one panel needs: the account, a marked header, the live rows,
    the closed history, the ungated summary, and the realized equity curve."""
    account = paper_store.get_account(conn, account_id)
    if account is None:
        return None

    rows = paper_store.list_positions(conn, account_id)
    open_rows = [r for r in rows if r["status"] == "open"]
    pending_rows = [r for r in rows if r["status"] == "pending"]
    closed_rows = [r for r in rows if r["status"] == "closed"]

    symbols = {r["symbol"] for r in open_rows}
    quotes = {s: q for s in symbols if (q := _quote(conn, s)) is not None}
    specs = {s: sp for s in symbols if (sp := _specs(conn, s)) is not None}

    state = pe.account_state(
        [_state(r) for r in open_rows], quotes, specs,
        balance=float(account["balance"]), leverage=int(account["leverage"]),
    )

    # Reuse the report's own drawdown: it is pure, it already starts its peak at
    # the account's start, and it reads `close_time_msc` — so alias `exit_msc`.
    seq_rows = conn.execute(
        "SELECT exit_msc AS close_time_msc, net_profit FROM paper_positions "
        "WHERE account_id = ? AND status = 'closed' AND net_profit IS NOT NULL",
        (account_id,),
    ).fetchall()
    _, max_dd, _, _ = sequence_stats(seq_rows)

    balance = float(account["initial_balance"])
    curve = []
    for r in sorted((r for r in closed_rows if r["exit_msc"] is not None),
                    key=lambda r: r["exit_msc"]):
        if r["net_profit"] is None:
            continue
        balance += float(r["net_profit"])
        curve.append({"exit_msc": r["exit_msc"], "balance": balance,
                      "position_id": r["id"], "symbol_base": r["symbol_base"]})

    return {
        "account": _row(account),
        "header": {
            "currency": CURRENCY,
            "balance": float(account["balance"]),
            "equity": state.equity,
            "margin": state.margin,
            "free_margin": state.free_margin,
            "margin_level": state.margin_level,
            "floating": state.floating,
            "leverage": int(account["leverage"]),
            "stopout_pct": float(account["stopout_pct"]),
        },
        "open": [
            {**_row(r),
             "floating": (pe.floating_usc(_state(r), quotes[r["symbol"]],
                                          specs[r["symbol"]])
                          if r["symbol"] in quotes and r["symbol"] in specs
                          else None)}
            for r in open_rows
        ],
        "pending": [_row(r) for r in pending_rows],
        "closed": [_row(r) for r in closed_rows],
        "summary": sim_summary(closed_rows),
        "max_drawdown": max_dd,
        "equity_curve": curve,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_web.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/paper.py tests/test_paper_web.py
git commit -m "feat(paper): accounts and a marked account view that never prints a bare number"
```

---

### Task 11: Placing an order

**Files:**
- Modify: `src/journal/web/paper.py`
- Test: `tests/test_paper_web.py`

**Interfaces:**
- Consumes: `commands.check_volume`, `commands.check_level`, `commands.CommandError` (Task 9); `risk.volume_for_risk`, `risk.floor_to_step`; `execute.FEED_STALE_MS`; `domain.symbols.to_base`.
- Produces: `place_order(conn, account_id, *, symbol, direction, kind="market", volume=None, risk_pct=None, price=None, sl=0.0, tp=0.0, expires_msc=None, now_msc=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paper_web.py`:

```python
def _order(conn, account, **kw):
    body = dict(symbol="XAUUSDc", direction="buy", kind="market", volume=0.10,
                sl=4025.0, tp=0.0)
    body.update(kw)
    return paper.place_order(conn, account, **body)


def test_a_market_buy_fills_at_the_ask_immediately(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    assert out["status"] == "open"
    assert out["entry_price"] == pytest.approx(4030.5)
    assert out["sl_initial"] == pytest.approx(4025.0)
    assert out["symbol_base"] == "XAUUSD"


def test_a_stale_quote_refuses_the_order_instead_of_resizing_it(conn, account):
    live_store.upsert_quote(conn, "XAUUSDc", bid=4030.0, ask=4030.5,
                            tick_msc=1_000, now_msc=now_ms() - 60_000)
    with pytest.raises(paper.PaperError, match="basi"):
        _order(conn, account)


def test_an_order_on_a_symbol_with_no_quote_at_all_is_refused(conn, account):
    with pytest.raises(paper.PaperError, match="belum ada harga"):
        _order(conn, account)


def test_a_pending_order_needs_no_quote_and_stays_pending(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account, kind="limit", price=4025.0, sl=4020.0)
    assert out["status"] == "pending"
    assert out["entry_price"] is None
    assert out["request_price"] == pytest.approx(4025.0)


def test_volume_and_risk_pct_together_are_refused_and_so_is_neither(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="salah satu"):
        _order(conn, account, volume=0.10, risk_pct=1.0)
    with pytest.raises(paper.PaperError, match="salah satu"):
        _order(conn, account, volume=None, risk_pct=None)


def test_risk_pct_sizes_from_the_accounts_own_equity(conn, account):
    _fresh_quote(conn)
    # 1% of 1_000_000 USC = 10_000 USC at risk. Entry 4030.5, stop 4025.0 is
    # 5.5 USD = 550 USC per 0.01 lot, so 10_000 / 55_000 per lot ≈ 0.18 lot,
    # floored to the 0.01 step.
    out = _order(conn, account, volume=None, risk_pct=1.0)
    assert out["volume"] == pytest.approx(0.18)


def test_risk_pct_sizing_needs_a_stop_to_size_against(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="SL"):
        _order(conn, account, volume=None, risk_pct=1.0, sl=0.0)


def test_a_stop_on_the_wrong_side_is_refused_by_the_shared_validator(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="BAWAH"):
        _order(conn, account, sl=4040.0)


def test_a_volume_off_the_brokers_step_is_refused(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="kelipatan"):
        _order(conn, account, volume=0.015)


def test_an_order_larger_than_the_free_margin_is_refused(conn, account):
    _fresh_quote(conn)
    small = paper.create_account(conn, name="Tipis", initial_balance=100.0,
                                 leverage=500, stopout_pct=20.0)["id"]
    with pytest.raises(paper.PaperError, match="margin"):
        _order(conn, small, volume=1.00)


def test_an_archived_account_takes_no_new_orders(conn, account):
    _fresh_quote(conn)
    paper.archive_account(conn, account)
    with pytest.raises(paper.PaperError, match="diarsipkan"):
        _order(conn, account)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_web.py -k order -v`
Expected: FAIL — `AttributeError: module 'journal.web.paper' has no attribute 'place_order'`.

- [ ] **Step 3: Write the implementation**

Append to `src/journal/web/paper.py`:

```python
from ..domain import commands as cmd
from ..domain import risk
from ..domain.symbols import to_base
from ..execute import FEED_STALE_MS
from ..store.db import now_ms


def _require_active(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    account = paper_store.get_account(conn, account_id)
    if account is None:
        raise PaperError(f"Tidak ada akun paper {account_id}.")
    if account["status"] != "active":
        raise PaperError("Akun ini sudah diarsipkan — buka akun lain untuk trading.")
    return account


def _fresh_quote(conn: sqlite3.Connection, symbol: str,
                 now_msc: int) -> pe.Quote:
    """The latest tick, or a refusal. A stale reference price does not fail
    loudly — it silently resizes the position, which is why the guard is here and
    not only in the browser. Same threshold as a real open (`FEED_STALE_MS`)."""
    row = live_store.read_quote(conn, symbol)
    if row is None:
        raise PaperError(
            f"Belum ada harga untuk {symbol} — `journal live` belum pernah "
            f"menyimpan tick simbol ini. Order ditolak, bukan ditebak."
        )
    age = now_msc - int(row["updated_msc"])
    if age >= FEED_STALE_MS:
        raise PaperError(
            f"Harga {symbol} basi {age / 1000:.0f}s — `journal live` tidak "
            f"menyuapi feed. Order ditolak."
        )
    return pe.Quote(symbol=symbol, bid=float(row["bid"]), ask=float(row["ask"]),
                    time_msc=int(row["tick_msc"]))


def _spec_row(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row is None:
        raise PaperError(
            f"Spesifikasi {symbol} belum diketahui — jalankan `journal sync`."
        )
    return row


def place_order(conn: sqlite3.Connection, account_id: int, *, symbol: str,
                direction: str, kind: str = "market", volume: float | None = None,
                risk_pct: float | None = None, price: float | None = None,
                sl: float = 0.0, tp: float = 0.0,
                expires_msc: int | None = None,
                now_msc: int | None = None) -> dict:
    """Open a market position immediately, or park a pending limit/stop order.

    Sizing takes `volume` OR `risk_pct`, never both and never neither: a route
    that picks for you is a route that sizes someone's position by guessing.
    """
    now = now_ms() if now_msc is None else now_msc
    account = _require_active(conn, account_id)
    if direction not in ("buy", "sell"):
        raise PaperError("Arah harus 'buy' atau 'sell'.")
    if kind not in ("market", "limit", "stop"):
        raise PaperError("Jenis order harus 'market', 'limit', atau 'stop'.")
    if (volume is None) == (risk_pct is None):
        raise PaperError("Isi salah satu: volume ATAU risk_pct, tidak dua-duanya.")

    quote = _fresh_quote(conn, symbol, now)
    spec_row = _spec_row(conn, symbol)
    specs = _specs(conn, symbol)
    if specs is None:
        raise PaperError(f"Spesifikasi harga {symbol} belum lengkap.")

    if kind == "market":
        reference = pe.entry_side(direction, quote)
    else:
        if price is None:
            raise PaperError("Order limit/stop wajib menyebut harga pemicu.")
        reference = float(price)

    if risk_pct is not None:
        if risk_pct <= 0:
            raise PaperError("risk_pct harus lebih besar dari 0.")
        if sl is None or abs(sl) < 1e-9:
            raise PaperError(
                "Sizing dari risiko butuh SL — tanpa jarak stop tidak ada "
                "risiko untuk dibagi."
            )
        state = pe.account_state(
            [_state(r) for r in paper_store.list_positions(
                conn, account_id, statuses=("open",))],
            {symbol: quote}, {symbol: specs},
            balance=float(account["balance"]), leverage=int(account["leverage"]),
        )
        equity = state.equity
        if equity is None:
            raise PaperError(
                "Equity akun belum bisa dihitung (harga posisi lain belum ada) "
                "— sizing dari risiko ditolak."
            )
        budget = equity * risk_pct / 100.0
        raw = risk.volume_for_risk(reference, sl, specs.tick_size,
                                   specs.tick_value, budget)
        volume = risk.floor_to_step(raw, spec_row["volume_step"])
        if volume is None or volume <= 0:
            raise PaperError(
                "Risiko yang diminta lebih kecil dari satu step volume broker."
            )

    try:
        cmd.check_volume("open", None, spec_row, volume)
        cmd.check_level("sl", sl, direction, reference, spec_row)
        cmd.check_level("tp", tp, direction, reference, spec_row)
    except cmd.CommandError as e:
        raise PaperError(str(e)) from e

    if kind == "market":
        need = pe.margin_usc(volume, reference, specs, int(account["leverage"]))
        state = pe.account_state(
            [_state(r) for r in paper_store.list_positions(
                conn, account_id, statuses=("open",))],
            {symbol: quote}, {symbol: specs},
            balance=float(account["balance"]), leverage=int(account["leverage"]),
        )
        if need is None or state.free_margin is None:
            raise PaperError(
                "Margin tidak bisa dihitung untuk simbol ini — order ditolak, "
                "bukan diasumsikan aman."
            )
        if need > state.free_margin:
            raise PaperError(
                f"Butuh margin {need:.2f} {CURRENCY}, free margin hanya "
                f"{state.free_margin:.2f} {CURRENCY}."
            )

    status = "open" if kind == "market" else "pending"
    pid = paper_store.insert_position(
        conn, account_id=account_id, symbol=symbol, symbol_base=to_base(symbol),
        direction=direction, order_kind=kind,
        request_price=(None if kind == "market" else reference),
        volume=float(volume), sl=float(sl or 0.0), tp=float(tp or 0.0),
        status=status,
        entry_price=(reference if kind == "market" else None),
        entry_msc=(quote.time_msc if kind == "market" else None),
        expires_msc=expires_msc,
    )
    return _row(paper_store.get_position(conn, pid))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_web.py -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add src/journal/web/paper.py tests/test_paper_web.py
git commit -m "feat(paper): place an order sized by lots or by a share of the account's own equity"
```

---

### Task 12: Closing, modifying, reversing

**Files:**
- Modify: `src/journal/web/paper.py`
- Test: `tests/test_paper_web.py`

**Interfaces:**
- Consumes: `paper_store.split_for_partial`, `mark_close`, `set_sltp`, `update_status`, `add_balance`; `replay_eval.net_profit_usc`, `r_multiple`; `domain/excursion.compute_excursion`; `store/candles_store.load_bars`.
- Produces:
  - `close_position(conn, position_id, *, volume=None, reason="manual", now_msc=None) -> dict`
  - `modify_sltp(conn, position_id, *, sl=None, tp=None) -> dict`
  - `reverse_position(conn, position_id, *, now_msc=None) -> dict`
  - `cancel_pending(conn, position_id) -> dict`
  - `close_all(conn, account_id, *, now_msc=None) -> dict` returning `{"closed": [...], "cancelled": [...]}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paper_web.py`:

```python
def test_a_manual_close_credits_the_balance_at_the_exit_side(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)                        # buy at 4030.5
    _fresh_quote(conn, bid=4040.0, ask=4040.5)
    closed = paper.close_position(conn, out["id"])
    # 9.5 USD on 0.1 oz = 950 USC, taken at the bid, not the mid.
    assert closed["net_profit"] == pytest.approx(950.0)
    assert closed["exit_reason"] == "manual"
    assert paper_store.get_account(conn, account)["balance"] == pytest.approx(
        1_000_950.0)


def test_a_partial_close_leaves_a_smaller_open_parent_and_a_closed_child(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    _fresh_quote(conn, bid=4040.0, ask=4040.5)
    child = paper.close_position(conn, out["id"], volume=0.04)
    parent = paper_store.get_position(conn, out["id"])
    assert child["volume"] == pytest.approx(0.04)
    assert child["status"] == "closed"
    assert child["net_profit"] == pytest.approx(380.0)     # 9.5 USD on 0.04 oz
    assert parent["volume"] == pytest.approx(0.06)
    assert parent["status"] == "open"


def test_a_partial_close_of_the_whole_volume_closes_it_outright(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    closed = paper.close_position(conn, out["id"], volume=0.10)
    assert closed["status"] == "closed"
    assert paper_store.get_position(conn, out["id"])["status"] == "closed"


def test_moving_the_stop_never_rewrites_the_stop_it_was_born_with(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    moved = paper.modify_sltp(conn, out["id"], sl=4029.0)
    assert moved["sl"] == pytest.approx(4029.0)
    assert moved["sl_initial"] == pytest.approx(4025.0)


def test_clearing_a_level_is_zero_and_leaving_it_alone_is_none(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account, tp=4050.0)
    kept = paper.modify_sltp(conn, out["id"], sl=None, tp=0.0)
    assert kept["sl"] == pytest.approx(4025.0)        # untouched
    assert kept["tp"] == pytest.approx(0.0)          # cleared


def test_a_reverse_closes_the_old_row_and_opens_the_other_way(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    result = paper.reverse_position(conn, out["id"])
    old = paper_store.get_position(conn, out["id"])
    assert old["status"] == "closed" and old["exit_reason"] == "reverse"
    assert result["direction"] == "sell"
    assert result["volume"] == pytest.approx(0.10)
    assert result["sl"] == pytest.approx(0.0) and result["tp"] == pytest.approx(0.0)


def test_cancelling_a_pending_order_marks_it_cancelled_and_keeps_the_row(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account, kind="limit", price=4025.0, sl=4020.0)
    cancelled = paper.cancel_pending(conn, out["id"])
    assert cancelled["status"] == "cancelled"


def test_cancelling_an_open_position_is_refused(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    with pytest.raises(paper.PaperError, match="pending"):
        paper.cancel_pending(conn, out["id"])


def test_close_all_reaches_every_symbol_and_the_pending_orders_too(conn, account):
    _fresh_quote(conn)
    live_store.upsert_quote(conn, "BTCUSDc", bid=50_000.0, ask=50_010.0,
                            tick_msc=now_ms(), now_msc=now_ms())
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, digits, point, tick_size, "
        "tick_value, contract_size, currency_profit, fetched_at, volume_min, "
        "volume_max, volume_step, stops_level, freeze_level, trade_mode, "
        "filling_mode) VALUES ('BTCUSDc', 'BTCUSD', 2, 0.01, 0.01, 0.1, 1.0, "
        "'USD', 1, 0.01, 100.0, 0.01, 0, 0, 4, 1)"
    )
    conn.commit()
    a = _order(conn, account)
    b = _order(conn, account, symbol="BTCUSDc", sl=49_000.0)
    c = _order(conn, account, kind="limit", price=4000.0, sl=3_990.0)

    out = paper.close_all(conn, account)

    assert sorted(out["closed"]) == sorted([a["id"], b["id"]])
    assert out["cancelled"] == [c["id"]]
    assert paper_store.list_positions(conn, account, statuses=("open", "pending")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paper_web.py -k "close or modify or reverse or cancel" -v`
Expected: FAIL — `AttributeError: module 'journal.web.paper' has no attribute 'close_position'`.

- [ ] **Step 3: Write the implementation**

Append to `src/journal/web/paper.py`:

```python
from ..domain import replay_eval as rev
from ..domain.excursion import compute_excursion
from ..store import candles_store as cs


def _excursion(conn: sqlite3.Connection, row: sqlite3.Row,
               exit_msc: int) -> tuple:
    """MAE/MFE in price and in R from the cached M1 bars the position lived
    through — the same pure helper and the same call shape `web/training.py`
    uses. All four are `None` when the bars are not cached: an excursion we
    cannot see is unknown, not zero (rule 4).

    `compute_excursion` takes `(time_msc, low, high)` tuples and returns
    `(mae, mfe)` in PRICE. R comes from dividing by the risk distance, and the
    distance is measured against `sl_initial` — the stop the position was born
    with — so moving the stop later cannot rewrite its own MAE in R.
    """
    if row["entry_msc"] is None or row["entry_price"] is None:
        return (None, None, None, None)
    bars = cs.load_bars(conn, row["symbol"], "M1", row["entry_msc"], exit_msc)
    if not bars:
        return (None, None, None, None)
    mae, mfe = compute_excursion(
        [(b.time_msc, b.low, b.high) for b in bars],
        row["entry_msc"], exit_msc, float(row["entry_price"]), row["direction"],
    )
    mae_r = mfe_r = None
    sl0 = row["sl_initial"]
    risk = abs(float(row["entry_price"]) - float(sl0)) if sl0 else None
    if risk:                      # truthy: not None and not 0.0 (Trap 6 shape)
        if mae is not None:
            mae_r = mae / risk
        if mfe is not None:
            mfe_r = mfe / risk
    return (mae, mfe, mae_r, mfe_r)


def _close_row(conn: sqlite3.Connection, row: sqlite3.Row, *, exit_price: float,
               exit_msc: int, reason: str, specs: pe.Specs) -> dict:
    """Resolve one row: money, R against the stop it was born with, excursion,
    and the account's realized balance moved by exactly that money."""
    net = rev.net_profit_usc(row["direction"], float(row["entry_price"]),
                             exit_price, float(row["volume"]),
                             specs.tick_size, specs.tick_value)
    r = None
    if row["sl_initial"] is not None:
        r = rev.r_multiple(row["direction"], float(row["entry_price"]),
                           exit_price, float(row["sl_initial"]))
    mae, mfe, mae_r, mfe_r = _excursion(conn, row, exit_msc)
    paper_store.mark_close(conn, row["id"], exit_msc=exit_msc,
                           exit_price=exit_price, exit_reason=reason,
                           net_profit=net, r_multiple=r, mae=mae, mfe=mfe,
                           mae_r=mae_r, mfe_r=mfe_r)
    paper_store.add_balance(conn, row["account_id"], net)
    return _row(paper_store.get_position(conn, row["id"]))


def close_position(conn: sqlite3.Connection, position_id: int, *,
                   volume: float | None = None, reason: str = "manual",
                   now_msc: int | None = None) -> dict:
    """Close a position, in full or in part, at the current exit side.

    A partial close SPLITS: the closed slice becomes its own complete row. A
    `volume` equal to (or above) what is held closes the whole position rather
    than refusing — the human asked to be flat, and a refusal there is a trap.
    """
    now = now_ms() if now_msc is None else now_msc
    row = paper_store.get_position(conn, position_id)
    if row is None:
        raise PaperError(f"Tidak ada posisi paper {position_id}.")
    if row["status"] != "open":
        raise PaperError("Hanya posisi terbuka yang bisa ditutup.")

    quote = _fresh_quote(conn, row["symbol"], now)
    specs = _specs(conn, row["symbol"])
    if specs is None:
        raise PaperError(f"Spesifikasi {row['symbol']} belum lengkap.")
    exit_price = pe.exit_side(row["direction"], quote)

    if volume is not None and volume < float(row["volume"]) - 1e-9:
        child_id = paper_store.split_for_partial(conn, position_id, float(volume))
        child = paper_store.get_position(conn, child_id)
        return _close_row(conn, child, exit_price=exit_price, exit_msc=now,
                          reason=reason, specs=specs)

    return _close_row(conn, row, exit_price=exit_price, exit_msc=now,
                      reason=reason, specs=specs)


def modify_sltp(conn: sqlite3.Connection, position_id: int, *,
                sl: float | None = None, tp: float | None = None) -> dict:
    """Move the live stop/target. `None` leaves a side alone, `0.0` clears it
    (rule 4). `sl_initial` is never rewritten — R depends on it."""
    row = paper_store.get_position(conn, position_id)
    if row is None:
        raise PaperError(f"Tidak ada posisi paper {position_id}.")
    if row["status"] not in ("open", "pending"):
        raise PaperError("Hanya posisi terbuka atau order pending yang bisa diubah.")

    reference = row["entry_price"] if row["status"] == "open" else row["request_price"]
    spec_row = _spec_row(conn, row["symbol"])
    try:
        cmd.check_level("sl", sl, row["direction"], reference, spec_row)
        cmd.check_level("tp", tp, row["direction"], reference, spec_row)
    except cmd.CommandError as e:
        raise PaperError(str(e)) from e

    paper_store.set_sltp(
        conn, position_id,
        sl=(row["sl"] if sl is None else float(sl)),
        tp=(row["tp"] if tp is None else float(tp)),
    )
    return _row(paper_store.get_position(conn, position_id))


def reverse_position(conn: sqlite3.Connection, position_id: int, *,
                     now_msc: int | None = None) -> dict:
    """Close in full and open the same volume the other way.

    The closed row is marked `'reverse'`, which is what makes it legible later as
    a deliberate flip and not a manual exit that happened to be followed by an
    entry. No SL/TP is carried across: the old levels belonged to the old side.
    """
    now = now_ms() if now_msc is None else now_msc
    row = paper_store.get_position(conn, position_id)
    if row is None:
        raise PaperError(f"Tidak ada posisi paper {position_id}.")
    if row["status"] != "open":
        raise PaperError("Hanya posisi terbuka yang bisa dibalik.")

    volume = float(row["volume"])
    opposite = "sell" if row["direction"] == "buy" else "buy"
    close_position(conn, position_id, reason="reverse", now_msc=now)
    return place_order(conn, row["account_id"], symbol=row["symbol"],
                       direction=opposite, kind="market", volume=volume,
                       sl=0.0, tp=0.0, now_msc=now)


def cancel_pending(conn: sqlite3.Connection, position_id: int) -> dict:
    row = paper_store.get_position(conn, position_id)
    if row is None:
        raise PaperError(f"Tidak ada posisi paper {position_id}.")
    if row["status"] != "pending":
        raise PaperError("Hanya order pending yang bisa dibatalkan.")
    paper_store.update_status(conn, position_id, "cancelled")
    return _row(paper_store.get_position(conn, position_id))


def close_all(conn: sqlite3.Connection, account_id: int, *,
              now_msc: int | None = None) -> dict:
    """Flatten the WHOLE account — every symbol, and the pending orders too.
    Not just the symbol currently on the chart."""
    now = now_ms() if now_msc is None else now_msc
    _require_active(conn, account_id)
    closed: list[int] = []
    cancelled: list[int] = []
    for row in paper_store.list_positions(conn, account_id,
                                          statuses=("open", "pending")):
        if row["status"] == "pending":
            cancel_pending(conn, row["id"])
            cancelled.append(row["id"])
        else:
            close_position(conn, row["id"], now_msc=now)
            closed.append(row["id"])
    return {"closed": closed, "cancelled": cancelled}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paper_web.py -v`
Expected: PASS, 27 tests.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/web/paper.py tests/test_paper_web.py
git commit -m "feat(paper): close, partial close, modify, reverse, cancel, and flatten"
```

---

### Task 13: Routes and the mode preference

**Files:**
- Modify: `src/journal/web/app.py`, `src/journal/store/prefs_store.py`
- Test: `tests/test_prefs_store.py` (exists; append)

**Interfaces:**
- Consumes: everything from Tasks 10-12.
- Produces: the ten `/api/paper/*` routes, plus `prefs_store.get_paper_prefs(conn) -> Any | None` and `set_paper_prefs(conn, prefs) -> int` under key `paper`.

- [ ] **Step 1: Write the failing preference test**

Append to `tests/test_prefs_store.py`:

```python
def test_paper_prefs_round_trip_under_their_own_key(conn):
    assert prefs_store.get_paper_prefs(conn) is None
    prefs_store.set_paper_prefs(conn, {"mode": "paper", "accountId": 3})
    assert prefs_store.get_paper_prefs(conn) == {"mode": "paper", "accountId": 3}
    # Its own key: turning paper on must not disturb the chart's appearance.
    assert prefs_store.get_chart_prefs(conn) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_prefs_store.py -k paper -v`
Expected: FAIL — `AttributeError: module 'journal.store.prefs_store' has no attribute 'get_paper_prefs'`.

- [ ] **Step 3: Add the preference pair**

Append to `src/journal/store/prefs_store.py`, following the four pairs already
there:

```python
def get_paper_prefs(conn: sqlite3.Connection) -> Any | None:
    """Paper-trading UI state: `{mode: 'real'|'paper', accountId: int|null}`.
    Its OWN key, not folded into the chart blob — `ChartSettings` is versioned
    and carries a legacy-object migration, and which paper account is selected is
    not chart appearance."""
    raw = get_pref(conn, "paper")
    return None if raw is None else json.loads(raw)


def set_paper_prefs(conn: sqlite3.Connection, prefs: Any) -> int:
    return set_pref(conn, "paper", json.dumps(prefs), now_ms())
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_prefs_store.py -v`
Expected: PASS.

- [ ] **Step 5: Add the routes**

In `src/journal/web/app.py`, import the module beside the existing web imports
(`from . import paper`) and add this block after the training block, matching its
style exactly — `Body(...)` parameters, `Depends(get_conn)`, and `api.to_jsonable`
on the way out:

```python
    # --------------------------------------------------------- paper trading
    # A virtual account: balance, leverage and stop-out the human sets. Fills
    # come from the tick `journal live` stored in `live_quotes`; the web never
    # touches the bridge. Nothing here reaches deals_raw or trades (rule 2).
    @app.get("/api/paper/accounts")
    def api_paper_accounts(status: str | None = None,
                           conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(api.to_jsonable(paper.list_accounts_view(conn, status)))

    @app.post("/api/paper/accounts")
    def api_paper_create(
        name: str = Body(...),
        initial_balance: float = Body(...),
        leverage: int = Body(...),
        stopout_pct: float = Body(...),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            out = paper.create_account(conn, name=name,
                                       initial_balance=initial_balance,
                                       leverage=leverage, stopout_pct=stopout_pct)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.post("/api/paper/accounts/{account_id}/archive")
    def api_paper_archive(account_id: int,
                          conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = paper.archive_account(conn, account_id)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.get("/api/paper/accounts/{account_id}")
    def api_paper_account(account_id: int,
                          conn: sqlite3.Connection = Depends(get_conn)):
        view = paper.account_view(conn, account_id)
        if view is None:
            return JSONResponse({"error": f"no paper account {account_id}"},
                                status_code=404)
        return JSONResponse(api.to_jsonable(view))

    @app.post("/api/paper/accounts/{account_id}/orders")
    def api_paper_order(
        account_id: int,
        symbol: str = Body(...),
        direction: str = Body(...),
        kind: str = Body("market"),
        volume: float | None = Body(None),
        risk_pct: float | None = Body(None),
        price: float | None = Body(None),
        sl: float = Body(0.0),
        tp: float = Body(0.0),
        expires_msc: int | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            out = paper.place_order(conn, account_id, symbol=symbol,
                                    direction=direction, kind=kind, volume=volume,
                                    risk_pct=risk_pct, price=price, sl=sl, tp=tp,
                                    expires_msc=expires_msc)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.patch("/api/paper/positions/{position_id}")
    def api_paper_modify(
        position_id: int,
        sl: float | None = Body(None),
        tp: float | None = Body(None),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        try:
            out = paper.modify_sltp(conn, position_id, sl=sl, tp=tp)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.post("/api/paper/positions/{position_id}/close")
    def api_paper_close(position_id: int,
                        volume: float | None = Body(None, embed=True),
                        conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = paper.close_position(conn, position_id, volume=volume)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.post("/api/paper/positions/{position_id}/reverse")
    def api_paper_reverse(position_id: int,
                          conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = paper.reverse_position(conn, position_id)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.delete("/api/paper/positions/{position_id}")
    def api_paper_cancel(position_id: int,
                         conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = paper.cancel_pending(conn, position_id)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.post("/api/paper/accounts/{account_id}/close_all")
    def api_paper_close_all(account_id: int,
                            conn: sqlite3.Connection = Depends(get_conn)):
        try:
            out = paper.close_all(conn, account_id)
        except paper.PaperError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(api.to_jsonable(out))

    @app.get("/api/prefs/paper")
    def api_paper_prefs_get(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse({"prefs": prefs_store.get_paper_prefs(conn)})

    @app.put("/api/prefs/paper")
    def api_paper_prefs_set(prefs: dict = Body(...),
                            conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse({"updated_ms": prefs_store.set_paper_prefs(conn, prefs)})
```

- [ ] **Step 6: Prove the app still builds and the routes exist**

Run:

```bash
uv run python -c "
from journal.web.app import create_app
paths = sorted(r.path for r in create_app('data/journal.db').routes if 'paper' in r.path)
print('\n'.join(paths))
"
```

Expected: twelve paths listed, including `/api/paper/accounts/{account_id}/orders` and `/api/prefs/paper`. An import error here means a missing import at the top of `app.py`.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/journal/web/app.py src/journal/store/prefs_store.py tests/test_prefs_store.py
git commit -m "feat(paper): the /api/paper routes and the mode preference"
```

---

## Phase 4 — the frontend

### Task 14: The client and the hook

**Files:**
- Create: `frontend/src/lib/paperApi.ts`, `frontend/src/hooks/usePaperAccount.ts`
- Modify: `frontend/src/lib/types.ts` (add the paper types beside the existing live types)
- Test: `frontend/src/hooks/usePaperAccount.test.ts`

**Interfaces:**
- Consumes: `postJson`, `patchJson` from `lib/api`; `useApi` from `lib/api`.
- Produces:
  - `PaperAccount`, `PaperPosition`, `PaperHeader`, `PaperAccountView` types
  - `listAccounts`, `createAccount`, `archiveAccount`, `getAccount`, `placeOrder`, `modifySltp`, `closePosition`, `reversePosition`, `cancelPending`, `closeAll`
  - `usePaperAccount(accountId: number | null, pollMs?: number)` returning `{ view, error, refresh }`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/usePaperAccount.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { usePaperAccount } from "./usePaperAccount";

const view = {
  account: { id: 1, name: "Scalping XAU", balance: 1_000_000, leverage: 500 },
  header: { currency: "USC", balance: 1_000_000, equity: 1_000_000, margin: 0,
            free_margin: 1_000_000, margin_level: null, floating: 0 },
  open: [], pending: [], closed: [], summary: { n: 0 },
  max_drawdown: null, equity_curve: [],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => view })));
});
afterEach(() => vi.unstubAllGlobals());

describe("usePaperAccount", () => {
  it("fetches nothing at all while no account is selected", async () => {
    renderHook(() => usePaperAccount(null));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("loads the selected account", async () => {
    const { result } = renderHook(() => usePaperAccount(1));
    await waitFor(() => expect(result.current.view?.account.name).toBe("Scalping XAU"));
    expect(result.current.view?.header.currency).toBe("USC");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix frontend test -- usePaperAccount`
Expected: FAIL — cannot resolve `./usePaperAccount`.

- [ ] **Step 3: Write the types**

Append to `frontend/src/lib/types.ts`:

```ts
// Paper trading. Money is USC — `header.currency` is the label to print, and
// every one of these numbers can be null, meaning UNKNOWN (never 0).
export interface PaperAccount {
  id: number; name: string; initial_balance: number; balance: number;
  leverage: number; stopout_pct: number; status: "active" | "archived";
  created_at_msc: number; archived_at_msc: number | null;
}

export interface PaperHeader {
  currency: string; balance: number;
  equity: number | null; margin: number | null; free_margin: number | null;
  margin_level: number | null; floating: number | null;
  leverage: number; stopout_pct: number;
}

export interface PaperPosition {
  id: number; account_id: number; symbol: string; symbol_base: string;
  direction: "buy" | "sell"; order_kind: "market" | "limit" | "stop";
  request_price: number | null; volume: number; sl: number; tp: number;
  sl_initial: number | null; expires_msc: number | null;
  status: "pending" | "open" | "closed" | "cancelled" | "expired";
  requested_msc: number; entry_msc: number | null; entry_price: number | null;
  exit_msc: number | null; exit_price: number | null;
  exit_reason: "tp" | "sl" | "manual" | "stopout" | "reverse" | null;
  net_profit: number | null; r_multiple: number | null;
  mae_r: number | null; mfe_r: number | null; parent_id: number | null;
  floating?: number | null;
}

export interface PaperSummary {
  n: number; win_rate: number | null; avg_r: number | null; total_r: number;
  avg_mae_r: number | null; avg_mfe_r: number | null;
}

export interface PaperAccountView {
  account: PaperAccount; header: PaperHeader;
  open: PaperPosition[]; pending: PaperPosition[]; closed: PaperPosition[];
  summary: PaperSummary; max_drawdown: number | null;
  equity_curve: { exit_msc: number; balance: number; position_id: number;
                  symbol_base: string }[];
}
```

- [ ] **Step 4: Write the client**

Create `frontend/src/lib/paperApi.ts`:

```ts
// Typed fetch wrappers for /api/paper/*. The backend is authoritative for every
// fill, every margin figure and every refusal — this file only carries them.
import { postJson, patchJson } from "./api";
import type { PaperAccount, PaperAccountView, PaperPosition } from "./types";

export async function listAccounts(status?: "active" | "archived"): Promise<PaperAccount[]> {
  const r = await fetch(`/api/paper/accounts${status ? `?status=${status}` : ""}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as PaperAccount[];
}

export function createAccount(body: {
  name: string; initial_balance: number; leverage: number; stopout_pct: number;
}) {
  return postJson<PaperAccount>("/api/paper/accounts", body);
}

export function archiveAccount(id: number) {
  return postJson<PaperAccount>(`/api/paper/accounts/${id}/archive`, {});
}

export async function getAccount(id: number): Promise<PaperAccountView | null> {
  const r = await fetch(`/api/paper/accounts/${id}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error((await r.json()).error ?? `HTTP ${r.status}`);
  return (await r.json()) as PaperAccountView;
}

export function placeOrder(accountId: number, body: {
  symbol: string; direction: "buy" | "sell";
  kind?: "market" | "limit" | "stop";
  volume?: number | null; risk_pct?: number | null; price?: number | null;
  sl?: number; tp?: number; expires_msc?: number | null;
}) {
  return postJson<PaperPosition>(`/api/paper/accounts/${accountId}/orders`, body);
}

export function modifySltp(positionId: number, body: { sl?: number | null; tp?: number | null }) {
  return patchJson<PaperPosition>(`/api/paper/positions/${positionId}`, body);
}

export function closePosition(positionId: number, volume?: number) {
  return postJson<PaperPosition>(`/api/paper/positions/${positionId}/close`,
    volume === undefined ? {} : { volume });
}

export function reversePosition(positionId: number) {
  return postJson<PaperPosition>(`/api/paper/positions/${positionId}/reverse`, {});
}

export async function cancelPending(positionId: number): Promise<boolean> {
  const r = await fetch(`/api/paper/positions/${positionId}`, { method: "DELETE" });
  return r.ok;
}

export function closeAll(accountId: number) {
  return postJson<{ closed: number[]; cancelled: number[] }>(
    `/api/paper/accounts/${accountId}/close_all`, {});
}
```

- [ ] **Step 5: Write the hook**

Create `frontend/src/hooks/usePaperAccount.ts`:

```ts
import { useCallback, useEffect, useState } from "react";
import { getAccount } from "../lib/paperApi";
import type { PaperAccountView } from "../lib/types";

/** The selected paper account, polled at the same 2500 ms as /api/live. A null
 *  id fetches NOTHING: an unselected account has no cost.
 *
 *  Not `useApi` — its `path` is a required string with no skip mode, so routing a
 *  null id through it would fetch `/api/paper/accounts/null` every 2.5 seconds
 *  forever while paper is switched off. */
export function usePaperAccount(accountId: number | null, pollMs = 2500) {
  const [view, setView] = useState<PaperAccountView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (accountId == null) { setView(null); return; }
    try {
      setView(await getAccount(accountId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [accountId]);

  useEffect(() => {
    if (accountId == null) { setView(null); return; }
    void refresh();
    const t = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(t);
  }, [accountId, pollMs, refresh]);

  return { view, error, refresh };
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm --prefix frontend test -- usePaperAccount`
Expected: PASS, 2 tests.

- [ ] **Step 7: Typecheck**

Run: `npm --prefix frontend run build`
Expected: `tsc -b && vite build` clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/paperApi.ts frontend/src/lib/types.ts frontend/src/hooks/usePaperAccount.ts frontend/src/hooks/usePaperAccount.test.ts
git commit -m "feat(paper): typed client and polling hook for the paper account"
```

---

### Task 15: The account bar and the positions table

**Files:**
- Create: `frontend/src/components/PaperAccountBar.tsx`, `frontend/src/components/PaperPositions.tsx`
- Test: `frontend/src/components/PaperAccountBar.test.tsx`, `frontend/src/components/PaperPositions.test.tsx`

**Interfaces:**
- Consumes: `PaperAccountView`, `PaperHeader`, `PaperPosition` (Task 14); `lib/theme.ts` tokens; `lib/type.ts` roles.
- Produces:
  - `<PaperAccountBar header={PaperHeader} name={string} live={boolean} />`
  - `<PaperPositions view={PaperAccountView} chartSymbol={string} onClose onPartial onReverse onCancel onCloseAll />`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/PaperAccountBar.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import PaperAccountBar from "./PaperAccountBar";

const header = {
  currency: "USC", balance: 1_000_000, equity: 999_995, margin: 80.61,
  free_margin: 999_914.39, margin_level: 1_240_534, floating: -5,
  leverage: 500, stopout_pct: 20,
};

describe("PaperAccountBar", () => {
  it("prints every money figure with its unit, never a bare number", () => {
    render(<PaperAccountBar header={header} name="Scalping XAU" live />);
    expect(screen.getAllByText(/USC/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Scalping XAU/)).toBeTruthy();
  });

  it("says unknown rather than 0 when the feed never produced a quote", () => {
    render(<PaperAccountBar name="X" live
      header={{ ...header, equity: null, margin: null, free_margin: null,
                margin_level: null, floating: null }} />);
    expect(screen.getByLabelText("equity").textContent).toMatch(/—/);
    expect(screen.queryByLabelText("equity")!.textContent).not.toMatch(/\b0\b/);
  });

  it("warns that positions are unmonitored while the daemon is down", () => {
    render(<PaperAccountBar header={header} name="X" live={false} />);
    expect(screen.getByRole("status").textContent).toMatch(/tidak dipantau/i);
  });
});
```

Create `frontend/src/components/PaperPositions.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PaperPositions from "./PaperPositions";
import type { PaperAccountView, PaperPosition } from "../lib/types";

function pos(over: Partial<PaperPosition> = {}): PaperPosition {
  return {
    id: 1, account_id: 1, symbol: "XAUUSDc", symbol_base: "XAUUSD",
    direction: "buy", order_kind: "market", request_price: null, volume: 0.1,
    sl: 4025, tp: 0, sl_initial: 4025, expires_msc: null, status: "open",
    requested_msc: 1, entry_msc: 1, entry_price: 4030.5, exit_msc: null,
    exit_price: null, exit_reason: null, net_profit: null, r_multiple: null,
    mae_r: null, mfe_r: null, parent_id: null, floating: -5, ...over,
  };
}

const view = (over: Partial<PaperAccountView> = {}): PaperAccountView => ({
  account: { id: 1, name: "X", initial_balance: 1e6, balance: 1e6, leverage: 500,
             stopout_pct: 20, status: "active", created_at_msc: 1,
             archived_at_msc: null },
  header: { currency: "USC", balance: 1e6, equity: 1e6, margin: 0,
            free_margin: 1e6, margin_level: null, floating: 0, leverage: 500,
            stopout_pct: 20 },
  open: [], pending: [], closed: [],
  summary: { n: 0, win_rate: null, avg_r: null, total_r: 0, avg_mae_r: null,
             avg_mfe_r: null },
  max_drawdown: null, equity_curve: [], ...over,
});

describe("PaperPositions", () => {
  it("marks a position that belongs to another symbol than the chart's", () => {
    render(<PaperPositions view={view({ open: [pos({ symbol: "BTCUSDc", symbol_base: "BTCUSD" })] })}
      chartSymbol="XAUUSDc" onClose={vi.fn()} onPartial={vi.fn()}
      onReverse={vi.fn()} onCancel={vi.fn()} onCloseAll={vi.fn()} />);
    expect(screen.getByTitle(/simbol lain/i)).toBeTruthy();
  });

  it("closes the position it was asked about, by id", () => {
    const onClose = vi.fn();
    render(<PaperPositions view={view({ open: [pos({ id: 7 })] })}
      chartSymbol="XAUUSDc" onClose={onClose} onPartial={vi.fn()}
      onReverse={vi.fn()} onCancel={vi.fn()} onCloseAll={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /tutup/i }));
    expect(onClose).toHaveBeenCalledWith(7);
  });

  it("offers nothing to press when the account is flat", () => {
    render(<PaperPositions view={view()} chartSymbol="XAUUSDc" onClose={vi.fn()}
      onPartial={vi.fn()} onReverse={vi.fn()} onCancel={vi.fn()}
      onCloseAll={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /tutup semua/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm --prefix frontend test -- PaperAccountBar PaperPositions`
Expected: FAIL — cannot resolve `./PaperAccountBar`.

- [ ] **Step 3: Write `PaperAccountBar`**

Create `frontend/src/components/PaperAccountBar.tsx`. The two functions the tests
pin, written out — every other decision in this component is layout:

```tsx
/** The ONLY path from a money number to the screen. `null` is UNKNOWN and reads
 *  as an em dash: a 0 here would say the account was wiped. The unit is never
 *  implied — this is a USC account and a bare number would be read as dollars. */
const money = (v: number | null, currency: string) =>
  v == null ? "—" : `${v.toFixed(2)} ${currency}`;

/** The one number that decides liquidation should look different BEFORE it
 *  fires, not after. Within 1.5x of the stop-out level it turns to the `neg`
 *  token; unknown stays muted rather than alarming. */
const levelTone = (level: number | null, stopoutPct: number) =>
  level == null ? "text-muted"
    : level <= stopoutPct * 1.5 ? "text-neg" : "text-ink";
```

The rest:

- Every figure goes through `money(...)`. There is no other formatting path, so
  there is no path that prints a bare number or a `0` for an unknown.
- Each figure carries `aria-label` (`balance`, `equity`, `margin`, `free-margin`,
  `margin-level`, `floating`) so a test can name it.
- Margin level renders as a percentage with one decimal, toned by `levelTone`.
- When `live` is false, a `role="status"` line says
  `Feed mati — posisi tidak dipantau` (the honesty the spec §5 requires).
- Colours come only from `palette` in `lib/theme.ts` (`pos`, `neg`, `muted`), and
  sizes only from the `lib/type.ts` roles (`text-title`, `text-body`,
  `text-caption`). No hex, no `text-[13px]`.

- [ ] **Step 4: Write `PaperPositions`**

Create `frontend/src/components/PaperPositions.tsx`:

- Three sections, each omitted entirely when empty: open positions, pending
  orders, closed history (most recent first, capped at 20 rows).
- A row whose `symbol` differs from `chartSymbol` renders its symbol with
  `title="posisi di simbol lain"` and the `muted` token — the account is
  cross-symbol while the chart shows one.
- Open rows show direction, volume, entry, SL, TP, and floating P&L through the
  same `money` helper. Buttons: `Tutup` (calls `onClose(id)`), `Sebagian` (calls
  `onPartial(id)`), `Balik` (calls `onReverse(id)`).
- Pending rows show the kind and trigger price, and a `Batalkan` button calling
  `onCancel(id)`.
- `Tutup semua` renders only when there is at least one open or pending row, and
  calls `onCloseAll()`.
- Closed rows show exit reason, net P&L, and R (`—` when null).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm --prefix frontend test -- PaperAccountBar PaperPositions`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PaperAccountBar.tsx frontend/src/components/PaperPositions.tsx frontend/src/components/PaperAccountBar.test.tsx frontend/src/components/PaperPositions.test.tsx
git commit -m "feat(paper): account bar and positions table, unknowns shown as unknown"
```

---

### Task 16: Order entry and account management

**Files:**
- Create: `frontend/src/components/PaperOrderPanel.tsx`, `frontend/src/components/PaperAccountDialog.tsx`
- Test: `frontend/src/components/PaperOrderPanel.test.tsx`

**Interfaces:**
- Consumes: `placeOrder`, `createAccount`, `archiveAccount`, `listAccounts` (Task 14); the existing `Modal` component.
- Produces:
  - `<PaperOrderPanel accountId symbol lastPrice onPlaced />`
  - `<PaperAccountDialog open accounts selectedId onSelect onCreated onArchived onClose />`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/PaperOrderPanel.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import PaperOrderPanel from "./PaperOrderPanel";

const placeOrder = vi.fn();
vi.mock("../lib/paperApi", () => ({ placeOrder: (...a: unknown[]) => placeOrder(...a) }));

beforeEach(() => placeOrder.mockReset().mockResolvedValue({ id: 1, status: "open" }));
afterEach(() => vi.clearAllMocks());

describe("PaperOrderPanel", () => {
  it("sends exactly one sizing field — lots, or a share of equity, never both", async () => {
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("sl"), { target: { value: "4025" } });
    fireEvent.click(screen.getByRole("button", { name: /^beli/i }));
    await waitFor(() => expect(placeOrder).toHaveBeenCalled());
    const body = placeOrder.mock.calls[0][1];
    expect(body.risk_pct == null !== (body.volume == null)).toBe(true);
  });

  it("switches sizing mode to risk and then sends risk_pct alone", async () => {
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /risiko/i }));
    fireEvent.change(screen.getByLabelText("risk-pct"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("sl"), { target: { value: "4025" } });
    fireEvent.click(screen.getByRole("button", { name: /^beli/i }));
    await waitFor(() => expect(placeOrder).toHaveBeenCalled());
    const body = placeOrder.mock.calls[0][1];
    expect(body.risk_pct).toBe(1);
    expect(body.volume).toBeNull();
  });

  it("shows the server's refusal instead of pretending the order landed", async () => {
    placeOrder.mockRejectedValue(new Error("Harga XAUUSDc basi 62s"));
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("sl"), { target: { value: "4025" } });
    fireEvent.click(screen.getByRole("button", { name: /^beli/i }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/basi/));
  });

  it("needs a limit price before a pending order can be sent", () => {
    render(<PaperOrderPanel accountId={1} symbol="XAUUSDc" lastPrice={4030}
      onPlaced={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /limit/i }));
    expect(screen.getByRole("button", { name: /^beli/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix frontend test -- PaperOrderPanel`
Expected: FAIL — cannot resolve `./PaperOrderPanel`.

- [ ] **Step 3: Write `PaperOrderPanel`**

Create `frontend/src/components/PaperOrderPanel.tsx`. The submit path, written out
— it is the part the tests pin and the part where a mistake sizes a position by
accident:

```tsx
type Sizing = "lot" | "risk";
type Kind = "market" | "limit" | "stop";

async function submit(direction: "buy" | "sell") {
  setBusy(true);
  setError(null);
  try {
    // Exactly one sizing field, the other explicitly null. Enforced HERE, not
    // left to the server to catch: sending both would make the browser and the
    // server disagree about which number sized the position.
    await placeOrder(accountId, {
      symbol, direction, kind,
      volume: sizing === "lot" ? Number(volume) : null,
      risk_pct: sizing === "risk" ? Number(riskPct) : null,
      price: kind === "market" ? null : Number(price),
      sl: Number(sl) || 0,
      tp: Number(tp) || 0,
    });
    onPlaced();
  } catch (e) {
    // The server's refusals are written for a human to read ("Harga XAUUSDc
    // basi 62s"). Replacing them with a generic message throws away the only
    // sentence that says what to do next.
    setError(e instanceof Error ? e.message : String(e));
  } finally {
    setBusy(false);
  }
}

// A pending order with no trigger price has nothing to wait for.
const disabled = busy || (kind !== "market" && !price);
```

The rest:
- Inputs, each with the `aria-label` the test names: `volume`, `risk-pct`, `sl`,
  `tp`, `price`.
- An order-kind toggle `Market` / `Limit` / `Stop`, and a sizing toggle
  `Lot` / `Risiko`.
- `Beli` and `Jual` buttons, both taking `disabled={disabled}` from above — so a
  request in flight cannot become two positions, and a pending order cannot be
  sent without its trigger.
- The `error` string renders in a `role="alert"` element.

- [ ] **Step 4: Write `PaperAccountDialog`**

Create `frontend/src/components/PaperAccountDialog.tsx` using the existing
`Modal` component:

- A list of accounts with name, balance and its `USC` unit; clicking one calls
  `onSelect(id)` and closes.
- A create form: name, starting balance (USC), leverage, stop-out %. Defaults
  `1_000_000` USC (= $10,000), leverage `500`, stop-out `20`. Submitting calls
  `createAccount` and then `onCreated(account)`.
- An archive button per row behind the existing `ConfirmModal`, calling
  `archiveAccount` then `onArchived(id)`. Archiving is not deleting — say so in
  the confirm copy, because the history is the point of the account.
- The balance input is labelled with its unit and shows the dollar equivalent as
  a caption (`1.000.000 USC ≈ $10.000`). This is the one screen where the human
  types a USC figure from scratch, so the unit cannot be implied.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm --prefix frontend test -- PaperOrderPanel`
Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PaperOrderPanel.tsx frontend/src/components/PaperAccountDialog.tsx frontend/src/components/PaperOrderPanel.test.tsx
git commit -m "feat(paper): order entry with exclusive sizing, and account management"
```

---

### Task 17: The toggle, and wiring it into the chart

**Files:**
- Modify: `frontend/src/components/ChartToolbar.tsx`, `frontend/src/pages/Chart.tsx`
- Test: `frontend/src/components/ChartToolbar.test.tsx` (create), `frontend/src/pages/Chart.test.tsx` (append — it already mocks `CandleChart` as a `forwardRef`, `useRiskSizing`, and the API layer; do NOT start a second page-test file with its own mock stack)

**Interfaces:**
- Consumes: everything from Tasks 14-16; `prefs` at `/api/prefs/paper`; the existing `Sheet`, `useLiveStatus`, and the draggable SL/TP line plumbing already in `CandleChart`.
- Produces: `ChartToolbar` gains `paperMode: boolean` and `onPaperMode: (on: boolean) => void`. `Chart.tsx` renders the paper panel in paper mode and hides the real order panel.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/ChartToolbar.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ChartToolbar from "./ChartToolbar";
import { DEFAULT_SETTINGS } from "../lib/chartPrefs";

function setup(paperMode = false, onPaperMode = vi.fn()) {
  render(<ChartToolbar symbol="XAUUSDc" tf="M5" settings={DEFAULT_SETTINGS}
    onSymbol={vi.fn()} onTf={vi.fn()} onSettings={vi.fn()} onReset={vi.fn()}
    onJumpNow={vi.fn()} onReplay={vi.fn()} paperMode={paperMode}
    onPaperMode={onPaperMode} />);
  return onPaperMode;
}

describe("ChartToolbar paper toggle", () => {
  it("says which mode is active, out loud", () => {
    setup(true);
    expect(screen.getByRole("button", { name: /paper/i }).getAttribute("aria-pressed"))
      .toBe("true");
  });

  it("asks for the other mode when pressed", () => {
    const onPaperMode = setup(false);
    fireEvent.click(screen.getByRole("button", { name: /paper/i }));
    expect(onPaperMode).toHaveBeenCalledWith(true);
  });
});
```

Append to `frontend/src/pages/Chart.test.tsx` — the safety test, the one that
matters most in this task. Reuse that file's existing `CandleChart` /
`useRiskSizing` mocks and its `renderChart()`-style setup rather than building a
second mock stack; add only the paper mock:

```tsx
// The paper account's own data comes from one hook. Mock it here so the mode
// test is about what the PAGE renders, not about polling.
vi.mock("../hooks/usePaperAccount", () => ({
  usePaperAccount: () => ({
    view: {
      account: { id: 1, name: "Scalping XAU", initial_balance: 1e6, balance: 1e6,
                 leverage: 500, stopout_pct: 20, status: "active",
                 created_at_msc: 1, archived_at_msc: null },
      header: { currency: "USC", balance: 1e6, equity: 1e6, margin: 0,
                free_margin: 1e6, margin_level: null, floating: 0,
                leverage: 500, stopout_pct: 20 },
      open: [], pending: [], closed: [],
      summary: { n: 0, win_rate: null, avg_r: null, total_r: 0,
                 avg_mae_r: null, avg_mfe_r: null },
      max_drawdown: null, equity_curve: [],
    },
    error: null,
    refresh: vi.fn(),
  }),
}));

describe("Chart in paper mode", () => {
  beforeEach(() => {
    window.localStorage.setItem("paper",
      JSON.stringify({ mode: "paper", accountId: 1 }));
  });

  it("marks the chart as paper so it cannot be misread as the real account", async () => {
    renderChart();
    expect(await screen.findByText(/PAPER/)).toBeTruthy();
  });

  it("does not render the real-money open button while paper is active", async () => {
    renderChart();
    await screen.findByText(/PAPER/);
    // The real panel's open button is the one that spends real money. In paper
    // mode it must not exist at all — disabled is not enough, a disabled button
    // still reads as "the thing I am about to press".
    expect(screen.queryByRole("button", { name: /^buka posisi/i })).toBeNull();
  });
});
```

If the existing file has no shared `renderChart()` helper, extract one from its
first test rather than copying its `render(<MemoryRouter>...)` block a third time.
And confirm the real panel's button label before pinning it: run
`rg -n "Buka" frontend/src/components/RiskSizePanel.tsx` and match the regex to
what is actually there. A safety test that greps for the wrong label passes for
the wrong reason, which is worse than not having it.

- [ ] **Step 2: Run them to verify they fail**

Run: `npm --prefix frontend test -- ChartToolbar Chart`
Expected: FAIL — `ChartToolbar` has no `paper` button; `Chart` renders no `PAPER` marker.

- [ ] **Step 3: Add the toggle to `ChartToolbar`**

Add two props (`paperMode`, `onPaperMode`) and a two-button segmented control
matching the timeframe group's existing markup, placed immediately before the
Replay button:

```tsx
      <div className="glass flex overflow-hidden text-body" role="group"
           aria-label="mode akun">
        <button
          onClick={() => onPaperMode(false)}
          aria-pressed={!paperMode}
          className={`px-2 py-1 ${!paperMode ? "bg-pos/20 text-pos" : "text-muted"}`}
        >REAL</button>
        <button
          onClick={() => onPaperMode(true)}
          aria-pressed={paperMode}
          className={`px-2 py-1 ${paperMode ? "bg-violet/20 text-violet" : "text-muted"}`}
        >PAPER</button>
      </div>
```

Both classes use palette tokens from `lib/theme.ts` through the Tailwind config;
no raw colour belongs here. `violet` and not `cyan`: cyan is already the one
`focus-visible` ring across the app, and a mode marker that borrows the focus
colour reads as focus. There is no `accent` token — check `lib/theme.ts` before
inventing one.

- [ ] **Step 4: Wire `Chart.tsx`**

In `frontend/src/pages/Chart.tsx`:

- Add `const [paperMode, setPaperMode] = useState(false)` and
  `const [paperAccountId, setPaperAccountId] = useState<number | null>(null)`,
  both loaded once from `/api/prefs/paper` and written back on every change
  (mirror how `useChartPrefs` persists, including its localStorage mirror).
- `const paper = usePaperAccount(paperMode ? paperAccountId : null)` — no polling
  at all while paper is off.
- While `paperMode` is true:
  - wrap the chart container in `ring-2 ring-violet` and render a `PAPER` badge
    in the corner, so a screenshot can never be mistaken for the real account;
  - render `PaperAccountBar`, `PaperOrderPanel` and `PaperPositions` as the side
    panel content, and **do not render** `RiskSizePanel` or `LivePositionCard`
    at all. Not disabled — absent;
  - pass `paper.view.open` into the existing draggable SL/TP plumbing as position
    lines, and commit a drag through `modifySltp`. The planned-vs-position line
    rule already in `CandleChart` needs no change: a paper position is a
    position;
  - pass `live={liveStatus?.alive ?? false}` into `PaperAccountBar` so the
    unmonitored warning appears exactly when the daemon is down.
- Keep `sidePanel` rendered in **exactly one** container below `lg`
  (`{!panelOpen && sidePanel}` plus the `Sheet`) — two live order panels aimed at
  one account, one of them invisible, has happened before.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm --prefix frontend test -- ChartToolbar Chart`
Expected: PASS, 4 tests.

- [ ] **Step 6: Run every gate**

Run:

```bash
uv run pytest -q
npm --prefix frontend test
npm --prefix frontend run build
uv run journal rebuild
```

Expected: pytest green, vitest green, `tsc -b && vite build` clean, and
`journal rebuild` still succeeds — paper tables are not derived from raw and must
survive a rebuild untouched. Paste the actual output of all four.

- [ ] **Step 7: Verify against the live store, read-only**

Run:

```bash
uv run journal status
uv run python -c "
from journal.store.db import connect
c = connect('data/journal.db')
print('paper accounts:', c.execute('SELECT COUNT(*) FROM paper_accounts').fetchone()[0])
print('paper positions:', c.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0])
print('quotes:', c.execute('SELECT COUNT(*) FROM live_quotes').fetchone()[0])
print('deals_raw untouched:', c.execute('SELECT COUNT(*) FROM deals_raw').fetchone()[0])
"
```

Expected: `journal status` exits 0; the paper tables exist and are empty; `deals_raw` holds the same count as before the feature.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ChartToolbar.tsx frontend/src/pages/Chart.tsx frontend/src/components/ChartToolbar.test.tsx frontend/src/pages/Chart.test.tsx
git commit -m "feat(paper): a marked PAPER mode on /chart where no real-money button exists"
```

---

## After the plan

1. **Restart `journal live`.** The daemon must load `paper_step`, and it already owed one restart from before this feature (`live_heartbeat.code_fingerprint`, migration 012).
2. **Hand-verify one margin figure** against the broker's own numbers, following the `risk_amount` precedent in `docs/mt5-deal-model.md` §8: open a 0.10 lot XAUUSDc paper position at 1:500 and confirm the reported margin against what MT5 shows for the same size. The docs are not the authority; the broker is.
3. **Update `docs/HANDOFF.md § CURRENT STATE`** with what landed, the gate output, and anything measured that contradicts the spec — the spec has been wrong before, and a measured contradiction goes into the doc, not into memory.
4. **Not done in this plan, deliberately:** `app.py` is now past 1,100 lines and wants splitting; that is its own change, with its own review.
