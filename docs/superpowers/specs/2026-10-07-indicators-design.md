# Indicators — scripts, chart, MTF, trade context, backtest

**Status:** design, awaiting review. **Date:** 2026-10-07.

User asks: run indicators on the live and replay chart, from authoring through
display, behaviour, backtest and stats; and a menu that jumps replay to where a
script succeeded most / least.

Decisions taken in brainstorming (2026-10-07):

| Question | Decision |
|---|---|
| Authoring | **Free scripts.** Python-like syntax, parsed with stdlib `ast`, whitelist of nodes. The built-in catalog (EMA, RSI, …) is just a library of shipped scripts. |
| Where computed | **Python, one source.** `domain/indicators/`. Chart, stats and backtest call the same evaluator. No TS port. |
| Rule 9 | **Amended:** `signal()` markers may render on the live chart, under lab-style conditions (below). |
| Replay jump | **Both** per-signal and per-period lists. |
| Delivery | **Four specs, four branches, in dependency order.** This document is the umbrella; §1 is detailed enough to plan, §2–§4 get their own spec file when their turn comes, building on the decisions fixed here. |

No new dependencies: `ast` (stdlib), pandas/numpy (already used by `lab/`),
lightweight-charts 5 panes (`addSeries(..., paneIndex)`) for the UI.

---

## Cross-cutting invariants (hold for all four specs)

