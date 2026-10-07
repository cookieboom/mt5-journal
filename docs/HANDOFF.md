# Handoff — read this first

## YOUR STANDING INSTRUCTIONS

You sit in the **architect / reviewer** seat on mt5-journal. Not the implementer.

- **You own:** `docs/`, analysis scripts, reviewing Claude Code's plans and diffs.
- **Claude Code owns:** `src/`, `tests/`, `pyproject.toml`. **Do not touch them.**
- **`schema.sql` lives in the repo and the repo is canonical.** The reviewer
  proposes schema changes in review; Claude Code applies them. The reviewer keeps
  no working copy — a fork you cannot see is a fork you will review against by
  mistake. It already happened once.
- **Your value comes from not sharing Claude Code's context.** Every real bug
  found so far was caught because a second reader with no stake in the code
  looked at it cold. Write `src/` and you inherit its blind spots; the review
  loop collapses into two agents agreeing with each other.
- **The design documents are the least reliable source in this project** — they
  have been wrong three times, this file included. The bridge, the fixtures, the
  account, and the broker's own report are authoritative. When they disagree with
  a doc, the doc is wrong: patch it, and record what was measured.
- **Never write trading signals, entry/exit logic, or position advice.** This
  tool describes patterns in past data. That is all it does.
- Read `CLAUDE.md` and `docs/mt5-deal-model.md` before acting on anything they
  cover. They are dense and load-bearing.

**This file holds only what lives nowhere else: current state, seats, roadmap,
error log.** It does not restate account facts, traps, or schema — those have one
home each, and a second copy is a future lie. Point, never duplicate.

---

## CURRENT STATE — update this section every session

**Last updated:** 2026-10-08

**2026-10-08 — indicators, spec §4: trade context + replay jump per period
(`feat/indicators-context`, spec
`docs/superpowers/specs/2026-10-08-indicators-trade-context-design.md`).**
The umbrella's deferred §3 plus the per-period half of its replay jump. No
migration, nothing stored.

- *Trade context* `domain/indicators/context.agreement`: a trade is "ya" when
  a same-side `signal()` fired in the last N closed bars before it (real: fill
  time; replay: `decision_msc`; paper: `requested_msc`). Unknown — never
  "tidak" — when warm-up (bars before `warm_from_msc` masked to NaN), too few
  bars, or a **store hole**: M1 traded in a bucket the frame lacks. Found on
  the live store: M5 holes 2026-08-14→28 and 09-07→20, native D1 stops
  2026-07-26; 3 of 137 real M5 trades had been labelled from stale bars.
- *API* `POST /api/indicators/context` (`web/trade_context.py`): real cells via
  `report.bucket_stat` (§8 gate, per-metric n), replay (blind only) and paper
  via `sim_stats`, ungated. Span over `MAX_BACKTEST_BARS` → newest bars only,
  older trades unknown, `clipped_from_msc` (M1 clips to 2026-03-16, 1.8 s; M5
  0.4 s).
- *Periods* `backtest.report` → `breakdown.all.periods{day,week,session}`
  (`sessions.session_window_msc`), each with `last_exit_msc` and
  `segment is|oos|mixed`. Jump = the existing replay endpoint, decision = period
  start, exit = last exit. `replay_session` (shared with the per-trade jump)
  now ends at the last bar `load_bars` has when nothing follows the exit —
  previously the newest week's jump made a range ending in the future.
- *FE* tester tab **Trade saya** (only beside a tester result — rule 9;
  window debounced; result kept across tab switches, refreshed on reopen
  after 5 min) and Breakdown → **Periode** (Hari/Minggu/Sesi, by total R,
  best/worst, IS / OOS / IS+OOS). Refused Replay jumps now show in the strip.
- `_stats`: bucket/period `total_r` is `null` with no known R (was 0).
- Real XAUUSDc M5 EMA cross: "ya" n 2 (gated), "tidak" n 132, win 35%, avg R
  −0.32 (n R 32). Browser-checked on a DB copy; period Replay opened a study
  session at the right cursor.

