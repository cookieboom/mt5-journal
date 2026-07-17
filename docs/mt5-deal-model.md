# MT5 deal model — how to turn deals into trades without lying to yourself

Read this before writing or changing anything in `domain/reconstruct.py`,
`ingest/deals.py`, or any code that touches a timestamp.

Every trap below produces **silently wrong numbers**, not a crash. That is why
this file exists.

---

## 1. The mental model

MT5 has three objects. "Trade" is not one of them.

| Object | What it is | Python call |
|---|---|---|
| **Order** | An instruction ("buy 0.10 XAUUSD, SL 3210, TP 3260"). May be filled, partially filled, cancelled, or rejected. Carries `sl` / `tp`. | `history_orders_get()` |
| **Deal** | An actual execution. Money changed hands. Immutable. Does **not** carry SL/TP. | `history_deals_get()` |
| **Position** | The currently-open net exposure. Disappears when closed. Carries current `sl` / `tp`. | `positions_get()` |

A "trade" as a human means it — *I entered here, I exited there* — is something
**you** reconstruct by grouping deals on `position_id`.

```
order (BUY 0.10, sl=3210)  ──fills──>  deal #1  entry=IN   position_id=555
                                       deal #2  entry=OUT  position_id=555
                                       ─────────────────────────────────────
                                       trade: 0.10 XAUUSD long, from d1 to d2
```

## 2. Field reference

### `history_deals_get()` → TradeDeal

```
ticket, order, time, time_msc, type, entry, magic, position_id, reason,
volume, price, commission, swap, profit, fee, symbol, comment, external_id
```

> **No `sl` / `tp` field.** This is the single most important fact in this
> document. SL/TP must come from `orders_raw` or from the poller.
> **VERIFY on your build**: print `mt5.history_deals_get(...)[0]._asdict()` once
> and paste the real field list into this doc. Builds change.

### `history_orders_get()` → TradeOrder

```
ticket, time_setup, time_setup_msc, time_done, time_done_msc, time_expiration,
type, type_time, type_filling, state, magic, position_id, position_by_id,
reason, volume_initial, volume_current, price_open, sl, tp, price_current,
price_stoplimit, symbol, comment, external_id
```

Has `sl` and `tp`. This is where `sl_initial` comes from — with a caveat (trap 6).

### Enums — do not copy these from memory. Probe the bridge.

**This section was wrong once already.** It originally listed
`DEAL_TYPE_COMMISSION = 6`. The live bridge reports `BONUS = 6, COMMISSION = 7`,
and the bridge is authoritative. The `live.py` enum assertion caught it. That is
the whole reason CLAUDE.md rule 12 exists.

Generate the enums from ground truth, never from this document:

```python
{a: getattr(mt5, a) for a in dir(mt5) if a.startswith("DEAL_TYPE_")}
{a: getattr(mt5, a) for a in dir(mt5) if a.startswith("DEAL_ENTRY_")}
{a: getattr(mt5, a) for a in dir(mt5) if a.startswith("DEAL_REASON_")}
{a: getattr(mt5, a) for a in dir(mt5) if a.startswith("ORDER_")}
```

Measured on this bridge (2026-07-16), partial — **complete via the probe above**:

```
DEAL_TYPE_BUY = 0    DEAL_TYPE_SELL = 1        DEAL_TYPE_BALANCE = 2
DEAL_TYPE_CREDIT = 3 DEAL_TYPE_CHARGE = 4      DEAL_TYPE_CORRECTION = 5
DEAL_TYPE_BONUS = 6  DEAL_TYPE_COMMISSION = 7  ... continues past 7

DEAL_ENTRY_IN = 0    DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2 DEAL_ENTRY_OUT_BY = 3     (verified, complete)

DEAL_REASON_CLIENT = 0  MOBILE = 1  WEB = 2  EXPERT = 3
DEAL_REASON_SL = 4      TP = 5      SO = 6   ... continues past 6
```

Rules that follow from this:

→ `DealType` and `DealReason` must contain **every** member the bridge exposes.
An incomplete `IntEnum` turns `DealType(deal.type)` into a `ValueError` the first
time a rollover, dividend, or agent-commission deal appears in history.
→ The `live.py` assertion should **assert when the bridge exposes the constant,
and log a warning when it does not** — an unexposed constant is unverifiable, not
a failure, and must not stop init.
→ Reconstruction stays a **positive whitelist** on `BUY`/`SELL` (trap 1), so new
deal types are ignored safely regardless. The enums exist for honest labelling
and for the equity curve, not for filtering.

