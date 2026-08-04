# Risk-based auto lot sizing + live position open — design

Date: 2026-08-04
Status: approved, ready for planning

## Problem

Live mode has no way to open a position. `domain/commands.py:KINDS` is
`('modify_sltp', 'close', 'close_partial', 'add_volume')`, and `add_volume`
requires an existing position row to derive symbol/direction from. Everything
the journal can do to a live position assumes MT5 opened it first.

Separately, the volume a human types is disconnected from the risk they intend.
`domain/risk.py:risk_amount()` computes risk *after the fact*, from a trade that
already exists. Nothing computes it forward.

This design closes both at once: the human sets a stop-loss level by dragging a
line on the chart, states how much they are willing to lose, and the system
derives the lot size and opens the position at the current market price.

## Scope

- New live command kind `open`. Real orders, real money.
- One sizing panel in the chart's right `<aside>`, shared by replay and live
  mode. Replay keeps the same arithmetic so practice matches production.
- Direction is implied by which side of the current price the SL sits on.
- TP is optional at open.

Out of scope: pending/limit orders, trailing stops, partial scale-in, any
suggestion of *where* an SL should go (CLAUDE.md rule 9 — this is arithmetic on
numbers the human supplies, never a signal).

## Decisions taken

| Question | Decision |
|---|---|
| Where does the panel apply | Replay **and** live, one component |
| Risk input | Toggle: percent of balance, or fixed USC |
| SL input | Price level — typed or dragged on the chart |
| Direction | Derived from SL side (below price = buy, above = sell) |
| TP | Optional, typed or dragged, shows R:R |
| Volume out of bounds | Round **down** to `volume_step`; refuse outside `volume_min`/`volume_max`/`MAX_LOT`, never silently clamp |
| Extra risk guard | Hard `MAX_RISK_PCT = 5.0` of balance, constant in code |
| Volume timing | Computed and frozen at enqueue, not recomputed at send |

## Architecture

### 1. Pure core — `src/journal/domain/risk.py`

The inverse of the existing `risk_amount`, in the same module, with the same
`None`-propagation discipline (rule 4: unknown never becomes 0).

```python
def volume_for_risk(entry_price, sl, tick_size, tick_value, risk) -> float | None:
    """`risk / ((|entry - sl| / tick_size) * tick_value)`, in lots.
    None if any input is unknown, tick_size is 0, tick_value is 0,
    or entry == sl (zero distance — infinite size)."""

def floor_to_step(volume, step) -> float | None:
    """Largest whole number of `step`s not exceeding `volume`.
    Rounds DOWN so realised risk never exceeds the target. None if step <= 0.
    Uses the same tolerance discipline as `commands._is_multiple` — a floor
    computed with raw IEEE754 division drops a whole step on exact multiples."""
```

Nothing else in `risk.py`. Validation is not this module's job.

### 2. Command layer — `src/journal/domain/commands.py`

`"open"` joins `KINDS` and `_OPENING`. For an open there is no position row, so
callers pass a **synthetic position mapping**:

```python
{"symbol": "XAUUSDc", "direction": "buy", "price_current": 4035.0,
 "position_id": None, "volume": None}
```

Every existing check then applies unchanged — `_check_trade_mode` (long-only /
short-only / close-only / disabled), `_check_volume` (`MAX_LOT`, broker
min/max/step), `_check_level` (SL below and TP above for a buy, mirrored for a
sell, plus `stops_level` distance). No new branch in any of them. This is the
whole reason for the synthetic mapping: an `open` is validated by the same code
path a `modify_sltp` is.

New, and only for `open`:

```python
MAX_RISK_PCT = 5.0  # hard ceiling on a single order's risk, as % of balance
```

`validate()` grows one keyword-only parameter, `balance: float | None = None`.
When `kind == "open"`:

1. SL is **mandatory** — an open with no stop has no computable risk, and the
   whole feature is risk-first. Refuse `sl` of `None` or `0.0`.
2. Compute `risk = risk_amount(price_current, sl, tick_size, tick_value, volume)`.
   `None` (unknown spec) → refuse.
3. `balance is None` → refuse. Rule 4 applied where it binds: an unknown ceiling
   is not permission to open. `journal sync` fills `accounts.balance`.
4. `risk > balance * MAX_RISK_PCT / 100` → refuse, message states both numbers.

The asymmetry the module already documents holds: none of this touches `close`.

`build_request` for `open`:

```python
TradeRequest(action=TradeAction.DEAL, position_id=None, symbol=symbol,
             order_type=_same(direction), volume=volume,
             sl=sl, tp=tp, filling=filling_for(spec["filling_mode"]))
```

`position_id=None` is load-bearing — on a DEAL that field means "close this".
No `price`: execution is MARKET (`trade_exemode=2`, measured), the broker fills
at its own price.

