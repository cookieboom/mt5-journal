# mt5-journal

Automated trading journal. Pulls trade history from MetaTrader 5, reconstructs
trades, stores OHLC centrally, renders charts on demand, and computes analytics.
Single user, local-only, macOS (Apple Silicon M4).

## This account (measured, not assumed)

- Adapter: `siliconmetatrader5` bridge, Docker container on `localhost:8001`.
- `margin_mode = 2` → **HEDGING**. One order = one position. `position_id` maps
  cleanly to one trade. Only `entry` values `0` (IN) and `1` (OUT) exist in this
  account's history — `INOUT` and `OUT_BY` have never occurred. Raise on both.
- **`server_utc_offset_s = 0` — confirmed.** Broker server clock is UTC. Session
  analytics need no conversion. WIB = UTC+7, display only. Re-measure each sync
  anyway.
- Account currency is **`USC` (US cents)**. Every `profit`/`commission`/`swap`/
  `tick_value`/`risk_amount` is in cents. Never print a bare number as "$".
  R-multiple is a ratio and therefore unit-free — prefer it over absolute P&L
  everywhere in analytics.
- **`symbol_specs.currency_profit` is the symbol's quote currency, NOT the unit
  of `tick_value`.** `XAUUSDc` reports `currency_profit = USD` while values are
  in USC. The unit of any money figure is always `accounts.currency`.
- Symbols traded: `XAUUSDc`, `BTCUSDc`, `EURUSDc`. The only suffix in use is
  `c`; the unsuffixed symbols do not exist on this server. `XAUUSDc`:
  `tick_size=0.001`, `tick_value=0.1`, `contract_size=1.0` (1 lot = 1 oz).
- Several positions on the same symbol can be open at once (hedging). Analytics
  must not assume trades are non-overlapping.
- ~140 deals ≈ 65 trades. Every reported statistic must show `n`, and buckets
  with `n < 20` must be suppressed or greyed. See docs §8. **Exception:
  replay/training summaries (`store/training_store._summary`) are ungated** — a
  session is a handful of trades, so the floor blanked every rate permanently
  and the panel carried no information. `n` still ships with every metric.

## Hard rules

1. **Never `import MetaTrader5` outside `src/journal/adapter/`.** All MT5 access
   goes through the `MT5Client` Protocol in `adapter/base.py`. Everything else in
   the codebase must be testable with `adapter/fake.py` and no MT5 running.
2. **`deals_raw` / `orders_raw` are append-only and never edited.** They are the
   source of truth. `trades` is derived and must be fully rebuildable from raw
   via `journal rebuild`. If you need a new field on `trades`, derive it — do not
   backfill it by hand.
3. **All timestamps are epoch milliseconds, integer, UTC.** Never store naive
   datetimes. Never store local time. Convert to WIB only at display time.
   MT5 timestamps are broker-server time, not UTC — see `docs/mt5-deal-model.md`
   before touching any time field.
4. **`NULL` means unknown; `0` means "none set".** This matters most for
   `sl_initial` / `tp_initial`. A trade with unknown SL must have `NULL` and must
   be excluded from R-multiple stats, never treated as 0.
5. **Money and prices are `REAL`, volume is `REAL`.** Compare with tolerance
   (`abs(a-b) < 1e-9`), never `==`.
6. **Charts are cache, not data.** Anything in `cache/` must be reproducible from
   the DB. Never make the DB depend on a rendered file.
7. **Tests before implementation** for anything in `domain/` and `analytics/`.
   Use fixtures in `tests/fixtures/`, not live MT5.
8. **Do not add dependencies without asking.** Current stack is `pyproject.toml`.
9. **Descriptive by default; `lab/` is the one predictive part.** Everything
   outside `src/journal/lab/` describes patterns in past data and must not
   generate trade signals or recommendations. `lab/` trains models on candle
   data and does predict. Its output is bound by three conditions that are not
   optional: it is always rendered together with the model's out-of-sample
   expectancy and its age; it never places, modifies, or sizes an order —
   `trade_commands` still requires a human click; and it is never the input to
   another automated step. Do not add "should I take this trade" features
   anywhere, including inside `lab/`.
10. **Never commit `data/`, `cache/`, or anything containing a real account
    login.** Fixtures must be sanitised (login → 0, broker name stripped).
    `origin` is a **public** repository and this rule has been broken twice, so
    it is now enforced: `tests/test_repo_hygiene.py` scans every tracked file
    for funding references, and — when `data/journal.db` is present — for the
    login, broker, server and funding comments read out of the live DB itself.
11. **Symbols are stored twice.** `symbol` = exactly what MT5 said (`XAUUSDc`);
    `symbol_base` = normalised (`XAUUSD`). Query MT5 with `symbol`. Group stats
    by `symbol_base`. Normalisation lives in one function, `domain/symbols.py`,
    and its suffix set is `{"c"}` — do not add suffixes speculatively.
12. **No MT5 constants outside `adapter/live.py` either.** Rule 1 covers the
    import; this covers the values. Timeframes cross the Protocol as strings
    (`"M15"`), matching `candles.timeframe` — `live.py` maps `"M15"` →
    `mt5.TIMEFRAME_M15`. Deal enums are our own `IntEnum`s in `adapter/base.py`
    (`DealType`, `DealEntry`, `DealReason`); `live.py` asserts at init that they
    match the bridge's values. `domain/` must never contain a magic `3`.

## Commands

```bash
uv run journal doctor           # verify adapter: account info + last tick
uv run journal sync             # pull deals/orders into raw tables
uv run journal rebuild          # drop + rebuild trades from raw
uv run journal chart <trade_id> # render PNG to cache/
uv run journal backup           # snapshot the DB (safe while `live`/`serve` run;
                                #   `journal live` also does this daily on its own)
uv run pytest                   # all tests, must pass before any commit
uv run pytest -k reconstruct    # the tests that matter most
```

## Read before you edit

- Touching `domain/reconstruct.py`, `ingest/deals.py`, or any time field
  → read `docs/mt5-deal-model.md` first. It lists the MT5 traps that cause
  silently wrong data.
- Touching the schema → read `src/journal/store/schema.sql`. Schema changes need
  a migration file; do not edit `schema.sql` in place once data exists.
- Touching anything in `lab/` → read `docs/lab-models.md` first. The label
  definitions and the purge gap are the parts that are easy to break silently.

## Pipeline

**Never build on `main`** — branch + worktree per spec. **No repro → no fix**:
a bug becomes a failing test before it becomes a patch.

The full order of superpowers skills for a feature or a bug fix lives in the
`pipeline` skill (`.claude/skills/pipeline/SKILL.md`). Invoke it when starting
any change touching more than one file.

## Definition of done

A task is done when: tests pass, you have pasted the actual pytest output, and
`journal rebuild` still succeeds. Not when the code "looks right".

## Milestones and current state

Milestone list, what is merged, and what is pending a human run all live in
`docs/HANDOFF.md § CURRENT STATE`. Read it there; do not mirror it here.

## graphify

A `PreToolUse` hook already tells you to run `graphify query` / `path` /
`explain` before searching or reading. Three things it does not say:

- `graphify-out/wiki/index.md` beats raw source browsing for navigation.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review, or
  when `query`/`path`/`explain` do not surface enough context.
- Run `graphify update .` after modifying code (AST-only, no API cost).