`reason` on the **last OUT deal** is your free discipline metric: it tells you
whether you hit TP, hit SL, or bailed out manually. Do not throw it away.

---

## 3. The traps

### Trap 1 — Not every deal is a trade

`DEAL_TYPE_BALANCE`, `CREDIT`, `CHARGE`, `CORRECTION`, `COMMISSION` are deposits,
withdrawals, bonuses, and broker adjustments. They have **`position_id == 0`**
and no symbol.

→ Filter: keep only `type in (DEAL_TYPE_BUY, DEAL_TYPE_SELL)` **and**
`position_id != 0`. Store the rest in `deals_raw` anyway (they're needed for the
equity curve), but never feed them to the reconstructor.

### Trap 2 — Partial fills: several IN deals, one position

A 1.00 lot order can fill as 0.60 + 0.40 → two `DEAL_ENTRY_IN` deals sharing one
`position_id`, at two different prices.

→ `open_price` = **volume-weighted average** of all IN deals:
`sum(price * volume) / sum(volume)`. Not the first price. Not the mean.

### Trap 3 — Partial closes: several OUT deals, one position

Same story on the exit. Also means `close_time` = time of the **last** OUT deal,
and `close_price` = volume-weighted average of OUT deals.

→ Assert `sum(IN volume) ≈ sum(OUT volume)` with tolerance `1e-9`. If it doesn't
balance, the position is still partially open — do not emit a closed trade.

### Trap 4 — `DEAL_ENTRY_INOUT` — NOT APPLICABLE to this account

On **netting** accounts, sending an opposite order larger than your position
reverses it: one deal closes the old exposure and opens new exposure in the same
`position_id`, direction flipping halfway through. Grouping by `position_id`
would then produce one nonsense record.

**This account is hedging (`margin_mode = 2`), so INOUT cannot occur.**

→ Decision: `raise NotImplementedError` if `entry == DEAL_ENTRY_INOUT` is ever
seen, with the position_id in the message. Write a test asserting it raises.
An honest crash beats a silently wrong trade.

### Trap 5 — `DEAL_ENTRY_OUT_BY` — possible, but never happened here

"Close by" closes two opposite positions against each other in one execution.
One deal, two `position_id`s involved. Profit accounting is split across both,
and the closing price on the OUT_BY side is not a market price you actually got.

This account is hedging, so the terminal offers Close-by. **But
`SELECT DISTINCT entry FROM deals_raw` over all 140 deals returns `[0, 1]` —
OUT_BY has never occurred.**

→ Decision: `raise NotImplementedError` on `entry == 3`, naming the position_id.
Write a test asserting it raises. Do not build for a case that has never been
triggered — but never let it fall through the normal OUT path either, because
its volume and price look plausible and would quietly corrupt the VWAP exit.
→ If it ever fires, that raise is your signal to come back and implement it.

### Trap 6 — SL/TP: `0` and `NULL` are not the same thing

Deals carry no SL/TP. So `sl_initial` comes from the order that opened the
position (`orders_raw` where `ticket == in_deal.order`). But:

- If you entered with **no SL** and added one later, the opening order has
  `sl = 0.0`. Real answer: you had no initial SL.
- If you entered **with** an SL and later moved it to breakeven, the opening
  order still has your original SL. Good — history is intact here.
- If you entered via a mobile app / one-click with SL set afterwards, `sl = 0.0`.
- If the order is outside your sync window, you get **nothing**.

→ Rule: `sl_initial = NULL` when unknown, `0` only when you can positively
confirm no SL was set. Any trade with `sl_initial IS NULL` is **excluded** from
R-multiple statistics. Never coerce NULL to 0 — that silently produces infinite
R and poisons every downstream stat.

→ **The same distinction applies to `risk_amount`, and it is easy to get
backwards.** If `sl_initial == open_price` (an SL sitting exactly at entry), the
risk is *known* and it is *zero*. That is not the same as unknown:

