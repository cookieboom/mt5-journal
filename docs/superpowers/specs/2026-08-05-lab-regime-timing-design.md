# Lab — regime classification and entry timing on candle data

Date: 2026-08-05
Status: approved, ready for planning

## Problem

The journal holds two years of XAUUSDc candles and has no way to ask a
quantitative question of them. The owner wants to train simple predictive
models on that data — one that labels the current market regime, and one that
scores whether entering now would reach a take-profit before a stop-loss — and
wants the training itself to be visible and adjustable rather than a black box
that emits a number.

Two constraints shape everything below.

**The account is small in trades but large in bars.** `trades` holds about 65
rows, far too few to train anything. `candles` holds 708k XAUUSDc M1 bars back
to 2024-08 and 23k H1 bars back to 2022-08. The models therefore learn from
price data only. They never see the owner's own trades.

**This changes rule 9.** CLAUDE.md currently says the tool "never generates
trade signals or recommendations". The owner explicitly asked for model output
on `/live`, next to the order buttons. Rule 9 is replaced as part of this work
(Task 1) with a narrower rule: the tool is descriptive by default, `lab/` is
the only predictive part, its output must always be displayed together with its
out-of-sample score and model age, and it never places an order on its own —
`trade_commands` still requires a human click.

## Measured baseline

Candle depth as of 2026-08-05:

| symbol   | timeframe | bars    | range              |
|----------|-----------|---------|--------------------|
| XAUUSDc  | M1        | 708,188 | 2024-08 → present  |
| XAUUSDc  | H1        | 23,222  | 2022-08 → present  |
| XAUUSDc  | M5        | 51,461  | 2025-11 → present  |
| XAUUSDc  | M15       | 13,859  | 2026-01 → present  |
| BTCUSDc  | M1        | 16,758  | 2026-01 → present  |
| EURUSDc  | M1        | 33      | effectively empty  |

XAUUSDc is the only symbol with real depth. The design is symbol-agnostic; other
symbols become trainable as their candle coverage fills, with a minimum-rows
guard rather than a hardcoded symbol list.

## Design

### Two stages

**Stage 1 — regime.** A three-class classifier over `trend_up`, `trend_down`,
`range`, giving the context in which a timing score is read.

**Stage 2 — timing.** A binary classifier for "would an entry at this bar reach
TP before SL", trained separately per regime.

Inference chains them: predict the regime for the current bar, then score timing
with that regime's model.

### Labels

Both labels are computed from candles alone, look forward by `N` bars, and are
therefore only definable for bars at least `N` from the end of the data. The
last `N` bars are excluded from training and are the bars scored at inference.

**Regime label.** Over the next `N` bars compute the efficiency ratio

```
ER = (close[t+N] - close[t]) / sum(|close[i] - close[i-1]| for i in t+1..t+N)
```

`ER > er_threshold` → `trend_up`; `ER < -er_threshold` → `trend_down`;
otherwise `range`. Default `er_threshold = 0.35`, `N = 24`. Both configurable
from the UI. `ER` is undefined when the denominator is zero (a completely flat
window); those bars are labelled `range`.

**Timing label (triple barrier).** For each bar `t` and each side (long, short):

- entry price is `open[t+1]`, not `close[t]` — `close[t]` is not tradeable and
  matches what the replay engine already does with next-bar-open fills.
- `R = k * ATR14(t)`, default `k = 1.0`. Stop is 1R against the entry, target is
  `rr * R` in favour, default `rr = 2.0`. Both configurable.
- Walk forward at most `N` bars from `t+1`. If a single bar's range touches both
  the stop and the target, the **stop wins** — pessimistic, matching the replay
  engine's SL-first rule.
- Outcome is one of `tp_first`, `sl_first`, `timeout`.

The model target is binary: `tp_first` = 1, everything else = 0. `timeout` is
kept as a distinct outcome in the metrics layer, where its realised P&L
(`close[t+N]` versus entry, in R) contributes to expectancy instead of being
scored as a flat loss.

`side` is a feature rather than a separate model, so one bar produces two rows.
Both rows always land in the same walk-forward fold, so this does not leak.

### Features

Fifteen features, all computed strictly from bars at or before `t`. Every one
except `side` is toggleable from the UI; `side` is always on, because the label
itself is defined per side.

