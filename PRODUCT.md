# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One trader — the person who owns this account and this repository. There is no
second audience, no team, no client, no public share link. Every decision is
allowed to be exact for this one person instead of general for many.

They use it in four situations, all of which the product must serve well
(confirmed, none is secondary):

1. **Post-session review** — after the trading day: read the report, tag and
   annotate trades, study the chart of what actually happened.
2. **Live during a trade** — MT5 terminal open, a position running: watch the
   live position, size from risk, drag SL/TP, get stopped from acting on a
   stale feed. Read under time pressure and at a glance.
3. **Replay / training reps** — deliberate practice on stored bars, away from
   live risk, with per-session summaries.
4. **Data-integrity operations** — sync, candle coverage, backup/restore,
   `journal status`. The journal is worth exactly as much as its data is
   honest.

## Product Purpose

Pull deal and order history out of MetaTrader 5 before the broker deletes it,
reconstruct it into trades, store OHLC bars centrally, render charts on demand,
and compute analytics over what already happened.

It exists because the broker's history is not durable: deals age out of the
terminal and stop being returned, and once gone there is nowhere to fetch them
from again. `deals_raw` / `orders_raw` are the append-only durable copy;
everything else is derived and rebuildable.

Success is that the trader can answer a question about their own past trading
— what happened, when, at what price, at what R — and trust the answer, years
after the broker stopped serving it.

## Positioning

**Built exactly around one trader.** One account, one broker, one workflow, one
set of symbols. No multi-account abstraction, no generic onboarding, no
tenant model. Every rule in the system was measured against this account rather
than assumed: hedging mode, `server_utc_offset_s = 0`, `USC` account currency,
the `c` symbol suffix. A commercial journal cannot make those measurements and
therefore has to guess or ask; this one already knows.

Two things follow from that and must not be traded away:

- **The data is the product.** Local-only, no cloud, no account data in the
  repository, and a store that can be rebuilt from raw at any time.
- **It describes; it does not advise.** Everything outside `src/journal/lab/`
  reports past data and generates no signals or recommendations. There is no
  "should I take this trade" feature anywhere.

## Operating Context

- macOS (Apple Silicon), local machine only. The UI is a React SPA served by
  FastAPI on localhost; the CLI (`journal sync|rebuild|status|serve|live|
  backup|restore|chart`) is the other half of the same product.
- MT5 is reached only through a `siliconmetatrader5` bridge in Docker on
  `localhost:8001`, behind one `MT5Client` Protocol. The web layer never
  touches the bridge.
- `journal live` is the long-lived daemon: ingests on position close, backs up
  daily, and executes commands queued from the UI. Live actions are queued
  commands, never direct broker calls from the browser.
- Surfaces in use: dashboard, live, chart (with replay, drawings, measurement,
  SL/TP drag), trades list + detail + shareable trade view, report, weekly,
  storage/data-health, commands, lab.
- Symbols traded: `XAUUSDc`, `BTCUSDc`, `EURUSDc`. Roughly 65 reconstructed
  trades from ~140 deals — a small-n dataset, permanently.

## Capabilities and Constraints

- **Money is in cents.** Account currency is `USC`. No money figure may ever be
  printed as a bare `$`. R-multiple is unit-free and is the preferred unit for
  analytics.
- **Small n is the normal case, not an edge case.** Every statistic ships its
  `n`. Buckets under `n = 20` are suppressed or greyed, with two deliberate
  exceptions: replay/training summaries, and the sequence block (max drawdown,
  longest streaks) which reports history rather than an average of it.
- **Times are epoch milliseconds, integer, UTC, everywhere in storage.** The
  broker clock is UTC (measured). Conversion to WIB (UTC+7) happens at display
  time only.
- **`NULL` means unknown, `0` means none set.** Unknown SL is excluded from
  R-multiple statistics, never treated as zero.
- Hedging account: several positions on one symbol can be open simultaneously.
  Trades overlap; nothing may assume otherwise.
- Charts and anything in `cache/` are cache, never data — always reproducible
  from the DB.
- `lab/` is the one predictive part, and only under three non-optional
  conditions: it always renders its out-of-sample expectancy and age, it never
  places/modifies/sizes an order, and it is never the input to another
  automated step.
- Terminology is MT5's and the trader's: deal, order, position, entry IN/OUT,
  R-multiple, MAE/MFE, R:R, SL/TP, lot, tick value, session bucket.
- Dependencies are not added without asking. Stack is fixed in
  `pyproject.toml` and `frontend/package.json`.

## Brand Commitments

No name, logo, or public identity — the product is never shown to anyone but
its user. Voice throughout the interface is measured and unflattering: state
the number and its `n`, do not celebrate, do not encourage, do not advise.

**The interface is written in Indonesian, with an English technical spine.**
This is a real convention already carried by the whole SPA, not a preference to
be re-litigated per surface:

- Prose, buttons, empty states, and error messages are Indonesian, lowercase
  and terse: `Memuat…`, `Gagal memuat`, `Batal`, `Tambah`, `Ke sekarang`,
  `tak ada posisi`, `basi · 214s`. Refusals lead with `Ditolak: <reason>`.
- Navigation labels and page titles stay English: Dashboard, Live, Chart,
  Trades, Report, Weekly, Commands, Storage, Lab.
- MT5 and trading vocabulary is never translated: trade, entry, exit, SL, TP,
  lot, R, win rate, expectancy, backfill, replay, live.
- CLI command names appear verbatim in code font inside Indonesian sentences
  (`jalankan <code>journal live</code>`).

Both languages stay lowercase and unexcited; Indonesian does not license
friendlier copy than English would.

## Evidence on Hand

Real, and the only real source: the live SQLite store at `data/journal.db`
(never committed), sanitised fixtures under `tests/fixtures/`, stored OHLC
candles, and the trader's own annotations and tags.

There are no customers, testimonials, benchmarks, pricing, licence, or
deployment story — this product has none, and future work must not invent them.
Account login, broker, and server names must never appear in any tracked file;
`tests/test_repo_hygiene.py` enforces it because the rule has been broken twice
and `origin` is public.

## Product Principles

1. **Raw data is sacred; everything else is derived.** If a number cannot be
   rebuilt from `deals_raw`, it is not trustworthy enough to display.
2. **Show `n`, or show nothing.** A statistic without its sample size flatters;
   this product refuses to flatter.
3. **Describe, never advise.** Past tense everywhere outside `lab/`, and `lab/`
   always wears its own expectancy and age.
4. **Honest about its own health.** Staleness, coverage gaps, a daemon running
   old code, a missing backup — the product says so rather than rendering a
   confident-looking chart over a hole.
5. **Exact for one trader beats general for many.** Measure this account, then
   encode what was measured.

## Accessibility & Inclusion

- Must be usable at laptop width in a single window (~1440px), sitting beside
  the MT5 terminal — density is tuned for that, not for a wide monitor.
- Phone check-ins are real: the key read-only surfaces must survive small
  screens.
- All displayed times are WIB (UTC+7), labelled as such; storage stays UTC.
- **Undecided:** whether a light theme is ever needed. The current interface is
  dark-only, but the user did not confirm dark-only as a commitment — treat it
  as the incumbent, not a rule.