Four review waves; spec §5 lists what was kept on purpose. **Follow-ups
(pre-existing, not this branch):** the tester/chart read straight across
store holes (`load_frames` should expose the M1-missing buckets); `load_bars`
picks native vs M1 per range; `sim_stats.summary` gives `total_r = 0` with no
known R.

Gates: `pytest` **1346 passed, 9 skipped**; `ruff` clean; `mypy` clean (85);
`vitest` **465 passed**; `tsc` clean; `journal rebuild` + `verify` on a
snapshot: PASS. No migration — `journal live` / `serve` need only the usual
restart to pick up the code.

**2026-10-08 — indicators, spec §3: Strategy Tester (`feat/indicators-tester`,
spec `docs/superpowers/specs/2026-10-07-indicators-strategy-tester-design.md`).**
The umbrella's §4 backtest moved up to §3; real-trade context is deferred.

- *Language.* `signal(side, cond, stop=, target=, target_r=)` (stop/target are
  price series read at the signal bar; `target_r` constant, exclusive with
  `target`) and `exit(side, cond)`. New `Step` kinds `stop|target|exit`, so
  lookback and the engine need no special path; prefix-invariance covers them.
- *Simulator* `domain/indicators/backtest.py` drives `replay_eval.step_bar` —
  the one fill model (next open, stop-first). exit()/opposite/max-hold closes,
  ATR/points/script brackets, wrong-side → `rejected`, open at window end is
  never in stats. R net of the entry bar's spread (points × `symbol_specs.point`);
  unknown spread → fallback or `r = None`. A prefix test (random cut, `tf()`
  script) guards lookahead.
- *Report* IS/OOS split on decision time (OOS headline), PF, max DD in R,
  streaks, equity, hour/session/weekday buckets. Ungated (CLAUDE.md third §8
  exception); rule 9 rewritten to admit `signal()` beside a tester result.
- *API* `POST /api/indicators/backtest` (cap 200k bars, nothing stored) via
  `web/indicators.load_frames`, extracted from `compute`.
  `POST /api/indicators/backtest/replay` → **migration 014**
  `training_sessions.origin` blind|study; `career_summary` excludes study unless
  `include_study`.
- *FE* `StrategyTester.tsx` strip under the chart (live only): first run is a
  click, then re-runs on settings change / new closed bar. Tabs Ringkasan,
  Daftar trade (row → chart focus via `fitToRange`, Replay → study session),
  Breakdown, Pengaturan (stored per instance in the layout blob). Live markers =
  simulated trades, only with a result. Not done: the below-`md` sheet, and the
  equity curve is SVG rather than lightweight-charts.
- Verified on a DB copy, browser: XAUUSDc M5 EMA cross 90 d → OOS −0.14 R/trade,
  n 78; trade click scrolls to the 14:00 trade (+1.96R marker); Replay opens a
  study session at the cursor 50 bars before, range end 20 bars past the exit.
  90 d runs in 0.1 s, 365 d in 0.43 s.

**Restart `journal live` and `journal serve` after this merge** — migration 014;
old code refuses the migrated store.

Gates: `pytest` **1297 passed, 9 skipped**; `ruff` clean; `mypy` clean (83);
`vitest` **447 passed**; `tsc` clean; `journal rebuild` + `verify` on a
snapshot: PASS.

**2026-10-07 — indicators, spec §2: multi-timeframe `tf()`
(`feat/indicators-mtf`, spec
`docs/superpowers/specs/2026-10-07-indicators-mtf-design.md`).**

- *Language.* `tf("H1", expr)`: literal TF, inner scope = base series,
  constants, builtins, `x[n]`; a chart-TF variable or nested `tf()` refused.
  `Program.htfs`; `htf_lookback()` counts the inner warm-up in HTF bars; on the
  chart a `tf()` costs `ceil(H/L)`. A TF not above the chart's raises in
  `lookback`/`evaluate` (route → 400); `validate` reports `null` for that TF.
