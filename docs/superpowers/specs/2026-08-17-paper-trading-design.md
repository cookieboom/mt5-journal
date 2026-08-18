# Paper trading — a virtual account with a balance you set

**Status:** design approved 2026-08-17, not yet planned.
**Scope:** a full virtual trading account on `/chart`, funded with a balance the
human chooses, filled against the live tick feed, with leverage, margin and
stop-out. Closest reference point is TradingView's Paper Trading.

## 1. Why this exists

The journal already simulates trades twice: `replay_eval` fills fake positions
against historical bars, and `training_positions` scores them in R. Neither has a
balance. A position sized at "1% risk" means nothing without a number to take one
percent of, so every risk decision in training is unanchored, and nothing in the
journal answers "what would this month have done to an account".

Paper trading adds the missing noun: an account. Balance, equity, margin, and a
stop-out that can actually happen.

## 2. What was decided, and what was rejected

Decisions, in the order they were made:

| Question | Decision |
| --- | --- |
| Depth | Full virtual account **plus** margin/leverage and stop-out |
| Who evaluates | The `journal live` daemon, **per tick** |
| Money unit | **USC**, same as the real account — zero conversion |
| Account count | **Many named accounts**, each with its own balance/leverage/stop-out |
| Order kinds | Market+SL/TP, limit/stop pending, modify SL/TP, partial close, reverse, close-all |
| Paper vs real | One `REAL`/`PAPER` toggle, with the chart visually marked in paper mode |
| Execution realism | Real spread from the tick; no slippage knob; commission/swap 0 |

Two architectures were considered and rejected.

**Extending `training_*` into a general simulated account.** One engine for both
replay and paper, fewer tables. Rejected because `replay_eval`'s fill model is
*the open of the next bar*, which is a different semantic from *the current tick*.
Teaching one tested pure module two fill semantics is the tidiest way to break
what already works. `training_positions` also has no order concept, so pending
limit/stop orders have nowhere to live, and the migration would touch live replay
data.

**A `PaperMT5Client` satisfying the adapter Protocol.** This is the maximum-reuse
option: fake deals flow through the entire real pipeline (`trade_commands` →
`execute` → `positions_get` → `poller` → `reconstruct` → `trades`) and paper
trades inherit every existing analytic for free. Rejected because fictional deals
would land in `deals_raw`, which is append-only and is the broker's source of
truth (rule 2). A wrong paper row there can never be removed. The reuse is real
and the price is the integrity of the one table that must not lie.

## 3. Data model

Two new tables, plus one small addition to the live-monitor group.

```sql
paper_accounts (
    id, name UNIQUE, initial_balance, balance, leverage, stopout_pct,
    status('active'|'archived'), created_at_msc, archived_at_msc
)

paper_positions (
    id, account_id, symbol, symbol_base, direction,
    order_kind('market'|'limit'|'stop'), request_price,
    volume, sl, tp, sl_initial, expires_msc,
    status('pending'|'open'|'closed'|'cancelled'|'expired'),
    requested_msc, entry_msc, entry_price,
    exit_msc, exit_price,
    exit_reason('tp'|'sl'|'manual'|'stopout'|'reverse'),
    net_profit, r_multiple, mae, mfe, mae_r, mfe_r,
    parent_id, created_at_msc
)

live_quotes (symbol PRIMARY KEY, bid, ask, tick_msc, updated_msc)
```

`live_quotes` belongs to the live-monitor group and is overwritten freely, exactly
like `live_candles` — it is a latest-value cache, not part of any append-only
contract. `tick_msc` is the broker's tick time; `updated_msc` is true UTC of the
overwrite, and it is what staleness is judged against.

An account is cross-symbol: one paper account can hold positions on `XAUUSDc` and
`BTCUSDc` at once, and margin and stop-out are computed across all of them. The
chart shows one symbol, so `PaperPositions` lists every position in the account
and marks the ones belonging to another symbol.

`symbol` is stored verbatim as MT5 said it and `symbol_base` normalised through
`domain/symbols.to_base` (rule 11).

Money is USC. All `*_msc` are epoch milliseconds, integer, server-UTC (rule 3).
`sl`/`tp` follow rule 4: `0` means none set, and `sl_initial` is written once at
fill so R stays honest after the stop is moved — the same discipline `trades` uses.

### What is deliberately absent

- **No `paper_ledger`.** Balance history is `initial_balance` plus a running sum
  of `net_profit` over closed rows ordered by `exit_msc`. A ledger table is a
  second copy of a fact that already exists, and a second copy is a future lie.