### 3. Schema — migration `009_open_command.sql`

`trade_commands` needs three changes SQLite cannot do in place (a CHECK
constraint cannot be altered, and `position_id` is `NOT NULL`), so the migration
rebuilds the table: create `trade_commands_new`, `INSERT INTO ... SELECT`, drop,
rename. `SCHEMA_VERSION` 8 → 9.

Changes:

- `kind` CHECK gains `'open'`.
- `position_id` becomes nullable — an open has no position until the broker
  answers.
- `symbol TEXT` — for an open this is where the symbol comes from; for every
  other kind it stays NULL and the position row remains the source.
- `direction TEXT CHECK (direction IN ('buy','sell'))` — same, NULL elsewhere.
- `price_ref REAL` — the price the human saw when they sized the order. Evidence
  for the audit trail and the re-validation fallback. Not the execution price.

`trade_commands` is not derived, so `journal rebuild` is unaffected (rule 2).

### 4. Queue — `src/journal/execute.py`

```python
def enqueue_open(conn, login, *, symbol, direction, sl, tp, volume, price_ref) -> int
```

Loads the spec and the account balance, builds the synthetic position, calls
`validate("open", ...)`, then inserts one `pending` row carrying
`symbol`/`direction`/`price_ref` and `position_id = NULL`. Refusal writes
nothing, exactly as `enqueue` does today.

`load_context` gains a sibling rather than a mode flag:

```python
def load_open_context(conn, login, symbol, direction, price) -> tuple[dict, sqlite3.Row]
```

Returns the synthetic position and the spec row, raising `CommandError` for an
unknown symbol spec.

### 5. Executor — `src/journal/ingest/live.py`

`_execute_one_command` branches once, at the top, on `row["kind"] == "open"`:

- Fetch a fresh tick, `client.symbol_info_tick(row["symbol"])`, and use its
  bid/ask as `price_current` for re-validation. This is the ingest layer, so
  touching the bridge is allowed (rules 1 and 12 bind `web/` and `domain/`).
- Tick missing or raises → fall back to the stored `price_ref`. A stale price is
  worse than a fresh one but better than skipping the side check entirely.
- Re-validate through the same `build_request`. If the market has moved through
  the SL since enqueue, the SL is now on the wrong side and the command is
  `rejected` without being sent — which is the correct outcome.

**Volume is not recomputed.** The stored volume is the intent, the same way
`add_volume` stores it. Because the SL is an absolute price level, the only
error introduced by the delay is entry slippage, and MARKET execution has that
regardless.

Everything after that — `order_check`, `mark_sent` committing before
`order_send`, the never-re-send-on-exception rule — is untouched.

### 6. Web — `src/journal/web/app.py`

**`POST /api/size`** — pure sizing, writes nothing, used by the panel in both
modes:

```
in:  {symbol, entry, sl, tp?, risk_mode: "pct"|"usc", risk_value}
out: {volume, risk_usc, risk_pct, distance, rr, direction, error?}
```

Reads `symbol_specs` and `accounts.balance`, converts `risk_mode` to a USC
amount, calls `volume_for_risk` + `floor_to_step`, and reports the *realised*
risk of the rounded volume — which is what the human is actually taking, and is
always ≤ the target. `direction` is derived from the SL side. A refusal comes
back as `error` with the human-readable reason, not an HTTP error: the panel
shows it inline while the human keeps dragging.

**`POST /api/live/open/preview`** and **`POST /api/live/open`** — same body plus
nothing extra; direction and volume are both derived server-side. **Declared
before** the existing `/api/live/{position_id}/{action}` routes so `"open"` is
never parsed as a `position_id`.

The client's computed lot is never trusted or even sent. The server recomputes
from `{symbol, entry, sl, risk_*}` on both the preview and the commit.

`entry` is supplied by the client because it is mode-dependent and the web layer
must not touch the bridge: in live mode it is the forming bar's close, the price
the chart is showing; in replay it is the cursor bar's close. It is stored as
`price_ref` — the price the human sized against. It is deliberately *not*
treated as an execution price, and it is not a security boundary: this is a
single-user local tool whose only client is this panel.

### 7. Frontend

**`components/RiskSizePanel.tsx`** — lives in the `<aside>` that already exists
at `Chart.tsx:367`, replacing `ReplayOrderTicket`. `ReplayPositions` stays.

Controls: risk-mode toggle (% / USC) with its value, SL price, optional TP
price. Read-outs: distance, **lot**, risk in USC, risk as % of balance, R:R when
a TP is set. One action button whose label follows the SL side — Buy when the SL
is below the current price, Sell when above. Disabled with the server's reason
in plain text whenever sizing fails.

Risk-mode and risk-value persist to `app_prefs` under a new key `risk_sizing`,
following the `useChartPrefs` / `useReplayPrefs` pattern.