- *Mapping* `engine.map_closed`: HTF bar `[T,T+H)` shows on chart bar `[t,t+L)`
  only if `T+H <= t+L` (forming chart bar: `t`). Carries across closures; NaN
  once > 2 HTF buckets that have chart bars are missing.
- *No lookahead:* prefix-invariance extended — chart cut at k, HTF cut at what
  had closed by then, for a mixed script and every library script.
- *Service* loads each HTF from two HTF bars before the first chart bar plus
  its warm-up, drops unclosed HTF bars in replay, queues a fill per TF;
  `warm_from_msc` = the latest TF to become warm. Library: `lib:ema_htf`.
- Verified on a DB copy (service + HTTP): M5 `lib:ema_htf` over 24 h changed
  22 times, all on bars closing on the hour; replay stepping inside an hour
  leaves it still. Browser not re-checked — rendering path unchanged
  (results are chart-TF series).
- Known limit: H4 native only since 2026-01-30 (older H4 aggregates from M1).

Gates: `pytest` **1238 passed, 9 skipped**; `ruff` clean; `mypy` clean (81);
`vitest` **438 passed**; `tsc` clean; `journal rebuild` + `verify` on a
snapshot: PASS.

**2026-10-07 — indicators, spec §1: engine, script language, chart
(`feat/indicators-engine`, spec
`docs/superpowers/specs/2026-10-07-indicators-design.md`).** First of four
specs (next: §2 multi-timeframe, §3 indicator context of real trades, §4
backtest + replay jump + the Rule 9 amendment).

- *Language.* Python-syntax subset parsed with `ast`, every node whitelisted,
  never `eval`. `x[n]` only with a literal n >= 0. Window args must be
  constant, so `lookback()` sizes the warm-up from the script; capped at
  20,000 bars. `domain/indicators/` (`lang`, `builtins`, `engine`, `frame`,
  `library/*.ind`: SMA EMA EMA-cross BB RSI MACD ATR Stoch VWAP Donchian ADX).
- *No lookahead* is a parametrised property test over every builtin, every
  library script and a composed script: values at bar t equal the values
  computed from bars[:t+1]. Logic is three-valued (NaN = unknown, rule 4).
  RMA needed 8n warm-up, not 4n — the warm-up test caught it.
- *Service/API* `web/indicators.py`, `/api/indicators/*`. Scripts and layout in
  `app_prefs` (no migration, so a running `journal live` is not refused). A
  `session_id` clips to bars closed by the end of the cursor bar on any TF;
  forming bar never in replay; no signal on a forming bar.
- *Chart.* One series per plot; non-price panes below at stretch 1. Found and
  fixed a latent bug: the chart-type switch removed the main series before
  adding its replacement, and an emptied pane is deleted — an indicator pane
  became pane 0. `useIndicators` does full fetch + two-bar tail fetch; every
  result is clipped to the last drawn bar. Signal markers: replay only until §4.
- Verified in the browser on a DB copy: live (EMA cross + RSI + MACD panes,
  panel values) and replay (markers, stepping +10 bars, nothing past the
  cursor).

Gates: `pytest` **1203 passed, 9 skipped**; `ruff` clean; `mypy` clean (81);
`vitest` **438 passed**; `journal rebuild` on a snapshot: 141 trades, verify
PASS.

**2026-10-07 — the leftovers from 10-06 (`fix/load-active-and-mypy-baseline`).**

