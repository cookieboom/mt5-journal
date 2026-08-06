# Lab models — regime + entry-timing

`src/journal/lab/` is the one predictive corner of this tool. CLAUDE.md rule 9
scopes prediction to this package alone: everything else describes patterns in
past data; `lab/` trains models on candle data and does predict. Its output is
bound by three conditions that are not optional — it is always rendered
together with the model's out-of-sample expectancy and its age; it never
places, modifies, or sizes an order (`trade_commands` still requires a human
click); and it is never the input to another automated step. There is no
"should I take this trade" feature here, and there must never be one.

Read this file before touching anything in `lab/`. The label definitions and
the purge gap are the parts that are easy to break silently — a shifted sign,
a leaked future bar, or a shrunk purge gap will not fail loudly, it will just
make the walk-forward numbers optimistic.

## The two stages

**Regime** — a 3-class classifier (`trend_up` / `trend_down` / `range`) that
labels each bar using the *realised* efficiency ratio over the next `n_bars`:

```
ER = (close[t+n] - close[t]) / sum(|close[i] - close[i-1]|)  over i in (t, t+n]
```

`|ER| > er_threshold` (default `0.35`) is a trend in the direction of the net
move; otherwise the bar is `range`. A window with zero total movement has an
undefined ratio and is treated as `range`.

**Timing** — a binary classifier per side (`long` / `short`) predicting
whether a trade entered at that bar hits its target before its stop, i.e.
`P(tp_first)`. One timing model is trained per regime when there is enough
data (see Pooled fallback below), plus a `side_code` feature so one estimator
covers both sides.

Both stages look `n_bars` ahead by construction, so the trailing `n_bars` rows
of any range are unlabelled — those are exactly the rows scored at inference
time in `lab/score.py`.

## Label definitions (the part that must match the replay engine)

`lab/labels.py::barrier_labels` runs a triple-barrier simulation per side,
matching the replay engine's conventions exactly so a lab number and a replay
number mean the same thing:

- **Entry is `open[t+1]`.** `close[t]` is not tradeable — you cannot execute
  at the price you just saw print.
- **Stop distance is `k_atr * ATR(14)` at `t`** (default `k_atr=1.0`); target
  is `rr * stop_distance` (default `rr=2.0`).
- **When a single bar's range touches both the stop and the target, the STOP
  wins ("pessimistic: stop before target" in the code).** A bar has no
  intrabar path recorded in OHLC data — assuming the favourable order is
  exactly how naive backtests lie. This is the single most important line in
  the file; do not "fix" it toward optimism.
- If neither barrier is touched within `n_bars`, the trade times out and is
  scored by its actual R at bar `t + n_bars`'s close.
- `r_net` subtracts an estimated spread cost (`spread * point / (k_atr *
  ATR)`) from the barrier's gross R. Where `candles.spread` is `NULL` (unknown,
  not zero — CLAUDE.md rule 4), `default_spread_points` stands in; the API
  response's `spread_assumed` flag tells the caller when that substitution
  happened.

## Features

`lab/features.py::PRICE_FEATURES` (14 columns, all backward-looking — pandas
`rolling`/`ewm` only, never `shift(-n)`):

