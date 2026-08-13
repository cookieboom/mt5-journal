# mt5-journal

Automated trading journal. Pulls trade history from MetaTrader 5, reconstructs
trades, stores OHLC centrally, renders charts on demand, computes analytics.
Single user, local-only, macOS (Apple Silicon M4).

## This account (measured, not assumed)

- Adapter: `siliconmetatrader5` bridge, Docker container on `localhost:8001`.
- `margin_mode = 2` → **HEDGING**. One order = one position; `position_id` maps
  cleanly to one trade. Only `entry` `0` (IN) and `1` (OUT) exist in this
  account's history — raise on `INOUT` and `OUT_BY`.
- **`server_utc_offset_s = 0` — confirmed.** Broker clock is UTC, so session
  analytics need no conversion. WIB = UTC+7, display only. Re-measure each sync.
- Account currency is **`USC` (US cents)**. Every `profit`/`commission`/`swap`/
  `tick_value`/`risk_amount` is in cents — never print a bare number as "$".
  R-multiple is unit-free; prefer it over absolute P&L everywhere in analytics.
- **`symbol_specs.currency_profit` is the quote currency, NOT the unit of
  `tick_value`.** `XAUUSDc` reports `currency_profit = USD` while values are in
  USC. The unit of any money figure is always `accounts.currency`.
- Symbols traded: `XAUUSDc`, `BTCUSDc`, `EURUSDc`. Only suffix in use is `c`;
  the unsuffixed symbols do not exist on this server. `XAUUSDc`:
  `tick_size=0.001`, `tick_value=0.1`, `contract_size=1.0` (1 lot = 1 oz).
- Several positions on one symbol can be open at once (hedging). Analytics must
  not assume trades are non-overlapping.
- ~140 deals ≈ 65 trades. Every statistic must show `n`; buckets with `n < 20`
  are suppressed or greyed (docs §8). **Exception: replay/training summaries
  (`store/training_store._summary`) are ungated** — a session is a handful of
  trades, so the floor blanked every rate permanently. `n` still ships with
  every metric.

## Hard rules

1. **Never `import MetaTrader5` outside `src/journal/adapter/`.** All MT5 access
   goes through the `MT5Client` Protocol in `adapter/base.py`. Everything else
   must be testable with `adapter/fake.py` and no MT5 running.
2. **`deals_raw` / `orders_raw` are append-only and never edited.** They are the
   source of truth; `trades` is derived and must be fully rebuildable via
   `journal rebuild`. Need a new field on `trades`? Derive it, never backfill.
3. **All timestamps are epoch milliseconds, integer, UTC.** Never naive
   datetimes, never local time. Convert to WIB only at display time. MT5
   timestamps are broker-server time — read `docs/mt5-deal-model.md` before
   touching any time field.
4. **`NULL` means unknown; `0` means "none set".** Matters most for
   `sl_initial` / `tp_initial`: unknown SL must be `NULL` and excluded from
   R-multiple stats, never treated as 0.
5. **Money and prices are `REAL`, volume is `REAL`.** Compare with tolerance
   (`abs(a-b) < 1e-9`), never `==`.
6. **Charts are cache, not data.** Anything in `cache/` must be reproducible
   from the DB. Never make the DB depend on a rendered file.
7. **Tests before implementation** for anything in `domain/` and `analytics/`.
   Use fixtures in `tests/fixtures/`, not live MT5.
8. **Do not add dependencies without asking.** Stack is `pyproject.toml`.
9. **Descriptive by default; `lab/` is the one predictive part.** Everything
   outside `src/journal/lab/` describes past data and must not generate signals
   or recommendations. `lab/` does predict, under three non-optional conditions:
   its output always renders with the model's out-of-sample expectancy and age;
   it never places, modifies, or sizes an order (`trade_commands` still needs a
   human click); it is never the input to another automated step. No "should I
   take this trade" features anywhere, `lab/` included.
10. **Never commit `data/`, `cache/`, or anything with a real account login.**
    Fixtures sanitised (login → 0, broker name stripped). `origin` is a
    **public** repo and this rule has been broken twice, so
    `tests/test_repo_hygiene.py` enforces it: scans every tracked file for
    funding references and — when `data/journal.db` is present — for the login,
    broker, server and funding comments read out of the live DB.
11. **Symbols are stored twice.** `symbol` = exactly what MT5 said (`XAUUSDc`);
    `symbol_base` = normalised (`XAUUSD`). Query MT5 with `symbol`, group stats
    by `symbol_base`. Normalisation lives in one function, `domain/symbols.py`,
    suffix set `{"c"}` — do not add suffixes speculatively.
12. **No MT5 constants outside `adapter/live.py` either.** Timeframes cross the
    Protocol as strings (`"M15"`), matching `candles.timeframe`; `live.py` maps
    them to `mt5.TIMEFRAME_M15`. Deal enums are our own `IntEnum`s in
    `adapter/base.py` (`DealType`, `DealEntry`, `DealReason`) and `live.py`
    asserts at init that they match the bridge. `domain/` must never contain a
    magic `3`.

## Commands

```bash
uv run journal doctor           # verify adapter: account info + last tick
uv run journal status           # store health: integrity, balance, unrebuilt
                                #   trades, backup age, live daemon. read-only,
                                #   no bridge; exit 1 only on wrong
uv run journal sync             # pull deals/orders into raw tables
uv run journal rebuild          # drop + rebuild trades from raw
uv run journal chart <trade_id> # render PNG to cache/
uv run journal backup           # snapshot the DB (safe under `live`/`serve`;
                                #   `journal live` also does this daily)
uv run journal restore          # put a snapshot back (newest, or --from FILE);
                                #   verifies source, refuses under `live`
uv run pytest                   # all tests, must pass before any commit
uv run pytest -k reconstruct    # the tests that matter most
```

## Read before you edit

- `domain/reconstruct.py`, `ingest/deals.py`, any time field →
  `docs/mt5-deal-model.md`. It lists the MT5 traps that cause silently wrong data.
- The schema → `src/journal/store/schema.sql`. Schema changes need a migration
  file; do not edit `schema.sql` in place once data exists.
- Anything in `lab/` → `docs/lab-models.md`. The label definitions and the purge
  gap break silently.

## Pipeline

**Never build on `main`** — branch + worktree per spec. **No repro → no fix**: a
bug becomes a failing test before it becomes a patch. Full skill order lives in
the `pipeline` skill; invoke it for any change touching more than one file.

## Definition of done

Tests pass, you have pasted the actual pytest output, and `journal rebuild`
still succeeds. Not when the code "looks right".

## Milestones and current state

`docs/HANDOFF.md § CURRENT STATE` (older entries in `docs/handoff-archive.md`).
Read it there; do not mirror it here.

## graphify

Beyond what the `PreToolUse` hook says: `graphify-out/wiki/index.md` beats raw
source browsing for navigation; read `GRAPH_REPORT.md` only for broad
architecture review; run `graphify update .` after modifying code.