1. **No lookahead, enforced by property test.** For every builtin and every
   shipped script: `evaluate(bars[:k])` equals `evaluate(bars)[:k]` for random
   `k` (NaN-equal, `abs < 1e-9`). This one test replaces per-function reasoning
   and must never be weakened (same status as `lab`'s no-lookahead guard).
2. **Replay never sees past the cursor — enforced server-side.** A compute
   request carrying `session_id` is clipped to the session's `cursor_msc` by the
   server; the client cannot ask for more.
3. **NaN = unknown (rule 4).** Warm-up bars, NULL `tick_volume`/`spread`
   sources, division by zero → `null` in JSON, never `0`. Insufficient warm-up
   history is reported (`warm_from_msc`), not papered over.
4. **Nothing is stored on `trades` (rule 2).** Indicator values are derived on
   demand from `candles` + script source. Anything persisted (backtest runs) is
   reproducible and keyed by `source_hash`.
5. **The forming bar is never final.** Its values are flagged `final: false`,
   are drawn but never counted in stats, and never fire a live signal marker
   until the bar closes.
6. **Money in USC, analytics in R** (CLAUDE.md). Backtest reports R first.

### Rule 9 amendment (lands with spec 4, text in CLAUDE.md)

> `lab/` **and indicator `signal()` markers** may be predictive. A signal
> marker on the live chart renders only when the script has a stored backtest
> run for that symbol/timeframe, and always alongside that run's out-of-sample
> expectancy (R), `n`, and age. It never places, modifies or sizes an order,
> and is never the input to another automated step. No "should I take this
> trade" feature anywhere.

Until spec 4 ships, `signal()` markers render in **replay only** (historical,
behind the cursor); the live chart shows plots only.

---

## §1 Engine, script language, chart (live + replay) — spec 1

### 1.1 Language

Python syntax, a strict subset. `ast.parse`, then a validator walks the tree
and rejects any node type not on the whitelist. The tree is evaluated over pandas Series by a small
interpreter. **No `eval`/`exec`, ever.**

```python
# EMA cross (shipped library script)
fast_len = input(20, min=1, max=500, title="Fast")
slow_len = input(50, min=1, max=500, title="Slow")
fast = ema(close, fast_len)
slow = ema(close, slow_len)
plot(fast, title="EMA fast", color="accent")
plot(slow, title="EMA slow", color="muted")
signal("long",  crossover(fast, slow))
signal("short", crossunder(fast, slow))
```

- **Statements:** assignment `name = expr`, and calls to `input`, `plot`,
  `hline`, `signal`. Nothing else (no `if`/`for`/`while`/`def`/`import`/
  `lambda`/comprehensions/attributes).
- **Expressions:** numbers, booleans, names, `+ - * / ** %`, unary `-`/`not`,
  comparisons (chainable), `and`/`or`, `where(cond, a, b)`, calls to
  whitelisted builtins, and `x[n]` with `n` an integer **literal ≥ 0** (n bars
  ago, `shift(n)`). A negative or non-literal index is a parse error. This is
  the language-level half of invariant 1.
- **Series in scope:** `open high low close volume spread hl2 hlc3 ohlc4 time`.
  `volume`/`spread` are nullable (invariant 3).
- **Builtins (v1):** `sma ema wma rma stdev highest lowest change roc`,
  `atr tr rsi macd_line macd_signal macd_hist bb_upper bb_mid bb_lower`,
  `stoch_k stoch_d vwap(session="day")` (UTC day, the broker clock is UTC),
  `donchian_upper donchian_lower adx`, `crossover crossunder`,
  `abs min max log sqrt where nz` (`nz` only where the author chooses it; it
  is never implicit). Multi-output indicators are separate functions, so there
  are no tuples in the language.
- **`input(default, min=, max=, step=, title=, options=)`:** a typed parameter
  the UI renders as a form field. Only numeric, bool and string-option inputs
  are allowed.
- **`plot(expr, title, color, pane="price"|"new"|"<name>", style="line"|"histogram"|"dots", width)`.**
  `color` is a **theme token name** (`accent`, `pos`, `neg`, `muted`, `c1..c8`
  categorical), never hex. This keeps the one-palette rule (`lib/theme.ts`).
- **`hline(value, title, pane)`** for fixed levels such as RSI 30/70.
- **`signal("long"|"short", cond)`** marks the bars where `cond` is true. It
  is not drawn on the live chart before spec 4 (see the Rule 9 amendment).
- **Limits:** source ≤ 20 kB, ≤ 200 statements, AST depth ≤ 50, a window
  argument ≤ 5 000, and a 2 s evaluation budget. Over a limit → a 400 with
  line/column.

**Errors** carry `line`, `col`, and a message. The editor shows them inline.

### 1.2 Warm-up

The validator computes `lookback` statically: for each builtin a window cost
(`ema(span)` → `3·span` so it converges; `rsi`/`atr`/`rma(n)` → `3·n`; `sma(n)`
→ `n`; `x[n]` → `n`), composed by **summing** along nesting and taking the
**max** across siblings. A compute request for `[from, to]` loads
`from − lookback·tf_ms` (capped). If the store has fewer bars, the response
says so via `warm_from_msc`, and values before it are `null`, not wrong.

### 1.3 Modules

```
src/journal/domain/indicators/
  lang.py      parse + validate (+ lookback), returns a checked Program
  builtins.py  the builtin functions, pure pandas
  engine.py    evaluate(Program, bars, inputs) -> Result(plots, hlines, signals)
  library/     shipped scripts, *.ind (read-only, versioned in git)
```

`domain/` keeps its layering (imports nothing above it). TDD per rule 7.

### 1.4 Storage — `app_prefs`, no migration

User scripts live in `app_prefs` key `indicator_scripts` as
`{"scripts": [{"id", "name", "source", "updated_ms"}]}`. Ids are server-assigned
and never reused. `source_hash` (`sha256(source)`) is derived when spec 4
needs it, not stored. Library scripts are files and are never seeded into the
DB; "Edit a copy" creates a user script. A table was considered and rejected:
a migration bumps the schema version, and a running `journal live` on older
code would then be refused for a change that it never reads.

The **chart layout** (which scripts are attached, with what inputs, visibility
and pane order) lives under key `indicator_layout`, versioned and normalised
the way `chartPrefs` is. It is a single layout shared by live and replay, so a
training session uses exactly what the live chart uses.

### 1.5 API

| Route | Purpose |
|---|---|
| `GET/POST/PUT/DELETE /api/indicators/scripts` | CRUD on user scripts. Library scripts are listed with `readonly: true`. |
| `POST /api/indicators/validate` | `{source}` → `{inputs, plots, lookback}` or errors. Used live by the editor. |
| `POST /api/indicators/compute` | `{script_ref, inputs, symbol, timeframe, from, to, session_id?, include_forming?}` → per plot `[[t, v]…]`, hlines, signals, `warm_from_msc`, and a `final: false` last point when the forming bar is included. |
| `GET/PUT /api/indicators/layout` | The chart layout blob (pydantic-validated, size-capped like drawings). |

`session_id` set → clip to the session's cursor (invariant 2).
`include_forming` is honoured only without `session_id`. The forming bar comes
from the same source `/api/candles/live` reads.

### 1.6 Frontend

- `hooks/useIndicators.ts`: fetches compute results for the visible window and
  extends as `useChartData` loads older bars or `loadUpTo` advances. In live it
  re-polls at the forming-bar cadence, sending the last `lookback + 1` bars
  (the server recomputes only the tail). In replay it re-fetches on each cursor
  step, passing `session_id`.
- `CandleChart`: new prop `indicators: IndicatorRender[]`, one series per plot
  (`LineSeries`/`HistogramSeries`), with `paneIndex` from the layout. Replay
  signal markers go through the existing `createSeriesMarkers`, merged with
  `props.markers`. A chart-type switch must re-attach indicator series (same
  trap as the existing `chartType` effect).
- `ChartInfoPanel`: the hovered bar's value for each visible plot, `—` for
  null.
- `IndicatorPanel` (drawer, the same `Sheet` pattern): attached list
  (toggle / inputs / remove / reorder), "Add" from library and user scripts,
  and "Edit script". It opens the
  **ScriptEditor**: a monospace textarea with server validation on a debounce
  and line-anchored errors. No editor dependency (rule 8).
- Pane legend: name, inputs, and current value, top-left of each pane.

### 1.7 Tests (written first)

- `lang`: whitelist acceptance and rejection per node type, negative or
  dynamic index rejected, limits, error line/col, lookback arithmetic.
- `builtins`: golden values against hand-computed pandas references on
  `tests/fixtures` candles, plus the **prefix-invariance property test** over
  every builtin and every library script.
- `engine`: NaN propagation, `null` for NULL volume/spread, warm-up reporting.
- API: session clip (a request past the cursor returns nothing beyond it),
  `include_forming` ignored in replay, validation 400 carries line/col,
  migration lockstep test.
- Frontend: series attach and detach, pane assignment, survival across a
  chart-type switch, hover values.

---

## §2 Multi-timeframe — spec 2 (outline)

- `tf("H1", expr)` evaluates `expr` on H1 bars and maps the result onto the
  chart's bars. **Only HTF bars that have closed** at the LTF bar's close are
  visible: an LTF bar `[t, t+ltf)` sees the HTF bar `[T, T+htf)` only when
  `T + htf ≤ t + ltf`. The forming HTF bar is never used. Invariant 1's
  property test is extended to mixed TFs.
- HTF bars come from the store for that TF. Where the store lacks them, and
  only there, they are resampled from the chart TF via `domain/resample.py`.
  Gaps stay gaps (memory: chart gaps are genuine closures).
- Lookback is per TF: `tf("H4", ema(close, 200))` needs 600 H4 bars of
  warm-up, loaded separately.
- Only higher TFs: `tf()` to a lower or equal TF is a parse error.

## §3 Indicator context of real trades and replay — spec 3 (outline)

> **Re-sequenced 2026-10-07 (user decision):** this section is DEFERRED. The
> spec built third is the Strategy Tester —
> `2026-10-07-indicators-strategy-tester-design.md` — which takes §4's backtest,
> the Rule 9 amendment and the per-trade replay jump.

- For each real trade, evaluate a chosen script on the trade's `symbol`
  (using `symbol` to read candles and `symbol_base` to group results, per
  rule 11) at the **last closed bar before `entry_time`**, on a chosen TF.
  This reads plot values and whether each `signal()` was true.
- Buckets: a boolean condition (true/false) or quantile bins of a plot. Per
  bucket: `n`, win rate, avg R, and total R. Trades with unknown SL are
  excluded from R (rule 4).
- **§8 gating applies to real trades** (`n < 20` greyed or suppressed). The
  same view over replay/paper positions is **ungated** (the `sim_stats`
  exception), still showing `n`.
- Overlapping hedged trades count individually. Nothing assumes they are
  non-overlapping.
- UI: a new section on Report (or a tab) with script + TF picker → bucket
  table and an R histogram per bucket. Computed on demand. Rule 2 means
  nothing is stored on `trades`; caching is a cache-dir concern only.

## §4 Backtest + replay jump — spec 4 (outline)

**Engine.** Walk the bars and, on each, feed `signal()` hits into
`domain/replay_eval.step_bar`. This is the same fill model as replay: entry at
the **next bar's open** and stop-first when SL and TP both sit inside one bar.
A backtest therefore cannot be more optimistic than training.

**Exit spec (form, not script):** SL = ATR multiple / fixed points / a named
plot; TP = R multiple / a named plot / none; max hold in bars; opposite
signal closes; position policy = one at a time, or overlap allowed (hedging).

**Cost:** the bar's `spread` where known. Where it is unknown, the user enters
a spread and the report says how many fills used it (rule 4, never silently
0). Commission and swap stay 0, which is genuine for this account (memory).

**Out-of-sample:** chronological split, 70/30 by default. The report shows IS
and OOS side by side, and **OOS is the headline**. Every metric carries `n`.
Metrics reuse `sim_stats.summary` plus the report sequence block (max
drawdown, streaks), with breakdowns by hour, session and day of week.

**Storage:** a `backtest_runs` table (migration) keyed by `script_id`,
`source_hash`, inputs, symbol, TF, range, split and exit spec, with the
results JSON and `created_ms`. It is derived and reproducible. A run whose
`source_hash` no longer matches the script is shown as stale.

**Live signal markers** (the Rule 9 amendment) read the newest
non-stale run for that script/symbol/TF. The badge shows OOS expectancy, `n`
and age. No run → no live markers. Markers fire on **closed** bars only
(invariant 5). They have no order button; ordering stays a manual click
elsewhere.

**Replay jump.** Two lists on the backtest result:
- *Per signal:* every simulated trade, sortable by R (best/worst). Click → a
  new training session with `cursor = signal_time − N bars` (default 50, user
  adjustable) and `range_end = exit + M bars`.
- *Per period:* day / week / session, ranked by total R and win rate with `n`.
  Click → a session starting at the beginning of that period.
- Sessions created this way are flagged `origin = 'backtest'` (a
  `training_sessions` column added by migration). They are **excluded from
  the career/training summary by default**, because cherry-picked winners
  would inflate blind-training stats. The summary offers an "include study
  sessions" toggle.
- The script's own `signal()` markers show in that replay behind the cursor,
  as in §1.

**Not in v1 (YAGNI, add when asked):** parameter sweeps / optimisation (the
fastest way to overfit 65 trades' worth of reality), walk-forward, alerts or
notifications, indicators on the trade PNG (`render/chart.py`), and
multi-symbol portfolio backtests.

---

## Risks

- **Lookahead.** The biggest one. It is guarded three ways: the language
  (no negative index), the property test, and the server-side cursor clip.
- **Overfitting and false confidence.** OOS is the headline, `n` is
  everywhere, live markers carry their OOS expectancy, and there is no
  optimiser.
- **Live load.** A tail-only recompute on each poll keeps it small. The
  2 s budget plus the size limits keep a pathological script from
  stalling `journal serve`.
- **Script sandbox.** No `eval`; the AST whitelist and resource limits are the
  sandbox. The server is already loopback-only with Host/Origin checks
  (`web/local_only`).