| Case | `risk_amount` | `r_multiple` | Says |
|---|---|---|---|
| SL unknown | `NULL` | `NULL` | we know nothing |
| SL exactly at entry | `0.0` | `NULL` | risk was zero; R is undefined |

`risk_amount` must keep returning `0.0` for a zero distance — making it return
`None` would collapse the two rows into one and lose a real fact. The guard that
needs fixing is on `r_multiple`: division by a *known* zero is undefined, not
unknown, so gate on `risk_amount` being truthy, not on it being non-`None`.
A `risk is not None` check passes for `0.0` and raises `ZeroDivisionError` on the
first breakeven-at-entry trade — killing the whole rebuild, not one row.
→ This is why M4 (the poller) exists: for trades going forward, snapshot
`positions_get()` every few seconds into `sl_tp_snapshots` so the *actual* first
SL is recorded regardless of how it was set.

### Trap 7 — Timestamps are broker-server time, not UTC

`copy_rates_*`, `history_deals_get`, and `symbol_info_tick` return Unix epoch
integers — but the wall-clock encoded in them is the **broker's server clock**
(commonly UTC+2 / UTC+3, with its own DST calendar), presented as if it were UTC.

`datetime.utcfromtimestamp(deal.time)` gives you the server's clock face, not
true UTC. Most brokers' "daily candle" therefore closes at 00:00 server time,
which is 05:00 or 06:00 WIB.

→ Measure the offset instead of assuming it:
```python
server_now = mt5.symbol_info_tick("XAUUSDc").time  # server epoch
true_now   = time.time()                            # real UTC epoch
offset_s   = round((server_now - true_now) / 900) * 900   # snap to 15 min
```
Do this during an active market session (a stale weekend tick will lie to you),
on a symbol that actually exists on the server, after `symbol_select(sym, True)`.

**Measured on this account: `offset_s = 0` — this broker's server clock IS UTC**
(confirmed on `XAUUSDc` with a 2-second-old tick). Session analysis needs no
conversion: London ≈ 07:00–16:00, NY ≈ 12:00–21:00, straight off `time_msc`.
WIB = UTC+7 at display time only.

→ Keep measuring it every sync and storing it in `sync_state.server_utc_offset_s`
anyway. If the broker ever introduces a DST-shifting server, a hardcoded `0` would
silently corrupt two weeks a year and you would never notice.

→ Note the circularity: tick age is only meaningful once the offset is known, and
the offset is only trustworthy from a fresh tick. It resolves here because the
offset measured 0 against a 2-second-old tick during an open session. If that
ever stops being true, re-derive both together — do not trust either alone.

→ Store `time_msc` **exactly as MT5 returned it** in `deals_raw` (raw = raw), and
store the offset alongside. Convert to true UTC in `domain/`, not in `ingest/`.
Session analysis (London/NY) is meaningless until this is right.

### Trap 8 — Window edges create orphans

`history_deals_get(from, to)` returns deals in a range. A position opened 100
days ago and closed yesterday, synced with a 90-day window, gives you an **OUT
with no IN**.

→ Detect orphans (`position_id` with OUT deals but zero IN deals) and skip them
with a logged warning. Do not guess the entry price.
→ On the first backfill, use `datetime(2000,1,1)` as `from` and take everything.
Incremental syncs should overlap the previous window by ~7 days.

### Trap 9 — Costs are scattered

`commission` often lands on the IN deal, `swap` on the OUT deal, and some brokers
emit standalone commission deals.

→ `net_profit = sum(profit + commission + swap + fee)` over **all** deals sharing
that `position_id`. Not just the OUT deal's `profit`. Getting this wrong makes
every marginal strategy look profitable.

### Trap 10 — Ticket uniqueness

Deal tickets are unique **per account**, not globally. If you ever add a second
account (demo + live, or a prop firm), `ticket` as PRIMARY KEY will collide.

→ Composite key `(account_login, ticket)` from day one. It costs nothing now and
is a migration nightmare later.

### Trap 11 — Risk needs contract specs, not just price distance

`risk_amount` is **not** `|open_price - sl| * volume`. You need per-symbol specs:

```
risk_amount = (|open_price - sl_initial| / tick_size) * tick_value * volume
```

from `symbol_info(symbol)`: `trade_tick_size`, `trade_tick_value`,
`trade_contract_size`, `digits`, `point`, `currency_profit`.