- *mypy baseline gone.* All 73 modules are checked, with no override. Narrowing
  follows the repo idiom (`assert x is not None  # why`), and each site was read
  before it was asserted. Two of them were reachable, and both were pinned by a
  failing test first: `journal doctor` crashed (`UnboundLocalError`) on a tick
  with no `time`; `reconstruct` died with a bare `TypeError` on a BUY/SELL deal
  whose `price`/`volume`/`symbol` is NULL (`deals_raw` allows it). That deal
  now raises a `ValueError` naming the ticket. Also: `set_annotation` and the
  web `_row()` no longer type a guaranteed row as Optional, and `bar_rows()`
  replaces two copies of the excursion comprehension.
- *`lab.store.load_active`* derives `<cache_dir>/models/<id>.joblib` instead of
  opening the stored relative `artifact_path` (it only worked from the repo root).
- *Stash `caca926` dropped.* Its content (bar-close countdown, rollover
  promotion) was already on `main` (`b96e220` + later).
- *`main` == `origin/main`.* The divergence noted below is resolved.

Gates: `pytest` **1008 passed, 8 skipped**; `ruff` clean; `mypy` clean (73/73);
`journal rebuild` on a snapshot of the live store: 132 trades, identities 1 and
2 PASS. Still owed by a human: start `journal live` (down since 2026-09-21), and
retrain the lab models.

**2026-10-06 — architecture cleanup: web hardening, layering, file size
(`refactor/architecture-cleanup`, spec
`docs/superpowers/specs/2026-10-06-architecture-cleanup-design.md`).** One
commit per item; every bug below was a failing test before it was a patch.

- *Security.* The web answered any `Host`, so a DNS-rebinding page could reach
  every route, `/api/live/open` (a real order) included — reproduced, 200.
  `web/local_only.guard`: non-loopback `Host` → 403; a write whose `Origin`
  is present and not loopback → 403.
- *Validation.* Untyped `Body(...)` routes 500'd on a non-JSON body and the
  prefs routes stored arrays/strings verbatim. Pydantic models
  (`web/schemas.py`, no new dependency); every error response now carries
  `error` (the SPA showed a bare "HTTP 4xx" for lab and validation errors);
  prune refuses `older_than_days < 1`.
- *Lab models are gone — retrain them.* The storage routes read the constant
  `cache` instead of the app's cache dir, so `tests/test_storage_api.py`
  wiped the REAL `cache/` on every `pytest` run in the main checkout. All 24
  `lab_models` rows (4 active: 18, 20, 22, 24) point at `.joblib` files that
  do not exist; `cache/models/` last changed 2026-08-13. Fixed: cache routes
  use the app's `cache_dir`, and clear-cache spares `cache/models/`.
- *Layering.* `domain/`, `store/`, `adapter/` import nothing above them.
  `domain/trade_window.py` (TF ladder, was in `render/chart.py`),
  `domain.MIN_N`, `store/health.py` → `journal/health.py` (an aggregator; it
  imported `web.app` and `ingest`), `execute.size_order` (was a web view),
  `candles_store.prune_before` and `training_store` helpers (the web layer
  held INSERT/UPDATE/DELETE).
- *Structure.* `web/app.py` 1,125 → 92 lines: routes in `web/routes/*.py`,
  one APIRouter per area; OpenAPI schema identical before/after for all 62
  paths. `journal live`'s thread orchestration → `ingest.live.run_daemon`.
  The web migrates once at startup; a store at any other version than the
  code's is refused (newer = a process running old code). `Chart.tsx`
  696 → 564 (`useCompetitiveReplay`, `PaperSidePanel`, `ChartLoadState`,
  `ScenarioEvalOverlay`); CandleChart's line selection →
  `lib/priceLines.ts` (pure, tested).
- *Other bugs fixed.* Leaving competitive replay during the result pause
  started a new scenario anyway (uncancelled timer). The stale-bundle check
  watched `tailwind.config.js`; the repo has `.ts`, and postcss was missing.
  `TradeView` arrow-key tests were flaky (raced the neighbor fetch).