| feature | meaning |
|---|---|
| `ret_1`, `ret_5`, `ret_20` | log-return over the last 1/5/20 bars |
| `atr_rel` | ATR(14) as a fraction of close |
| `ema20_dist`, `ema50_dist` | distance from the 20/50-bar EMA, in ATR units |
| `body_ratio` | candle body as a fraction of its high-low range |
| `upper_wick`, `lower_wick` | wick length as a fraction of range |
| `range_pct` | bar's high-low range as a fraction of close |
| `vol_rel` | tick volume relative to its 20-bar mean |
| `spread` | `candles.spread`, raw |
| `hour_utc`, `dow` | hour-of-day and day-of-week, UTC (this account's `server_utc_offset_s = 0`, confirmed) |

`side` is **not** a feature column — it belongs to the label, and
`lab/train.py::build_dataset` appends it as `side_code` (see Cross-module
contract below) only when assembling the training matrix.

`vol_rel` and `spread` are nullable at the source (`tick_volume`, `spread`).
`lab/features.py::usable_columns` drops a feature — not the rows — when its
source column is unknown for more than 5% of the range, because dropping every
row with an unknown spread would throw away most of a range fetched before
that column was populated. The training response's `dropped_features` records
what was cut and why.

## Why the purge gap exists

`lab/evaluate.py::purged_folds` builds expanding-window, forward-only splits:
never shuffled, because a random split leaks the future into the past and
produces a beautiful, meaningless score. Between every train block and its
test block sits a purge gap of `n_bars * 2` rows (both sides' worth, since
`build_dataset` interleaves long and short rows for the same bar). The gap
exists because every label already looked `n_bars` ahead — without it, a bar
just inside the test block would have been labelled using bars that leaked
into the training block on the other side of the split.

A fold whose train or test side would be empty is dropped rather than
reported; `purged_folds` returns fewer folds (or none) rather than a fold with
missing data.

## Reading the metrics table

Order matters — read expectancy against its baseline first, calibration
second, accuracy last:

1. **Expectancy vs. baseline.** `expectancy_r` is the mean `r_net` over only
   the rows the model would have taken (`proba >= threshold`);
   `baseline_expectancy_r` is the mean `r_net` over *every* row in the block —
   what entering at random would have returned. A model that doesn't beat its
   own baseline is not doing anything, regardless of what its accuracy says.
   `expectancy_r` is `null` (not zero) below `n_taken = 20` (CLAUDE.md §8);
   `expectancy_n` still ships so the UI can tell "thin sample" from "no
   model."
2. **Calibration.** Ten probability buckets; `predicted` vs. `realised` win
   rate per bucket, each with its own `n`. A model whose 70%-bucket wins 40%
   of the time is miscalibrated even if its AUC looks fine — check this before
   trusting a probability at face value.
3. **Accuracy / win_rate / AUC last.** These read well on a model that's
   simply overfit to the noisiest rows. They're worth glancing at, not
   optimizing for.

**Regime-stage metrics are filler and the API strips them.** `train_all` runs
the 3-class regime model through the same `fold_metrics` helper as the timing
model, fed a constant probability of 1.0 for its own predicted class — so
`auc` computes to exactly 0.5, `expectancy_r` and `baseline_expectancy_r` to
exactly 0.0, and `calibration` collapses to one degenerate bucket every time.
None of that is a measurement. `web/lab_api.py::_public_model` /
`_scrub_regime_metrics` removes — omits from the JSON entirely, not `null` —
`auc`, `expectancy_r`, `baseline_expectancy_r`, and `calibration` for
`stage="regime"` rows, and renames `win_rate` to `accuracy` there (a regime
model classifies into three buckets; "win rate" doesn't mean anything for it,
"accuracy" does). Timing-stage models keep all of the original fields,
including `win_rate`. If a future change makes these numbers real for the
regime stage, delete the scrub — but until then, do not "fix" them back onto
the wire; they are exactly as meaningless as this section says.

## `/api/lab/score` status values

Every failure is a status, never an exception that escapes to the caller —
this output renders on `/live` beside the order buttons, and a blank panel
with a reason is honest where a stale number is not.

| status | means | what to do |
|---|---|---|
| `ok` | scored normally | read the badge |
| `no_model` | no active model for this symbol/timeframe/stage | train one from `/lab` |
| `artifact_missing` | the `lab_models` row exists but its `.joblib` file is gone from `cache/models/` | retrain — `config_json` rebuilds the artifact byte-for-byte |
| `no_bars` | not enough candle data cached to build features for this range | **FILL** — fill candle history for this symbol/timeframe first |
| `stale_features` | a model was fit on a feature schema the current data no longer produces (`features.py` changed, or `usable_columns` dropped a column this run) | **RETRAIN** from `/lab` |

The frontend (`Lab.tsx::scoreStatusText`, `LabBadge.tsx::STATUS_TEXT`) renders
`stale_features` and `no_bars` as the distinct RETRAIN / FILL prompts above,
not interchangeable "something's wrong" text — keep that distinction if you
touch either component.

`model_age_ms` on a score report is the **worst-case** staleness across every
model actually consulted for that report (the regime model plus every timing
model loaded — pooled or per-regime), not just whichever model produced the
reported `expectancy_r`. A fresh timing model behind a six-month-old regime
model still reports six months old.

## Pooled fallback

`lab/train.py::train_all` trains one timing model per regime only when *every*
regime has at least `pooled_min_rows` (default 500) labelled rows. If even one
regime is thin, **all three fall back to a single pooled timing model** — this
is all-or-nothing, not "skip the thin regime and keep the other two
per-regime." `TrainedModel.pooled` and the score report's `pooled` field carry
this through to the UI (`LabBadge` shows "pooled model" when applicable).

## Cross-module contract

Three names form the seam between training and scoring; nothing downstream may
restate them — always import:

- `SIDE_CODE = {"long": 1, "short": 0}` — `lab/train.py`
- `SIDE_CODE_COLUMN = "side_code"` — `lab/train.py`
- `TrainedModel.features == cfg.features + (SIDE_CODE_COLUMN,)`, in that exact
  order — this is what a fitted estimator's input columns actually are.

`lab/score.py` rebuilds each model's feature vector from that model's own
recorded `config["features"]` (`lab/store.py::save_models` writes
`TrainedModel.features` verbatim into `config_json`), not from a locally
reconstructed list — a guessed encoding direction would silently invert every
timing prediction with no test to catch it.

## Modules (six, not five)

The spec named five; `evaluate.py` was split out of `train.py` during
implementation so the walk-forward splits and expectancy maths could be
tested against hand-written arrays without fitting anything:

- `features.py` — candle → feature DataFrame, no lookahead.
- `labels.py` — regime ER labels + triple-barrier timing labels.
- `evaluate.py` — purged folds, fold/aggregate metrics, baseline comparison.
  Fits nothing; pure functions over arrays.
- `train.py` — dataset assembly + fitting (logreg and LightGBM, both, for
  every stage/regime). No sqlite.
- `store.py` — the *only* module under `lab/` that touches sqlite. Persistence
  for `lab_models` (see Write-lock discipline below).
- `score.py` — scores live bars with the active models; every failure path
  returns a `ScoreReport.status`, never raises past its own boundary.

## Write-lock discipline

`lab/store.py::save_models` is deliberately two-phase, following the lesson
from `deals.sync` and `fill_range` (both of which have frozen this app by
holding the WAL writer slot across slow work): every `joblib.dump()` — one per
stage/regime/kind, plausibly slow — runs to a temp filename **before any
transaction opens**. The transaction that follows only inserts rows and
renames already-written files into place, then commits once. `web/lab_api.py`
follows the same shape at the route level: read bars in one short call, let
that transaction end, fit with no cursor open, only then write.

## Reproducing a stored model from `config_json`

Every `lab_models` row's `config_json` is `TrainConfig`'s fields plus the
`features` list actually used (post-`usable_columns`) — enough to refit
byte-for-byte with the same seed:

1. Look up the row (`GET /api/lab/models`) and read `config_json`.
2. Re-fetch the same candle range (`train_from_ms`..`train_to_ms`) for
   `symbol`/`timeframe`.
3. Call `lab/train.py::train_all(df, TrainConfig(**config))` — `seed` is
   recorded, so the LightGBM and logistic-regression fits are deterministic.
4. This is exactly what happens if `artifact_missing` shows up: retrain
   through `/lab` with the same inputs and the new `.joblib` replaces the
   missing one.

## What does NOT exist here

`/live` (`frontend/src/pages/Live.tsx`) has no candle chart in this codebase —
it does not render `CandleChart`. There is therefore no regime shading on
`/live`; the only lab surface there is `LabBadge` (regime label, both
probabilities, model age with a staleness threshold, out-of-sample expectancy
with its `n`, and a "pooled model" note when applicable). Regime shading and
the probability strip exist only on `/lab`, via `RegimeOverlay`. Do not
document or assume chart shading on `/live` — it was scoped out because there
is no chart to shade.
