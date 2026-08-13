# mt5-journal

[![ci](https://github.com/cookieboom/mt5-journal/actions/workflows/ci.yml/badge.svg)](https://github.com/cookieboom/mt5-journal/actions/workflows/ci.yml)

An automated trading journal for MetaTrader 5. It pulls deal and order history
out of the terminal, reconstructs it into trades, stores OHLC bars centrally,
renders charts on demand, and computes analytics over what already happened.

Single user, local-only, no cloud, no account data in this repository.

## Why it exists

The broker deletes history. Deals age out of the terminal and stop being
returned, and once they are gone there is nowhere to fetch them from again.
`deals_raw` is append-only and never edited: it is the durable copy, and every
other table is derived from it and fully rebuildable.

**It describes past trades. It does not tell anyone what to trade.** Everything
outside `src/journal/lab/` is descriptive by construction; `lab/` trains models
on candle data and is bound to never place, modify, or size an order.

## Stack

Python 3.12 · SQLite (WAL) · FastAPI · Typer · pandas/mplfinance ·
React + TypeScript + Vite + lightweight-charts. Dependencies are managed with
[uv](https://docs.astral.sh/uv/); the frontend with npm.

All MT5 access goes through one `MT5Client` Protocol in
`src/journal/adapter/`, so the entire test suite runs against a fake adapter
with no terminal, no bridge, and no broker.

## Quick start

```bash
brew install libomp        # macOS only: lightgbm's wheel dlopens it, `lab/` needs it
uv sync
uv run journal doctor      # is the adapter alive?
uv run journal sync        # pull deals/orders into the raw tables
uv run journal rebuild     # derive trades from raw
uv run journal status      # is the store healthy? (read-only, no bridge)
uv run journal serve       # the dashboard on localhost
```

`journal backup` snapshots the one file that cannot be re-synced, `journal
restore` puts a snapshot back, and `journal live` runs the daemon that ingests
on position close, backs up daily, and executes commands queued from the UI.

## Tests

```bash
uv run pytest                     # Python
npm --prefix frontend test        # frontend unit tests
npm --prefix frontend run build   # type check + bundle
```

CI runs all three on every push and pull request.

## Documentation

- `CLAUDE.md` — the hard rules. Read before changing anything.
- `docs/mt5-deal-model.md` — the MT5 traps that cause silently wrong data.
- `docs/HANDOFF.md` — current state, roadmap, and the error log.
- `docs/lab-models.md` — label definitions and the purge gap.
