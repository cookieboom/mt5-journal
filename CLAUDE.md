# mt5-journal

Automated trading journal. Pulls trade history from MetaTrader 5, reconstructs
trades, stores OHLC centrally, renders charts on demand, and computes analytics.
Single user, local-only, macOS (Apple Silicon M4).

## This account (measured, not assumed)

- Adapter: `siliconmetatrader5` bridge, Docker container on `localhost:8001`.
- `margin_mode = 2` → **HEDGING**. One order = one position. `position_id` maps
  cleanly to one trade. `DEAL_ENTRY_INOUT` will not occur — raise if it does.
  `DEAL_ENTRY_OUT_BY` (Close-by) CAN occur — handle per docs.
- Account currency is **`USC` (US cents)**. Every `profit`/`commission`/`swap`/
  `risk_amount` in this DB is in cents. Never print a bare number as "$".
  Always render the currency code. R-multiple is a ratio and therefore unit-free
  — prefer it over absolute P&L everywhere in analytics.
- Broker uses a **`c` suffix**: the traded symbol is `XAUUSDc`, not `XAUUSD`.
  Store the raw symbol in `_raw` tables; group analytics on `symbol_base`.
- Several positions on the same symbol can be open at once (hedging). Analytics
  must not assume trades are non-overlapping.

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
    by `symbol_base`. Normalisation lives in one function, `domain/symbols.py`.

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

## Definition of done

A task is done when: tests pass, you have pasted the actual pytest output, and
`journal rebuild` still succeeds. Not when the code "looks right".

## Milestones

M0 doctor · M1 ingest deals · M2 reconstruct trades · M3 candles + renderer
· M4 SL/TP poller · M5 analytics (R, MAE/MFE) · M6 annotations + weekly report

Currently on: **M0**