| feature | definition |
|---|---|
| `ret_1`, `ret_5`, `ret_20` | log return over 1, 5, 20 bars |
| `atr_rel` | `ATR14(t) / close[t]` |
| `ema20_dist`, `ema50_dist` | `(close[t] - EMA) / ATR14(t)` |
| `body_ratio` | `abs(close - open) / (high - low)` |
| `upper_wick`, `lower_wick` | wick length / `(high - low)` |
| `range_pct` | `(high - low) / close` |
| `vol_rel` | `tick_volume / SMA(tick_volume, 20)` |
| `spread` | the `candles.spread` column |
| `hour_utc`, `dow` | categorical, from `time_msc` (server clock is UTC) |
| `side` | categorical, long or short |

Zero-denominator bars (`high == low`) yield `NULL` for the ratio features; rows
with any `NULL` feature are dropped before training and the count is reported.

`candles.tick_volume` and `candles.spread` are nullable. `NULL` means unknown,
not zero (rule 4). If either column is `NULL` for more than 5% of the selected
range, that feature is disabled for the run and the UI says why, rather than
silently dropping most of the dataset. Where `spread` is unknown, the expectancy
cost deduction falls back to a per-symbol default entered in the training form,
and the metrics label that number as assumed rather than measured.

### Models

Every training run fits **two** models on the same data and reports them side by
side:

- **logistic regression** (scikit-learn) — the glass box. Standardised inputs,
  coefficients rendered as a horizontal bar chart so the owner can read what the
  model is weighting.
- **LightGBM** — the stronger tabular learner. Gain-based feature importance
  rendered the same way.

If LightGBM does not beat logistic regression on out-of-sample expectancy, the
UI says so plainly. Both are stored; either can be activated.

Timing models are fit per regime. If a regime holds fewer than 500 rows in a
training fold, that fold falls back to a pooled model and the UI marks the
result as pooled.

Random seeds are fixed and recorded in `config_json`, so a stored model can be
reproduced exactly from the database.

### Evaluation

Purged walk-forward, five sequential folds in time order. No shuffling, no
random splits. Between each training block and its test block sits a gap of `N`
bars — the labels look `N` bars ahead, so without the gap the answer leaks
backwards into training.

Reported per fold and in aggregate, all out-of-sample:

- **expectancy in R** — the headline metric
- win rate, and the share of `tp_first` / `sl_first` / `timeout`
- AUC
- a calibration curve: predicted probability bucket versus realised win rate.
  This is the check on whether the probabilities mean anything.
- regime confusion matrix
- a **random-entry baseline**: same side mix, same SL/TP, entries drawn at
  random over the test block. Model expectancy is only interesting relative to
  this.

Every figure carries its `n`. Buckets with `n < 20` are suppressed per CLAUDE.md
§8. Expectancy is computed net of the spread recorded on the entry bar;
gross-of-cost numbers are not shown anywhere.

### Storage

Migration `010_lab_models.sql`, `SCHEMA_VERSION = 10`.

```sql
CREATE TABLE IF NOT EXISTS lab_models (
    id            INTEGER PRIMARY KEY,
    created_ms    INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    stage         TEXT NOT NULL,          -- 'regime' | 'timing'
    regime        TEXT,                   -- NULL for stage='regime' or a pooled model
    kind          TEXT NOT NULL,          -- 'logreg' | 'lgbm'
    config_json   TEXT NOT NULL,          -- N, k, rr, er_threshold, features, seed
    metrics_json  TEXT NOT NULL,          -- walk-forward results, n per fold
    train_from_ms INTEGER NOT NULL,
    train_to_ms   INTEGER NOT NULL,
    n_rows        INTEGER NOT NULL,
    artifact_path TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 0
);
```

Fitted models are joblib files under `cache/models/<id>.joblib`. Rule 6 holds:
`config_json` plus the fixed seed is enough to retrain the identical model from
the database, so `cache/` remains disposable. Deleting `cache/` leaves the rows
intact and the Retrain button rebuilds the artifacts. Scoring against a model
whose artifact is missing returns a clear "artifact missing, retrain" state
rather than a stack trace.

`active` selects the model used by `/live`: at most one per
`(symbol, timeframe, stage, regime)`. `regime` is `NULL` for regime-stage and
pooled models, and SQLite treats `NULL`s as distinct in a unique index, so the
index keys on `COALESCE(regime, '')` and applies only to active rows:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS lab_models_active
    ON lab_models (symbol, timeframe, stage, COALESCE(regime, ''))
    WHERE active = 1;
```

Activating a model clears the previous active row in the same group inside one
transaction, so the index never has to reject a legitimate switch.

`lab_models` is not touched by `journal rebuild`; that command rebuilds `trades`
from raw and must keep succeeding unchanged.

**Write-lock discipline.** Training must not hold the WAL writer across the fit.
The run is two-phase, the same shape as the `deals.sync` and `fill_range` fixes:
one short read transaction pulls the candles into memory, the transaction
closes, features/labels/fit run with no open cursor, and a single short write
inserts the `lab_models` rows at the end.

### Modules

```
src/journal/lab/
  features.py   # candle DataFrame -> feature matrix. Pure, no DB, no MT5
  labels.py     # triple-barrier + regime labelling. Pure
  train.py      # purged walk-forward, fit, metrics
  store.py      # lab_models CRUD + artifact read/write
  score.py      # load active models, score recent bars