→ Cache these in `symbol_specs`. They change (broker adjusts contract size) —
store `fetched_at` and refetch weekly.
→ If `currency_profit != account currency`, `tick_value` already accounts for
conversion *at fetch time*, which is not the rate at trade time. For a personal
journal this error is small; note it and move on. Don't silently pretend it's
exact.

---

### Trap 12 — Broker symbol suffixes

This broker appends `c` (cent account): the tradeable symbols are `XAUUSDc`,
`BTCUSDc`, `EURUSDc`. `symbol_info_tick("XAUUSD")` returns nothing — the
unsuffixed symbol **does not exist on this server**.

→ Two columns, always: `symbol` = verbatim from MT5, used for every MT5 call and
stored in `_raw`. `symbol_base` = normalised, used for grouping in analytics.
→ Normalisation lives in exactly one place, `domain/symbols.py`.
→ **The suffix set is `{"c"}` and nothing else.** Do not speculatively add
`.m`, `.raw`, `_ecn`, `#`, `-` "just in case" — every extra rule is an extra way
to silently mangle a symbol, for zero present benefit. Mention them in a comment.
Widen the set only when a broker that uses them actually appears.
→ Guard: strip only if the remainder is ≥ 3 characters. No match → return
verbatim and log once. Test that `USDCAD` → `USDCAD`, unchanged.
→ Reason this matters later: if you ever move from this cent account to a
standard one, `XAUUSDc` and `XAUUSD` must merge into one history, not two.

### Trap 13 — Cent accounts

Account currency `USC` = US cents. `profit`, `commission`, `swap`, `fee`, and
`trade_tick_value` are all denominated in it. A balance of `6047.22` is $60.47.

→ Never format a money column with a `$`. Always print the code from
`accounts.currency`.
→ `risk_amount` will be in USC too — which means `r_multiple =
net_profit / risk_amount` is **unit-free and unaffected**. This is why analytics
should lead with R and treat absolute P&L as secondary.
→ Verify once by hand: take one closed trade, compute expected risk in USC from
`tick_size`/`tick_value`, and compare to what the code produced. Do this before
trusting a single R number. See §8 for the reference figure.

### Trap 14 — `currency_profit` is NOT the unit of `tick_value`

Measured on this account: `XAUUSDc` reports `currency_profit = "USD"` while the
account currency is `USC`. Read carelessly, `tick_value = 0.1` looks like $0.10.
It is not. It is **0.1 US cents**.

| Field | What it actually is |
|---|---|
| `SYMBOL_CURRENCY_PROFIT` | The symbol's **quote currency** — the "USD" in XAUUSD. Descriptive metadata about the instrument. |
| `SYMBOL_TRADE_TICK_VALUE` | Value of one `tick_size` move, **in the deposit currency** — i.e. `account_info().currency`. MT5 has already done the conversion. |

→ **Rule: the unit of `tick_value`, and therefore of `risk_amount`, is always
`accounts.currency`. Never `symbol_specs.currency_profit`.** Store
`currency_profit` for reference, but never label a money value with it.
→ Cross-check that the numbers cohere before trusting them:
`contract_size = 1.0` (1 lot = 1 oz), a `0.001` price move on 1 oz = $0.001
= 0.1 cents = `tick_value` of `0.1` USC. ✓ Consistent.
→ Caveat to note and move on: MT5 converts at *fetch* time, not at trade time.
For a personal journal on a USD-quoted symbol with a USD-derived account
currency, this error is nil. It would matter on e.g. EURGBP. Do not pretend it
is exact; just record `symbol_specs.fetched_at`.

### Trap 15 — `copy_rates_*` returns SECONDS; the `candles` column is MILLISECONDS

Deals carry both `time` (seconds) and `time_msc` (milliseconds). **Rates do not.**
`copy_rates_range` gives you `time` in epoch **seconds** only. The `candles`
table column is `time_msc`, and CLAUDE.md rule 3 says every timestamp outside the
adapter is epoch milliseconds.

So a ×1000 conversion must happen — and if nobody owns it, seconds land in a
column named `_msc`. The renderer then queries
`WHERE time_msc BETWEEN open_time_msc AND close_time_msc`, matches **zero rows**,
and draws an empty chart. You will blame mplfinance for an hour.