- **No separate orders table.** This account is `margin_mode = 2` (hedging): one
  order is one position. A pending order is a row with `status = 'pending'` — the
  exact shape `training_positions` already proves.
- **No `currency` column.** Always USC. One unit means one money path.
- **No stored `equity` / `margin` / `margin_level`.** Computed on read. Stored,
  they could go stale.

### Partial close splits the row

Closing part of a position inserts a new `closed` row for the closed slice, with
`parent_id` pointing at the parent, and reduces the parent's `volume`. Every
closed row is therefore a complete trade record (entry, exit, volume, net_profit,
R) and every statistic works with no special case.

The alternative — an accumulating `realized_profit` column on a still-open row —
forces every statistic to understand "partly realised but still open". That cost
lands on all future readers; the split costs one `parent_id` column.

## 4. `domain/paper_eval.py` — the pure evaluator

Takes plain dataclasses: position states, a `Quote(bid, ask, time_msc)`, account
state, symbol specs. Returns events. No DB, no bridge, no MT5 (rules 1, 7, 12).
Fixture-testable with nothing running.

Order of evaluation per tick, explicit, because this is where the bugs live:

1. **Expiry** of pending orders (`expires_msc`; NULL = good till cancelled).
2. **Pending triggers.** Buy limit when `ask <= request_price`; buy stop when
   `ask >= request_price`; sell limit when `bid >= request_price`; sell stop when
   `bid <= request_price`. The fill takes the current quote — ask for a buy, bid
   for a sell — **not** the requested price. Tick data is discrete; handing out a
   better price than was actually observed is a fabricated gift.
3. **SL/TP on open positions.** A buy exits through the bid, a sell through the
   ask. Exit price is the SL/TP level itself; slippage across a tick gap is not
   modelled — the same choice, for the same reason, as `replay_eval`. When one
   tick reaches both levels, **the stop fills first**: pessimistic, because tick
   granularity cannot reveal the true order and an honest simulator never
   flatters.
4. **Stop-out.** `equity = balance + Σ floating`, and
   `margin_level = equity / Σ margin × 100`. Below `stopout_pct`, close the
   **worst-losing** position, recompute, and repeat until the level recovers.
   That is MT5's behaviour, not a guess. With no open positions the margin is 0
   and there is no margin level to compare — the check is skipped, never treated
   as an infinite or a zero level.

A stopped-out account has **no separate status**. Stop-out closes positions; the
account stays `active` with whatever balance is left, and the human decides
whether to archive it. An account state named after a loss would be a judgement
the journal has no business making.

Floating P&L per position reuses `replay_eval.net_profit_usc` against the exit
side of the quote (bid for a buy, ask for a sell).

### Margin, derived from the specs rather than typed in

`symbol_specs` carries no `margin_initial`, so margin has to be derived. The
number 100 (USC per USD) never appears as a literal:

```
usc_per_quote_unit = tick_value / tick_size      # XAUUSDc: 0.1 / 0.001 = 100
notional_usc       = volume * price * tick_value / tick_size
margin_usc         = notional_usc / leverage
```

Derived from each symbol's own specs, so it self-corrects per symbol instead of
trusting one hardcoded ratio. Its validity is conditional: `currency_profit` must
be `USD` **and** `accounts.currency` must be `USC`. Otherwise the result is
`None` — unknown, never a coerced 0 (rule 4, Trap 14). All three symbols on this
account satisfy the condition; the condition is checked, not assumed.

Margin is computed at the entry price and frozen.

> `ponytail:` frozen entry-price margin. MT5 recomputes metals margin at the
> current price. Recompute per tick if the difference is ever felt.

## 5. Who writes what

This split is what decides how the feature feels.

**The daemon (`journal live`) keeps watch.** It stores the latest tick into
`live_quotes` — one `symbol_info_tick` per symbol that has an open or pending
paper position. No paper activity means no bridge calls and no cost. It then runs
the evaluator: triggers, SL/TP, stop-out.

**The web executes instantly.** Market open, close, partial close, reverse and
close-all resolve immediately against the freshest `live_quotes` row, and are
refused when that quote is stale — the same shape as `execute._check_feed_fresh`,
which already guards real opens. No queue, no waiting a cycle. It feels like
TradingView, and SL/TP and stop-out between clicks still belong to the daemon.

The web never touches the bridge. That boundary stays intact.

Staleness reuses the threshold `execute._check_feed_fresh` already applies to real
opens — one constant, one definition of "too old to trade against", for both kinds
of money.

### What breaks when the daemon is down, stated plainly

