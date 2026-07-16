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

### Enums you will need

```python
DEAL_TYPE_BUY = 0; DEAL_TYPE_SELL = 1
DEAL_TYPE_BALANCE = 2; DEAL_TYPE_CREDIT = 3; DEAL_TYPE_CHARGE = 4
DEAL_TYPE_CORRECTION = 5; DEAL_TYPE_COMMISSION = 6  # ...and more, all non-trades

DEAL_ENTRY_IN = 0      # opening
DEAL_ENTRY_OUT = 1     # closing
DEAL_ENTRY_INOUT = 2   # reversal: closes and opens in one execution
DEAL_ENTRY_OUT_BY = 3  # closed by an opposite position (hedging accounts)

DEAL_REASON_CLIENT = 0   # desktop terminal
DEAL_REASON_MOBILE = 1
DEAL_REASON_WEB    = 2
DEAL_REASON_EXPERT = 3   # EA
DEAL_REASON_SL     = 4   # hit stop loss
DEAL_REASON_TP     = 5   # hit take profit
DEAL_REASON_SO     = 6   # stop out (margin call)
```

`reason` on the **last OUT deal** is your free discipline metric: it tells you
whether you hit TP, hit SL, or bailed out manually. Do not throw it away.

> **VERIFY**: enum integer values above are from the MQL5 docs and should be
> stable, but confirm against `mt5.DEAL_REASON_SL` etc. rather than hardcoding
> the literals. Import them from the adapter.

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

### Trap 5 — `DEAL_ENTRY_OUT_BY` — LIVE on this account

"Close by" closes two opposite positions against each other in one execution.
One deal, two `position_id`s involved (`position_by_id` on the order). Profit
accounting is split across both, and the closing price on the OUT_BY side is not
a market price you actually got.

**This account is hedging, so Close-by is available in the terminal UI and may
appear in history.**

→ First: check whether it *ever happened* (`SELECT DISTINCT entry FROM deals_raw`).
  - **Not present** → `raise NotImplementedError` on encounter, test that it
    raises, move on. Do not build for a case you have never triggered.
  - **Present** → it must be implemented, and it needs its own fixture.
→ Either way, never let an OUT_BY deal fall through the normal OUT path. Its
volume and price will look plausible and quietly corrupt the VWAP exit.

### Trap 12 — Broker symbol suffixes

This broker appends `c` (cent account): the tradeable symbol is `XAUUSDc`, and
`symbol_info_tick("XAUUSD")` is a different thing — possibly `None`, possibly a
non-tradeable feed. Other brokers use `.m`, `.raw`, `_ecn`, `#`, `-`.

→ Two columns, always: `symbol` = verbatim from MT5, used for every MT5 call and
stored in `_raw`. `symbol_base` = normalised, used for grouping in analytics.
→ Normalisation lives in exactly one place, `domain/symbols.py`. It must be a
**pure lookup + regex**, not a guess: strip a known suffix set, and if a symbol
does not match, keep it verbatim and log once. Never silently mangle `USDCAD`
into `USDCA` by stripping a trailing `d`. Write the test for that case first.
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
trusting a single R number.

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
server_now = mt5.symbol_info_tick("EURUSD").time  # server epoch
true_now   = time.time()                           # real UTC epoch
offset_s   = round((server_now - true_now) / 900) * 900   # snap to 15 min
```
Do this during an active market session (a stale weekend tick will lie to you).
Store it in `sync_state.server_utc_offset_s` on every sync — **it changes with
broker DST**, so a single hardcoded constant will corrupt two weeks per year.

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
- [ ] `rebuild` run twice → byte-identical trades table (idempotent)

---

## 6. This account — measured 2026-07-16

Confirmed by running the doctor script against the live bridge.

| Fact | Value | Consequence |
|---|---|---|
| Adapter | `siliconmetatrader5`, `localhost:8001` | — |
| `margin_mode` | **2 = RETAIL_HEDGING** | Trap 4 (INOUT) cannot occur → raise. Trap 5 (OUT_BY) CAN occur → must handle. |
| Account currency | **`USC` (US cents)** | All money columns are cents. R-multiple is unit-free — prefer it. |
| Balance at measurement | 6047.22 USC (≈ $60.47) | Small account. Position sizing will be in 0.01 lots. |
| Symbol suffix | **`c`** — trades `XAUUSDc` | See trap 12. |
| Total deals in history | 140 | ≈ 60–70 trades. See §7. |
| `TradeDeal` fields | `ticket, order, time, time_msc, type, entry, magic, position_id, reason, volume, price, commission, swap, profit, fee, symbol, comment, external_id` | **No `sl`/`tp` — trap 6 confirmed.** `fee` exists and must be summed (trap 9). |

Still open:

- [ ] `server_utc_offset_s` — first measurement said 0, but it used `XAUUSD`
      (wrong symbol; this account trades `XAUUSDc`). **Re-measure with the
      correct symbol, during an open session, after `symbol_select(sym, True)`.**
      0 is plausible (some brokers run UTC) but is not yet trusted. Until
      confirmed, do not ship session/hour analytics.
- [ ] Does `entry == 3` (OUT_BY) actually appear in this account's history?
      If yes, trap 5 must be implemented, not raised.
- [ ] `trade_tick_value` for `XAUUSDc` — expected to be denominated in USC.
      Verify `risk_amount` against one hand-computed trade before trusting R.
- [ ] Standalone commission deals, or folded into the trade deal?
- [ ] `MaxBars` actually in effect in the container.

## 7. Sample-size honesty

140 deals ≈ 60–70 trades. At that size, win rate has a margin of error of roughly
±12 percentage points, and per-session or per-hour breakdowns will be single
digits per bucket — i.e. noise.

→ Build the analytics anyway (the pipeline is the point), but the reports must
display `n` next to every statistic, and must suppress or grey out any bucket
with `n < 20`. A journal that presents noise confidently is worse than no
journal. This is a rule, not a nicety.