→ The conversion belongs at the **adapter boundary**, not in `ingest/`. The
`Candle` dataclass carries `time_msc: int` and nothing else time-shaped;
`live.py` does `int(row["time"]) * 1000`, `fake.py` does the same from fixtures.
Everything above the adapter then obeys rule 3 with no exceptions to remember.
→ Test: a fixture bar with `time = 1752624000` must surface as
`Candle(time_msc=1752624000000)`. Assert the magnitude — a bar timestamp below
`10**12` is seconds that leaked through.

### Trap 16 — The broker deletes your history

**Observed on this account, 2026-07-11.** Exness archived old deals: removed them
from retrievable history and left a `DEAL_TYPE_CORRECTION` marker with amount
`0.00` and comment `"Archived deals"`. The deals it took netted −14.50 USC.
`history_deals_get(2000-01-01, now)` cannot return them. Neither can MT5's own
report. They are gone.

This is the single most important fact about this project.

→ **MT5 is not a durable record of your trading. This journal is.** Every day
without a sync is a day the broker may delete something you will never get back.
The M4 poller is therefore not a nice-to-have; it is the reason the project
exists.
→ `deals_raw` being append-only stops mattering as a style choice and starts
mattering as an archival guarantee: once a deal is captured locally, no broker
action can remove it. Never add a delete path to `deals_raw`.
→ `journal sync` must detect every future archive event by set difference:
`{ticket in deals_raw} - {ticket returned by the broker}`. Non-empty means the
broker deleted deals you still hold — report it loudly and positively. The
balance invariant **cannot** see this: archiving moves no money, so the residual
never budges. See §6.
→ Analytics caveat: your history contains the trades that *survived*. Any total
predating the first sync is a lower bound, not a count. Say so in reports.
→ `DEAL_TYPE_CORRECTION` with amount `0.00` is the marker to watch for. Do not
dismiss it as a no-op — it is a headstone.

## 4. Reconstruction algorithm

```
1. Read deals_raw for account, ordered by time_msc, ticket.
2. Drop non-trade deals (Trap 1).
3. Group by position_id.
4. For each group:
   a. If any deal has entry == INOUT  -> handle per Trap 4 (split or raise).
   b. If any deal has entry == OUT_BY -> handle per Trap 5 (raise for now).
   c. ins  = [d for d in group if d.entry == IN]
      outs = [d for d in group if d.entry == OUT]
   d. If not ins  -> orphan, skip + warn (Trap 8).
      If not outs -> still open, emit with status='open', close fields NULL.
   e. If sum(v of ins) != sum(v of outs) within 1e-9 -> status='partially_open'.
   f. direction   = 'buy' if ins[0].type == DEAL_TYPE_BUY else 'sell'
      open_time   = min(d.time_msc for d in ins)
      close_time  = max(d.time_msc for d in outs)
      open_price  = vwap(ins);  close_price = vwap(outs)
      volume      = sum(v of ins)
      net_profit  = sum(profit + commission + swap + fee for d in group)
      close_reason= outs[-1].reason
   g. sl_initial / tp_initial: look up orders_raw by ins[0].order.
      Missing order -> NULL (Trap 6). sl == 0.0 -> NULL unless poller confirms.
   h. risk_amount: Trap 11. NULL if sl_initial is NULL.
      r_multiple = net_profit / risk_amount, else NULL.
5. Write to trades. Never UPDATE — DELETE all and INSERT (rebuildable).
```

---

## 5. Test cases that must exist

`tests/test_reconstruct.py` is not done until all of these pass:

- [ ] simple long: one IN, one OUT → correct direction, prices, duration
- [ ] simple short
- [ ] partial fill: two INs at different prices → VWAP entry
- [ ] partial close: one IN, three OUTs → VWAP exit, close_time = last OUT
- [ ] partial close leaving remainder open → status='partially_open', no r_multiple
- [ ] still open: IN, no OUT → status='open'
- [ ] orphan: OUT with no IN → skipped, warning logged, no crash
- [ ] balance/credit deals present → ignored, do not appear as trades
- [ ] commission on IN + swap on OUT → net_profit sums all of them
- [ ] opening order has sl=0 → sl_initial IS NULL, r_multiple IS NULL
- [ ] opening order missing from orders_raw → sl_initial IS NULL, no crash
- [ ] close_reason correctly reads SL / TP / CLIENT off the last OUT deal
- [ ] INOUT deal present → raises NotImplementedError naming the position_id
- [ ] OUT_BY deal present → raises (until confirmed present in real history)
- [ ] two positions open on the same symbol at once (hedging) → two separate
      trades, neither contaminating the other