With `journal live` stopped, `live_quotes` stops advancing. Two consequences, and
neither is hidden from the human:

- Every paper market action is **refused** on the staleness guard, the same way a
  real open is. Nothing executes against a dead feed.
- Open paper positions are **not evaluated**: an SL will not trigger, a pending
  order will not fill, and a stop-out will not happen until the daemon runs again.
  When it does, it evaluates against the *current* tick, not the ticks it missed —
  so a stop that would have been hit during the outage fills late and worse.

The paper panel therefore shows the same liveness indicator the live panel already
uses (`useLiveStatus`), and says out loud that positions are unmonitored while it
is red. A simulator that quietly pretends to have been watching is worse than one
that admits it was not.

**Placement in `live_cycle`: immediately after the liveness beacon (step 4)**,
ahead of the ingest pipeline and the order send. A paper SL evaluation has a
deadline the same way an order does, and work that can block for seconds must not
run in front of it. A bridge error in this step is logged and skipped — the loop
must never die over play money. It also runs with `trading=False`: paper is not
real trading.

## 6. Web API — `src/journal/web/paper.py`

`web/paper.py` holds the service functions; the route declarations sit inline in
`app.py` beside the training block, which is this codebase's established shape
(`web/training.py` + `@app.post("/api/training/...")` in `app.py`). An `APIRouter`
would be one line in `app.py` instead of a hundred, but it would put a second
routing convention in one folder — the same drift that produced two design systems
in the storage subtree. `app.py` is already 999 lines and this adds to it; splitting
it is worth doing, and is not this feature's job.

```
GET    /api/paper/accounts                  list
POST   /api/paper/accounts                  {name, initial_balance, leverage, stopout_pct}
POST   /api/paper/accounts/{id}/archive
GET    /api/paper/accounts/{id}             header + positions + pending + summary + equity curve
POST   /api/paper/accounts/{id}/orders      {symbol, direction, kind, volume|risk_pct,
                                             price?, sl, tp, expires_msc?}
PATCH  /api/paper/positions/{id}            {sl, tp}
POST   /api/paper/positions/{id}/close      {volume?}   omitted volume = full
POST   /api/paper/positions/{id}/reverse
DELETE /api/paper/positions/{id}            cancel a pending order
POST   /api/paper/accounts/{id}/close_all
```

The `GET` header carries `balance`, `equity`, `margin`, `free_margin`,
`margin_level` and the currency label `USC` — never a bare number printed as `$`.

Three route semantics that would otherwise be read two ways:

- **`volume` and `risk_pct` are exclusive.** Exactly one must be given. Both, or
  neither, is a `400` — a route that picks for you is a route that sizes someone's
  position by guessing.
- **`reverse`** closes the position in full at the current quote and opens a new
  one in the opposite direction with the same volume, carrying no SL/TP across.
  The closed row's `exit_reason` is `'reverse'`, which is what makes it legible
  later as a deliberate flip rather than a manual exit that happened to be
  followed by an entry.
- **`close_all`** covers the whole account, every symbol, and cancels pending
  orders too. Not just the symbol currently on the chart.

Pending-order expiry is evaluated only by the daemon (§5), so an expired order can
outlive its `expires_msc` while the daemon is down. It expires unfilled on the next
cycle; it never fills late.

### Validation is reused, not copied

`domain/commands.py` already owns order validation: `_check_volume`
(volume_min/max/step) and `_check_level`, which already checks the *side* of a
level against a reference price **and** the broker's `stops_level` in points, and
already treats `None` as "leave alone" and `0.0` as "clear" (rule 4). Both become
public names — `check_volume`, `check_level` — and `validate()` keeps calling
them. Paper passes the entry price for a market order and `request_price` for a
pending one as the reference.

`web/training.py::_check_direction` is deliberately **left where it is**. It
exists to handle the case where the entry price is not yet known and only sl-vs-tp
can be compared; paper always has a reference price, so `check_level` covers it
and moving the training helper would be churn that buys nothing.

Exactly one refusal is genuinely new: **insufficient free margin**. It lives in
the pure evaluator, not in the route.

Sizing reuses `domain/risk.volume_for_risk` and `floor_to_step`, with the budget
expressed as `risk_pct × paper equity`. That is the whole point of a configurable
balance: 1% risk only means something once the number it is one percent of
is yours.

## 7. Frontend

- `lib/paperApi.ts` (mirrors `replayApi.ts`), `hooks/usePaperAccount.ts` polling
  at 2500 ms — the same interval as `/api/live`.