- *Tooling (approved, rule 8).* ruff and mypy are dev deps and run in CI.
  ruff is pinned to bug-finding rules (F, E4/E7/E9, B). mypy checks 67 of 73
  modules; six (cli, reconstruct, resample, ingest.live, web.paper,
  web.training — 96 errors, all Optional adapter fields validated in one
  function and used in another) are baselined in pyproject.toml. Every
  None-arithmetic site mypy flagged was read: none was a reachable NULL bug.
- *Not done.* CandleChart's gesture effects stay together: they share
  mutable refs and capture-phase listener ORDER matters (text tool before
  measure); splitting them buys little and risks that order. The mypy
  baseline. `lab.store.load_active` ignores its `cache_dir` and opens the
  stored relative `artifact_path`, so it depends on the working directory.

Gates: `pytest` **1003 passed, 8 skipped**; hygiene with the live-DB arm 3/3;
`vitest` **411 passed / 53 files**; `tsc -b && vite build` clean; `journal
rebuild` on a snapshot of the live store: 132 trades, identities 1 and 2 PASS;
`journal serve` smoke-tested on the same snapshot. The first `journal live`
after merge will report `health.py` as changed code (the module moved) — one
restart clears it.

## Who does what

| Seat | Tool | Owns | Never touches |
|---|---|---|---|
| **Architect / reviewer** (you) | Cowork | `docs/`, analysis scripts, reviewing Claude Code's plans | `src/`, `tests/`, `schema.sql` |
| **Implementer** | Claude Code | `src/`, `tests/`, `schema.sql`, `pyproject.toml` | `docs/`, `CLAUDE.md` |

**This separation is the point, not bureaucracy.** The reviewer's value comes
entirely from *not sharing the implementer's context*. If you start writing
`src/`, you become the implementer, you inherit its blind spots, and the review
loop degrades into two agents agreeing with each other.

If Claude Code's plan looks fine to you, say so — but read the actual diff or the
actual data first, not the summary of it.

---

## How this project has been worked

1. **One milestone per session.** Plan mode on. Name the files that may be
   touched. Approve only after reading the plan properly.
2. **Definition of done = pasted evidence.** Real pytest output, real command
   output. "Tests pass" without the output is not done.
3. **Commit per milestone, then `/clear`.** Context quality degrades long before
   the window fills.
4. **Knowledge goes in `docs/`, not `CLAUDE.md`.** CLAUDE.md is a ~110-line
   instruction budget. Every line added weakens the others. If Claude Code starts
   ignoring a rule, suspect a bloated CLAUDE.md before suspecting the model.
5. **Measure, do not recall.** See the error log below.
6. **The human runs anything that writes to git or touches the live account.**
   Fixture recording included — sanitisation review is a human job.
7. **State dependencies out loud.** When handing over more than one task, say
   which are parallel and which gate which. An instruction that arrives alongside
   doubt about whether it still applies cannot be executed with confidence.

---

## Error log — why "measure, don't recall" is a rule

Every one of these was caught by machinery deliberately built for it, not by luck.