- [ ] `symbol_base`: `XAUUSDc` → `XAUUSD`, `EURUSDc` → `EURUSD`,
      `USDCAD` → `USDCAD` (unchanged — must not strip a real trailing letter)
- [ ] money columns carry the account currency through; nothing formats as `$`
- [ ] `rebuild` run twice → identical trades, **comparing every column except
      `id` and `rebuilt_at`**. Those two are *expected* to differ: `id` is
      `AUTOINCREMENT` and renumbers after a `DELETE`, and `rebuilt_at` is a
      wall-clock stamp. An earlier draft of this section demanded a
      "byte-identical" table, which is not achievable and would have sent you
      hunting a phantom. Compare on `(account_login, position_id, segment)` plus
      the value columns.
      (This is also why `annotations` and `tags` key on
      `(account_login, position_id, segment)` and never on `trades.id` — a
      rebuild would orphan every note you had written.)
- [ ] **the balance invariant below holds to within 0.01 USC**

## 6. The balance invariant — the one test that proves reconstruction is complete

Unit tests prove each case works. This proves nothing was **lost or
double-counted** across the whole history — the failure mode unit tests cannot
see.

MT5 balance is nothing more than the sum of every deal's cash effect:

```
sum(profit + commission + swap + fee) over ALL deals in deals_raw
    == account_info().balance                                  (to within 0.01)
```

That includes `DEAL_TYPE_BALANCE` / `CREDIT` deposits, whose amount lives in the
`profit` field. If this fails, ingest dropped or duplicated a deal — fix that
before anything else.

Then the reconstruction check — a **partition** check. Every trade deal belongs to
exactly one `position_id`, therefore to exactly one `trades` row; every other deal
is counted separately. So the grouping must preserve total cash exactly:

```
sum(trades.net_profit)                       -- ALL statuses, not just closed
  + sum(profit+commission+swap+fee of all NON-trade deals)  -- deposits, corrections
  - sum(reconciliations.amount)
    == accounts.balance                                     (to within 0.01)
```

**Include open and partially-open trades.** An earlier draft of this section said
`WHERE status='closed'`, reasoning that floating P&L lives in equity rather than
balance. The reasoning is right; the conclusion is wrong. Floating P&L is not in
`deals_raw` either, so it never enters this sum. What *is* in both is an open
position's **realised** cash — its entry commission — which is in the balance
today. Excluding open trades would drop it and break the identity. It happens to
be invisible on this swap-free, zero-commission account, which is exactly why it
would have gone unnoticed until the account changed.

`trades.net_profit` = the cash of every deal sharing that `position_id`, whatever
the status. For an open trade that is just the entry costs. The identity then says
precisely what you want it to say: **reconstruction is a partition of the deals,
not a filter.** If it fails while identity 1 holds, the bug is in
`reconstruct.py` — a position_id skipped, a partial close miscounted, or a cost
component dropped (trap 9).

→ Ship this as `journal verify`, run it after every `rebuild`, and make it fail
loudly. It costs one SQL query and catches an entire class of silent corruption.

### Pair the balance with the deals, or the invariant lies

`balance` must be the snapshot **captured by the same sync that captured the
deals** (`accounts.balance`), never a live `account_info()` read at verify time.

Two reasons, and the second is the important one:

1. **Spurious failures.** Sync at 10:00, a position closes at 10:05, verify at
   10:10 → balance moved, `deals_raw` did not, residual is garbage. The invariant
   asks "do the deals I captured at time T explain the balance at time T". Both
   halves must come from T.
2. **Self-sufficiency.** A `verify` that needs a live bridge cannot check a
   backup, cannot run in CI, and cannot run when the broker is down. Trap 16 says
   this journal — not MT5 — is the durable record. An invariant that depends on
   the broker being reachable contradicts the thesis of the whole project.

→ `sync` writes `accounts.balance` and `accounts.equity`. `verify` is pure SQL
against the store and takes no client at all.

