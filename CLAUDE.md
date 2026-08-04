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
8. **Do not add dependencies without asking.** Current stack: python 3.12,
   sqlite3 (stdlib), pandas, mplfinance, typer, pytest.
9. **This tool describes patterns in past data. It never generates trade signals
   or recommendations.** Do not add "should I take this trade" features.
10. **Never commit `data/`, `cache/`, or anything containing a real account
    login.** Fixtures must be sanitised (login → 0, broker name stripped).
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
uv run pytest                   # all tests, must pass before any commit
uv run pytest -k reconstruct    # the tests that matter most
```

## Layout

```
src/journal/
  adapter/   base.py (Protocol) | live.py (siliconmetatrader5) | fake.py (fixtures)
  ingest/    deals.py | candles.py | poller.py
  domain/    reconstruct.py   <- deals -> trades. The hard part.
  store/     schema.sql | db.py | migrations/
  render/    chart.py         <- mplfinance
  analytics/
  cli.py
```

## Read before you edit

- Touching `domain/reconstruct.py`, `ingest/deals.py`, or any time field
  → read `docs/mt5-deal-model.md` first. It lists the MT5 traps that cause
  silently wrong data.
- Touching the schema → read `src/journal/store/schema.sql`. Schema changes need
  a migration file; do not edit `schema.sql` in place once data exists.

## Pipeline

Run the superpowers skills, in order. They carry the how; this lists the when.

**New feature** (anything touching more than one file):

1. `brainstorming` → spec in `docs/specs/<name>.md`. No code yet.
2. `writing-plans` → numbered tasks, one task = one commit-able unit.
3. `using-git-worktrees` → branch + worktree per spec. Never build on `main`.
4. `executing-plans`, and inside each task `test-driven-development` (rule 7).
5. `requesting-code-review` on the whole branch → `receiving-code-review` →
   fix wave → re-review until clean.
6. `verification-before-completion`, then `finishing-a-development-branch`.
   Fast-forward merge, no force-push.

**Bug fix**:

1. `systematic-debugging` first — before proposing any fix.
2. `test-driven-development`: reproduce as a failing test. No repro → no fix.
3. Root cause, not symptom: grep every caller of the function you are about to
   touch. The fix goes at the shared choke point, not in each caller.
4. `verification-before-completion`: full `uv run pytest`, not just the new
   test — the fix moved a shared path.
5. Single-file, single-cause → straight to `main`. Otherwise branch.

Skip the ceremony for typos, comments, and one-line constants.

## Definition of done

A task is done when: tests pass, you have pasted the actual pytest output, and
`journal rebuild` still succeeds. Not when the code "looks right".

## Milestones

M0 doctor · M1 ingest deals · M2 reconstruct trades · M3 candles + renderer
· M4 SL/TP poller · M5 analytics (R, MAE/MFE) · M6 annotations + weekly report
· M7 web dashboard (`journal serve`) · M8 by_symbol + `/report` page · M9 live
positions + trade interaction + auto-ingest on close + UI redesign (`journal
live`, `/live`; trading ON by default, 1.00-lot cap)
· **Frontend rework** (Jinja→React SPA, served at `/`; Jinja UI retired at
Phase 5 cutover)

Currently on: **Frontend React rework COMPLETE — the SPA is the sole UI, served
at `/` (Jinja retired, Phase 5 cutover). M9 live-bridge smoke still pending a
human run — see docs/HANDOFF.md. Draggable SL/TP chart lines (replay
instant-commit + live precision-dialog → ConfirmModal) MERGED to main
2026-08-01 (`13fc345`); in-browser visual pass with the MT5 bridge running
still pending a human run — see memory `sltp-drag-2026-08-01`.**

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