- A `REAL`/`PAPER` toggle in `ChartToolbar`, persisted under its **own**
  `app_prefs` key `paper` (`{mode, accountId}`) through a new
  `get_paper_prefs`/`set_paper_prefs` pair — the shape `prefs_store` already uses
  for chart, replay, risk and trade-PNG preferences. Not folded into the `chart`
  blob: `ChartSettings` is versioned `version: 1` and carries a legacy-object
  migration, so adding a field there means a version bump and a second migration
  path, and which paper account is selected is not chart appearance anyway.
  Reopening the page keeps the last mode.
- With PAPER active the chart container takes an accent border and a badge.
  Colours come from `lib/theme.ts` and sizes from `lib/type.ts`; pasted hex and
  `text-[13px]` are defects, not choices.
- New components: `PaperAccountBar` (balance / equity / margin level),
  `PaperPositions` (shaped after `ReplayPositions`), `PaperOrderPanel`,
  `PaperAccountDialog` (pick / create / archive).
- The draggable SL/TP lines already exist and already know the rule: planned
  lines retire once a position line exists. Paper positions enter as position
  lines — no new chart mechanics.
- Below `lg` the panel goes into the existing `Sheet`, and `sidePanel` renders
  **exactly once**. Two "Buka" buttons aimed at one account, one of them
  invisible, has happened before.

## 8. Statistics

Paper never enters `trades`, never enters `analytics/report`, and `journal
rebuild` never touches it — stated in the schema comment, as the training tables
already do.

Its own summary: `n`, win rate, avg R, total R, avg MAE_R, avg MFE_R, max
drawdown, and an equity curve. `training_store._summary` moves to
`domain/sim_stats.py`; training and paper both import it. One aggregator, one
ungating precedent (docs §8's replay exception applies for the same reason — a
paper account is a handful of trades), one place to fix when it is wrong. Every
metric still ships with its own `n`.

MAE/MFE are computed at close from cached candles via `domain/excursion`, the way
`web/training.py` already does it.

## 9. Tests — domain first (rule 7)

1. `tests/test_paper_eval.py`, pure and fixture-based: all four trigger sides;
   the fill takes the quote and not the requested price; the spread cost appears
   at entry; SL/TP resolve through bid vs ask correctly; stop-first when one tick
   hits both; exit exactly at the level; expiry; **margin for 0.10 lot XAUUSDc
   computed by hand and matched**; a missing or non-USD spec yields `None`; the
   stop-out cascade closes the worst loser first; the stop-out check is skipped
   with zero open positions instead of dividing by zero; partial-close split
   arithmetic; R still correct after the stop is moved (`sl_initial`).
2. `tests/test_paper_store.py`: CRUD, parent/child split, archive, cascade delete.
3. `tests/test_paper_web.py`: the `web/paper.py` service functions called
   directly against a seeded DB, with no HTTP layer — the same discipline
   `tests/test_web.py` states, which is why this project carries no `TestClient`
   dependency. A stale quote
   is refused, insufficient margin is refused, the reused volume-step and
   `stops_level` validators are actually reached, `volume` together with
   `risk_pct` is a `400` and so is neither, `reverse` leaves one closed row marked
   `'reverse'` plus one open row the other way with no SL/TP, and `close_all`
   reaches other symbols and pending orders too.
4. Additions to `tests/test_live.py`: the paper step runs; **zero bridge calls
   when no paper activity exists**; a raising bridge does not kill the loop; it
   runs with `trading=False`.
5. Vitest: `PaperOrderPanel.test.tsx`, `PaperPositions.test.tsx`, and one safety
   test — in PAPER mode the real open button cannot be pressed.
6. One margin figure hand-verified against the broker's own numbers, following
   the `risk_amount` precedent in `docs/mt5-deal-model.md` §8. The docs are not
   the authority; the broker is.

## 10. Migration and operations

`migrations/013_paper.sql` creates `paper_accounts`, `paper_positions`,
`live_quotes` and their indexes; `schema.sql` is updated in the same change.
`journal backup` already covers them — it snapshots the one file. `journal status`
gains nothing: a blown paper account is not an unhealthy store.

One operational note: the daemon must be restarted after this lands, and it
already owes one restart from before.

## 11. Rule 9

Paper trading simulates the human's own decision. It emits no signal, recommends
no entry, and is never the input to an automated step — the same standing as the
replay/training path that already ships. No new exception to rule 9 is needed.

## 12. Implementation phases

1. Migration, store, and the pure evaluator (tests first). No UI.
2. The daemon step and `live_quotes`.
3. The web API.
4. The frontend: toggle, panels, accounts.