### The residual does NOT detect future archiving

**Archiving removes the record, not the money.** The balance does not move — the
cash changed hands long ago. And `deals_raw` is append-only, so our sum does not
move either. **The residual stays flat and `verify` keeps passing.** An archive
event is invisible to the balance invariant.

The 14.50 below exists only because those deals were archived *before* the first
sync ever ran. It is a one-time historical scar, not a live detector. Do not
write "a widening residual means the broker archived more" anywhere — it is false.

The real detector is a set difference, and `sync` already holds both sides:

```sql
archived = {ticket FROM deals_raw} - {ticket returned by history_deals_get}
```

Non-empty → the broker has deleted deals this journal still holds. That is not an
error condition. **It is proof the journal is doing its job**, and the only place
those trades still exist. Report it prominently; count it; never let it pass
silently.

### Never absorb a gap into a tolerance

When the identity fails, the temptation is `abs(delta) < 15.0`. **Do not.** That
epsilon blinds the invariant to the exact failure it exists to catch — a dropped
or duplicated deal worth less than the epsilon is now invisible forever.

Instead every discrepancy gets a named row in `reconciliations`, and the
invariant becomes:

```
sum(deals cash) - sum(reconciliations.amount) == balance      (within 0.01)
```

A row starts as `status='unexplained'` and stays visible in every report until a
human writes a `reason`. The gap does not disappear; it acquires a name.

### Measured 2026-07-16: the 14.50 USC gap — RESOLVED

```
sum(all 140 deals) = 6061.72 USC
balance            = 6047.22 USC
delta              =  +14.50      (the account holds LESS than deals claim)
```

**Cause: the broker archived deals and deleted them from history.**

Confirmed against MT5's own `Account History → Report` (Exness-MT5Real36):

- The report's own cumulative Balance column ends at **6061.72** while its
  `Balance:` line reads **6047.22**. **MT5's own export contains the identical
  gap.** The bridge is faithful — this is not an adapter bug.
- `Commission` and `Swap` are `0.00` in the report too. This is a swap-free cent
  account; `swap = 0` on every deal is the truth, not a dropped field.
- Every fixture figure matches the report exactly: net profit 63.72, 68 trades,
  deposits/withdrawals 5998.00.
- Report row 297: deal **1399033630**, type `correction`, `2026-07-11 04:58:56`,
  amount `0.00`, comment **`"Archived deals"`**.

The arithmetic lands exactly on that timestamp:

```
running balance at the correction   = 6051.32
trades after it                     = 6061.72 - 6051.32 = +10.40
true balance minus those trades     = 6047.22 - 10.40   = 6036.82
6051.32 - 6036.82                   = 14.50   ← the gap, at the correction
```

On 2026-07-11 the broker archived old deals — removed them from retrievable
history — and inserted correction #1399033630 as a marker carrying `0.00`. The
archived deals netted −14.50 USC. That amount is real and sits in the balance,
but **no deal you can fetch contains it, from MT5 or the bridge. It is gone.**

Resolution: one `reconciliations` row, `status='explained'`:

```
amount        = 14.50
effective_msc = 2026-07-11 04:58:56 UTC
reason        = "Broker archived deals; underlying deals unrecoverable."
evidence      = "correction deal 1399033630, comment 'Archived deals';
                 MT5 report cum-balance 6061.72 vs Balance: 6047.22"
```

`journal verify` then passes honestly, and the 14.50 stays named forever instead
of dissolving into an epsilon.

> **Recording note, now proven:** the first fixture recording sanitised
> `comment -> ""` on all 140 deals. That destroyed the string `"Archived deals"`
> — the literal answer to this question — along with every `[sl 4035.112]` and
> `[tp 4057.193]` marker. Deal comments are execution metadata, not PII. Redact
> `login`, `name`, `server`, `company` only. **Keep `comment` and `external_id`.**

→ Independent cross-check, once, by hand: export MT5's own **Account History →
Report** and compare its total profit and trade count against your `trades`
table. Your reconstruction agreeing with the broker's own statement is the only
external validation available.

---

## 7. This account — measured 2026-07-16

Confirmed by running the doctor script against the live bridge.