```

`features.py` and `labels.py` are pure functions over a DataFrame. They hold the
logic that must be correct and are tested without a database.

HTTP endpoints:

| method | path | purpose |
|---|---|---|
| POST | `/api/lab/train` | run a training job, return metrics + new model ids |
| GET | `/api/lab/models` | list stored models with metrics and age |
| POST | `/api/lab/models/{id}/activate` | mark a model active |
| GET | `/api/lab/score` | regime + timing probability for the latest closed bar |
| GET | `/api/lab/regimes` | predicted regime per bar over a range, for chart shading |

### UI

**`/lab`** — a new SPA page reusing `CandleChart` and `useChartData`:

- left: the training form — symbol, timeframe, `N`, `k`, `rr`, `er_threshold`,
  a checkbox per feature, and a Train button.
- centre: the candle chart with the background shaded by regime, a probability
  strip beneath the candles, and markers on bars above the chosen threshold.
- right: metric cards per fold — expectancy in R, win rate, `n`, AUC, the
  calibration curve, and the random-entry baseline. Logistic regression and
  LightGBM shown side by side, with coefficient and importance bars.
- bottom: stored models with their age and metrics, and an Activate button.

Training is synchronous behind a spinner. On H1/M15 this is seconds; on 708k M1
bars LightGBM on twelve features is still seconds. If a run ever grows past
comfortable, the upgrade path is a background job — noted as a `ponytail:`
comment rather than built now.

**`/live`** — a badge showing the current regime, P(tp_first) for long and
short, the active model's age ("trained 12 days ago", red past 30 days), and
that model's out-of-sample expectancy, which is never separable from the
probability it is displayed beside. The live chart background is shaded by
regime. Both update **on bar close**, not per tick: the models are trained on
closed bars and an intrabar score would be a different, untested quantity.

### Tests

Written before implementation, per rule 7, using synthetic and fixture candle
series rather than live MT5.

1. **No lookahead.** Mutate bars after `t`; every feature value at `t` must be
   unchanged. This is the single most important test in the design.
2. **Triple barrier.** Hand-built fixtures covering: stop wins when one bar
   touches both barriers, timeout at exactly `N`, entry taken at `open[t+1]`,
   and correct mirroring for the short side.
3. **Regime labelling.** Synthetic straight-line trend, synthetic flat sawtooth,
   and the zero-denominator flat window.
4. **Walk-forward split.** No train/test index overlap, and the purge gap of `N`
   bars is present between every train and test block.
5. **Expectancy maths.** A small dataset with a known answer, including the
   timeout contribution and the spread deduction.
6. **Store round-trip.** Write a model, read it back, score with it; and the
   missing-artifact path returns the retrain state.
7. `uv run journal rebuild` still succeeds with `lab_models` populated.

## Risks, stated up front

1. **This will most likely find no edge.** Candle-only features plus a gradient
   booster on XAUUSD is heavily trodden ground. Out-of-sample expectancy will
   probably sit at or below the random-entry baseline once the spread is
   deducted. The evaluation layer is built so that outcome is visible rather
   than hidden — that is the point of the calibration curve and the baseline.
2. **Cost matters more than accuracy here.** A 55%-accurate model can still lose
   money after spread, which is why expectancy in R net of spread is the
   headline number and raw accuracy is not.
3. **The real hazard is `/live`.** A probability rendered next to an order
   button is persuasive regardless of whether it is any good. Model age and
   out-of-sample expectancy are therefore rendered inseparably from the
   probability, and the display can never trigger an order by itself.

## Out of scope

- Any use of the owner's own `trades` as training data. 65 rows is not a
  dataset. Comparing model output against the owner's historical entries is a
  plausible later feature, not this one.
- Automatic retraining. Training is manual, from the button, so that every model
  in the store has a human behind it and a visible age.
- Deep learning, sequence models, and multi-symbol transfer.
- Position sizing from model confidence. Sizing stays with the existing
  risk-based lot calculator.

## Dependencies

Two new dependencies, approved by the owner (CLAUDE.md rule 8):
`scikit-learn` and `lightgbm`. Both ship arm64 wheels for Apple Silicon.
`joblib` arrives as a scikit-learn transitive dependency and is used for model
artifacts.