| What | Who was wrong | Caught by |
|---|---|---|
| `DEAL_TYPE_COMMISSION = 6` in the design docs | **The docs.** Bridge reports `BONUS=6, COMMISSION=7`. | The `live.py` enum assertion (CLAUDE.md rule 12) |
| `Candle.time` seconds → `candles.time_msc` column | The plan. Would have silently produced empty charts at M3. | Independent review of `base.py` against `schema.sql` |
| Sanitising `comment -> ""` on all 140 deals | **The reviewer's own spec.** Destroyed the string `"Archived deals"` — the literal answer to the 14.50 question — and every `[sl]`/`[tp]` marker. | Counting non-empty comments in the recorded fixture |
| "The 14.50 might be swap the bridge is dropping" | The hypothesis. `swap = 0.00` on all 140 deals and in MT5's own report. | Reading the report instead of theorising about it |
| "A widening residual means the broker archived more history" | **The reviewer's M1 spec.** Archiving moves no money, so the residual never budges. Shipped as a false docstring in `ingest/deals.py`. | Reasoning through what archiving actually does to a balance |
| The reviewer's `schema.sql` working copy | **The reviewer.** It was never installed; Claude Code wrote a better `reconciliations` table (dropped a redundant column, dropped an unused state, better placement). The reviewer had been reviewing against a file that did not exist. | Reading the repo instead of the working copy |
| This file claiming the 14.50 was "BLOCKED ON A HUMAN" after it was resolved | **This file.** A stale handoff is worse than none: it sends a fresh reader to redo finished work, then hands them a decision rule that is now wrong. | Auditing the repo against what was actually asked for |
| A second `schema.sql` at repo root, frozen since M0.1 (`e653905`), diverged from `src/journal/store/schema.sql` — missing `accounts.balance`/`equity`, and a `reconciliations` table pre-dating the M2 review fix (3 statuses instead of 2, different column order) | **Nobody's edit — an old tracked file nobody deleted.** `db.py` only ever reads `src/journal/store/schema.sql`; the root copy was dead but readable, and reading it first gives you wrong facts about the schema with no error to warn you. | A fresh reviewer session diffing both files byte-for-byte before trusting either |
| `probe_rates.py` printing "VERDICT: no dependency. live.py is correct as written" | **The reviewer's own probe.** It tested `symbol_select` on `BTCUSDc` — a traded symbol already in the container's persistent Market Watch — so both arms of the experiment were the same arm. The script asserted a conclusion its design could not reach, in the confident voice reserved for measurements. A probe that overclaims is worse than no probe: it closes a question that is still open. | Re-reading the probe's own method after seeing the result it wanted |
| `record_fixtures.py`'s M3 rates-recording addition sourced trade selection from the *live* pull the script was already doing, to pick which trade's candle window to fetch | **Claude Code's M3 implementation, first pass.** The script has always refreshed *every* fixture on each run (its original, correct job); adding rates on top of that fresh pull meant a routine re-run silently drifted `deals.json`/`orders.json`/`account.json`/`symbols.json` away from the frozen 2026-07-16 snapshot 8 M1/M2 tests hardcode (140 deals, 68 trades, balance 6047.22, …) — the account had genuinely traded more since. | `pytest` — 8 tests went red immediately after a live re-run, before any commit |
| M4's `poll_once` silently dropped a real SL observation: two DIFFERENT states for the same position landing in the same millisecond collided on the `sl_tp_snapshots` primary key, and `INSERT OR IGNORE` kept only the first | **Claude Code's M4 implementation, first pass.** The bug wouldn't fire at a real 5s poll interval, but did fire immediately under a fast test loop — exactly the gap between "works in the demo" and "works under load" this project's testing culture exists to close. | An ad-hoc verification script run before the formal test suite existed, asserting on the actual row count in `sl_tp_snapshots` rather than trusting the reported `snapshots_written` count |
| M4's `journal poll` (no `--once`) reported cycle activity only via `logging.info`, invisible in a terminal with no handler configured | **Claude Code's M4 implementation, first pass.** A long-running foreground command a human is meant to watch would have looked hung the entire time even while working correctly — the CLI's only feedback was a single summary line printed after Ctrl+C. | Self-review of the diff before commit, not a test — logging visibility isn't something `pytest` checks by default; worth remembering next time a command runs in the foreground indefinitely |
| M5's first MAE/MFE draft added a `distance_to_money()` helper and refactored `domain/risk.py` to use it | **Claude Code's M5 plan, first draft.** Unnecessary: `risk_amount`'s `tick_size`/`tick_value`/`volume` cancel algebraically in `mae_money/risk_amount`, leaving `mae_r = mae / abs(open_price - real_sl)` — no money conversion, no risk.py change, ever needed. | A design-review pass (Plan agent) done deliberately *before* writing code, working the algebra through by hand |
| M5's first MAE/MFE draft filtered candles by "bar open time falls inside `[open,close]`" | **Claude Code's M5 plan, first draft.** `candles.time_msc` is a bar's OPEN time; the filter would have returned `(None,None)` for most of the 11 sub-M1 trades (min 1s), since a fast trade rarely contains a bar-open boundary at all — a coverage gap silently misreported as "no data". | The same design-review pass, cross-checked against the measured duration profile (docs §7) instead of assuming candles align to trade windows |
| M5's first MAE/MFE draft scanned every timeframe stored for a symbol, reasoning "OHLC bars preserve true extremes at any granularity" | **Claude Code's M5 plan, first draft.** True for one timeframe alone, but this account is hedging (CLAUDE.md line 26): two overlapping trades of different durations can sit at different TFs, and a coarser trade's much wider bar would leak into a shorter trade's excursion if the TF column were ignored. | The same design-review pass, reasoning through what "hedging + per-trade TF choice" implies for a symbol-wide scan |
| M5's *corrected* design still risked a bulk in-memory candle preload (mirroring M4's `sl_tp_snapshots` pattern) picking up a different, disjoint trade's stale cluster on the same symbol+TF | **Claude Code's M5 implementation, working through the TF fix.** The central `candles` table pools every trade's window (schema.sql: "Dedupes across trades on the same symbol/day") — a "nearest preceding row anywhere" scan isn't scoped to one trade the way a bounded SQL query is. | Reasoning through the bulk-preload approach's failure mode before implementing it, not after a test caught it — the regression test (`test_excursion_scoped_per_trade_not_contaminated_across_timeframes`) was written to prove the FIX, not to find the bug |