**No sizing arithmetic in TypeScript.** The panel debounces 150 ms and asks
`/api/size`. One formula, one language — a mirrored formula is a formula that
drifts.

**`CandleChart`** gains an optional planned-order line pair:

```ts
plannedOrder?: { entry: number; sl: number | null; tp: number | null };
onPlannedChange?: (change: { sl?: number; tp?: number }) => void;
```

These register in the same `linesMeta` array the position lines use, under the
sentinel `positionId: PLANNED_ID = -1`, so `resolveDragTarget`, the hit test, the
ghost line, and the Escape/pointercancel cleanup all work unmodified. No second
drag machine.

**`useLiveCommand`** accepts `position_id: null` and builds the URL from the
action (`/api/live/open/preview`, `/api/live/open`). The preview → `ConfirmModal`
→ confirm flow is otherwise unchanged, so an open gets the same two-step
confirmation an SL/TP edit does.

**Replay** posts the volume returned by `/api/size` to the existing
`POST /api/replay/{id}/open`. `web/training.py:open_position` needs no change.

## Data flow

```
drag SL line
  → RiskSizePanel state (sl, tp, risk_mode, risk_value)
  → debounce 150ms → POST /api/size
      → symbol_specs + accounts.balance
      → volume_for_risk → floor_to_step → risk_amount (realised)
  → panel shows lot / risk / R:R, button labelled Buy or Sell

click Buy/Sell
  live:   POST /api/live/open/preview   (server re-derives volume, validates)
        → ConfirmModal
        → POST /api/live/open           → execute.enqueue_open → trade_commands
        → journal live: fresh tick → re-validate → order_check → order_send
        → the resulting position arrives through the normal live snapshot
  replay: POST /api/replay/{id}/open with the sized volume (unchanged path)
```

## Error handling

Every refusal is a `CommandError` with a message naming the limit that was hit,
in Indonesian, matching the existing messages in `commands.py`. The panel renders
it verbatim. Cases:

- SL missing or equal to the entry price → no computable risk.
- Symbol spec unknown (`tick_size`/`tick_value`/`volume_*` NULL) → refuse, tell
  the human to run `journal sync`. Never assume a default.
- `accounts.balance` NULL → refuse an open, whatever the risk mode.
- Sized volume below `volume_min` → refuse, and state that the risk budget is
  too small for this SL distance.
- Sized volume above `volume_max` or `MAX_LOT` → refuse, state both the computed
  lot and the cap.
- Risk above `MAX_RISK_PCT` → refuse, state the risk and the ceiling.
- `trade_mode` wrong for the derived direction → the existing message.
- Market moved through the SL between enqueue and send → `rejected`, not sent.

## Testing

pytest, written before the implementation (rule 7):

- `test_risk.py` — `volume_for_risk` round-trips against `risk_amount` on the
  hand-verified §8 reference figure (4035/4030 XAUUSDc, 50 USC → 0.10 lot);
  every unknown propagates to `None`; zero distance and zero `tick_value` →
  `None`; `floor_to_step` never rounds up and is exact on exact multiples.
- `test_commands.py` — `open`: SL mandatory; wrong-side SL and TP; `MAX_LOT`;
  broker min/max/step; `MAX_RISK_PCT` boundary (just under passes, just over
  refuses); NULL balance refuses; every `trade_mode`; `build_request` emits
  `position_id=None`, the correct `order_type`, and carries sl+tp.
- `test_execute.py` — `enqueue_open` writes exactly one row with
  symbol/direction/price_ref set and `position_id` NULL; a refused open writes
  nothing.
- `test_live.py` — the executor's open branch: fresh tick used when available,
  `price_ref` fallback when the tick raises, and a price that has crossed the SL
  produces `rejected` with no `order_send`.
- `test_web.py` — `/api/size` happy path and each refusal shape; `/api/live/open`
  preview and commit; route ordering (a literal `"open"` never hits the
  `{position_id}` route).

vitest:

- `RiskSizePanel` — button label follows the SL side; disabled with the server's
  reason; read-outs render `n`-free money as USC never `$`; prefs round-trip.
- `sltpDrag` — the planned-order sentinel resolves to sl/tp correctly and does
  not collide with a real `position_id`.

## Risks

- **Real money.** This is the first code path in the project that opens a
  position. The two-step preview/confirm, the server-side recompute, `MAX_LOT`,
  `MAX_RISK_PCT`, and the trading-OFF flag in `journal live` all stack; none is
  removable.
- **`trade_commands` rebuild.** The migration copies a table that holds the audit
  trail of real orders. It must preserve every row and must be verified against a
  copy of the live DB before it runs on the real one.
- **Slippage.** MARKET execution means realised risk can exceed the target by the
  entry slippage. The panel says so; the alternative (a pending order at a fixed
  price) is out of scope.