| Fact | Value | Consequence |
|---|---|---|
| Adapter | `siliconmetatrader5` v1.2.3, `localhost:8001` | — |
| `margin_mode` | **2 = RETAIL_HEDGING** | Trap 4 (INOUT) cannot occur → raise. |
| Account currency | **`USC` (US cents)** | All money columns are cents. R-multiple is unit-free — prefer it. |
| Balance at measurement | 6047.22 USC (≈ $60.47) | Small account, 0.01-lot sizing. |
| **`server_utc_offset_s`** | **0 — CONFIRMED** | Measured on `XAUUSDc` with a 0-second-old tick. Broker server clock **is UTC**. Session analysis needs no conversion. WIB = UTC+7 for display only. **Still re-measure every sync** in case the broker introduces DST. |
| `entry` values in history | **`[0, 1]` only** | IN and OUT only. **OUT_BY never occurred in 140 deals** → raise, do not implement (trap 5). INOUT impossible on hedging → raise (trap 4). Reconstruction is the simple path. |
| Symbols traded | `XAUUSDc`, `BTCUSDc`, `EURUSDc` | Only suffix in use is **`c`**. `symbol_info_tick("XAUUSD")` returns nothing — the unsuffixed symbol does not exist here. |
| `XAUUSDc` specs | `tick_size=0.001`, `tick_value=0.1`, `contract_size=1.0`, `currency_profit=USD` | 1 lot = 1 oz (not 100). **`tick_value` is in USC, not USD** — see trap 14. |
| Total deals | 140 | ≈ 60–70 trades. See §9. |
| `TradeDeal` fields | `ticket, order, time, time_msc, type, entry, magic, position_id, reason, volume, price, commission, swap, profit, fee, symbol, comment, external_id` | **No `sl`/`tp` — trap 6 confirmed.** `fee` exists and must be summed (trap 9). |

Still open:

- [ ] `BTCUSDc` trades weekends; forex does not. Session/day analytics must not
      assume a 5-day week across all symbols.
- [ ] `MaxBars` actually in effect in the container (matters at M3).

Closed: standalone commission deals (none — MT5's own report confirms
`commission = 0.00`) · `BTCUSDc`/`EURUSDc` specs (M1 `symbol_specs`: tick_value
0.1 / 0.01 / 1.0 — genuinely distinct; gold's transfer nowhere).

### SL provenance is EA-only — measured 2026-07-17

Over the 68 opening orders (the order each IN deal points to), three sets:

| set | size |
|---|---:|
| opening order `sl != 0`            | 6 |
| IN deal `magic != 0`               | 6 |
| IN deal `reason == EXPERT (3)`     | 6 |

The three are the **same six trades**: `S == M == E`, `|S ∩ M ∩ E| = 6`.

- discretionary trades (`magic == 0`): **62**; of these, with `sl != 0`: **0**.
- cross-tab `(magic!=0, sl!=0)`: `(False, False) → 62` · `(True, True) → 6`.

So `sl_initial` (hence `risk_amount`, hence `r_multiple`) is recoverable from
`orders_raw` for the **6 EA trades only**; discretionary R-coverage is **0 of 62**.

## 8. Risk calculation — the reference figure

Verified by hand against the specs above. Any code that disagrees is wrong.

```
Entry 4035.000, SL 4030.000, volume 0.10 lot, XAUUSDc

ticks       = |4035.000 - 4030.000| / tick_size(0.001)  = 5000
risk_amount = ticks × tick_value(0.1) × volume(0.10)    = 50 USC  (= $0.50)
% of 6047.22 USC balance                                = 0.83%
```

If `risk_amount` comes out as `0.50` you have wrongly assumed USD.
If it comes out as `5000` you forgot `tick_value`.
If it comes out as `5.0` you forgot `volume`.

**This is a required test case in `tests/test_risk.py`.** Write it before the
risk code exists.

## 9. Sample-size honesty

140 deals ≈ 60–70 trades. At that size, win rate has a margin of error of roughly
±12 percentage points, and per-session or per-hour breakdowns will be single
digits per bucket — i.e. noise.

→ Build the analytics anyway (the pipeline is the point), but the reports must
display `n` next to every statistic, and must suppress or grey out any bucket
with `n < 20`. A journal that presents noise confidently is worse than no
journal. This is a rule, not a nicety.