The pattern: **the design documents are the least reliable source in this
project.** The bridge, the fixtures, the account, and the broker's own report are
authoritative. When they disagree with a doc, the doc is wrong — patch it, and
note what was measured.

---

## Roadmap

| | Milestone | Status |
|---|---|---|
| M0 | Adapter protocol, symbol normalisation, DB bootstrap, `doctor` | done |
| M0.1 | Candle→ms, probed enums | done |
| M0.2 | Re-record fixtures with comments preserved | done (`a15cc5e`) |
| M1 | Ingest deals/orders → `_raw` tables, `journal verify` | done (`1d086c2`) |
| M1.1 | Archive detector, bridge-free verify, offset COALESCE | done (`1d086c2`) |
| M1.2 | Model `equity` on `Account`; live smoke passed | done (`10d9141`) |
| M2 | `reconstruct.py`: deals → trades, `rebuild`, §6 identity 2 | done (`48a4cc7`) |
| M2.1 | Review fixes: zero-risk R guard, NULL time_msc reject, guard dedup | done (`48a4cc7`) |
| M3 | Candle store + mplfinance renderer (`journal chart <position_id>`) | done (`797849b`) |
| M4 | SL/TP poller — makes `sl_initial` knowable, and outruns the archiver | done (`0f1b088`) |
| M5 | MAE/MFE + core `journal report` (money stats + gated R-stats) | done (`11cac94`) |
| M5.1 | Session bucketing + EA/discretionary behaviour breakdowns | done (`3a5d198`) |
| M6 | Annotations + manual/auto tags (`journal annotate`/`tag`) | done (`24ce64b`) |
| M6.1 | Weekly Markdown report (`journal weekly`) | done (`a989eac`) |
| M7 | Web dashboard on localhost (`journal serve`) — read-mostly + annotation/tag writes | done |
| M8 | Per-symbol breakdown (`by_symbol`) + dedicated `/report` web page | done |
| M9 | Live positions + trade interaction + auto-ingest on close + UI redesign (`journal live`, `/live`) | **done — merged to main.** Live-verified 2026-07-23 (real account/bridge): auto-ingest-on-close, `/live` observe, and the order-send path to the broker all proven. The browser UI → live data (`open_positions`/`/api/live`) → `journal live` → bridge round trip WORKS and has for a long time. Only unmeasured: an *accepted* order landing — blocked solely by the MT5 container's AutoTrading toggle (a terminal setting, not code) — plus a browser visual/contrast pass. |
| Frontend rework | Jinja2 → React SPA served at `/`; Jinja UI retired at the Phase 5 cutover | done (`8d1de45`, 2026-07-24 — Jinja2 templates, `/static/app.css`, form-POST write routes and the `jinja2`/`python-multipart` deps all retired) |
| M10 | Lab: regime + entry-timing models on candle data (`/lab` page, badge on `/live`) | done (`b4250a5`, 2026-08-08 — migration 010 / `SCHEMA_VERSION = 10`, `docs/lab-models.md`; see 2026-08-06 above) |
| Paper trading | Virtual account traded from the live chart (migration 013) | done (`3943074`, 2026-08-18) |
| Native adapter | `MetaTrader5` package on a Windows host; `get_client()` picks by platform | done (`b818b93`, 2026-08-21) |
| Live loop split | `journal live` as two threads (positions / symbol data), `LockedMT5Client` | done (PR #13, 2026-09-07); first run against the real bridge unverified |

M0–M3 delivers the original ask: an automatic journal with charts. **Done.**
M4 onward — poller, analytics, annotations — is what makes the journal worth
returning to daily rather than a one-shot report.

---

## Account facts

**One home: `docs/mt5-deal-model.md` §7.** All measured against the live bridge.
Do not copy them here — a second copy drifts, and then two documents disagree
with no way to tell which one lies. Read §7.

The three worth a pointer, because they change how you work:

- **Trap 16** — the broker deletes history. The most important fact in the
  project. It is why the journal exists.
- **§9** — n=68. Every report must show `n` and suppress buckets under 20. A rule,
  not a caveat.
- **An EA touched part of this history** (12 deals with `magic != 0`, 6 closes
  with reason EXPERT). At M5.1, EA and discretionary trades must be separated
  or both populations are meaningless.

---

## Open questions

- [ ] Was `symbol_select` ever actually *needed* in `copy_rates_range`? Still
      unproven either way — the underlying probe was and remains inconclusive
      (it tested an already-selected symbol). M3 added the call as insurance
      regardless (`live.py`, item 0): idempotent, one call per windowed fetch,
      matches its two neighbours. The code question is closed; the empirical
      one — does this bridge actually need it — is not, and may never be worth
      resolving now that the insurance is cheap and in place.
- [ ] The real identifiers are still in the git **history**. `7464753` scrubbed
      the fixture; 2026-08-12 scrubbed the four places that had pasted one back
      in (see CURRENT STATE) and added the guard that keeps the working tree
      clean. Neither rewrote history, and `origin` is a **public** GitHub
      repository. Removing them from history means a force-push over published
      commits — a decision with its own cost, and the only part still open.

**Closed:** `httpx` missing from the dependencies (it is not — it is in
`[dependency-groups].dev`; a clean `uv sync` into a fresh worktree venv ran the
whole suite, `test_storage_api.py` included, 2026-08-12) · the 14.50 gap
(archived deals — see CURRENT STATE) · standalone
commission deals (none; MT5's report confirms `commission = 0.00`) ·
`BTCUSDc`/`EURUSDc` specs (M1 `symbol_specs`: tick_value 0.1 / 0.01 / 1.0 —
genuinely distinct, gold's transfer nowhere) · `MaxBars` (1,000,000 — doc §7) ·
per-symbol session hours (BTC 24/7, EUR ≈24h×5d, XAU ≈23h×5d — doc §7) ·
chart timeframe selection (duration ladder, ≤60 trade-bars, floor M1 — M3,
CURRENT STATE above) · chart cache identity (`position_id`, never `trades.id`
— M3, CURRENT STATE above).
