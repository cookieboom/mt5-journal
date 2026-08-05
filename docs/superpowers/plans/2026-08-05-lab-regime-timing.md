# Lab — Regime + Entry Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train and display two candle-only models per (symbol, timeframe) — a three-class market-regime classifier and a per-regime triple-barrier entry-timing classifier — from an interactive `/lab` page, with the active model's score and age surfaced on `/live`.

**Architecture:** A new pure-python package `src/journal/lab/` holds features, labels, training and scoring. It has six modules where the spec named five: `evaluate.py` is split out of `train.py` so the walk-forward splits and the expectancy maths can be tested with hand-written arrays, without fitting anything. Nothing in it imports MT5 or FastAPI. `store.py` is the only file that touches sqlite; fitted estimators are joblib artifacts under `cache/models/` with a row in the new `lab_models` table. The web layer follows the existing split: payload builders in `web/api.py`, routes in `web/app.py` using `Depends(get_conn)`. The frontend follows the existing split too: pure logic and API client in `frontend/src/lib/lab.ts`, data fetching in `frontend/src/hooks/useLabModels.ts`, presentation in `frontend/src/pages/Lab.tsx`, reusing `CandleChart` and `useChartData`.

**Tech Stack:** python 3.12, sqlite3 (stdlib), pandas, numpy, scikit-learn (new), lightgbm (new), joblib (transitive), typer, FastAPI, pytest · React + TypeScript, react-router-dom, lightweight-charts 5.2.0, vitest, testing-library.

## Global Constraints

Copied from the spec and CLAUDE.md. Every task's requirements implicitly include this section.

- **Never `import MetaTrader5` outside `src/journal/adapter/`.** Nothing in `lab/` touches the adapter or the bridge. All bars come from `store/candles_store.load_bars`.
- **All timestamps are epoch milliseconds, integer, UTC.** `candles.time_msc` is bar OPEN time. This account's `server_utc_offset_s = 0`, so `hour_utc` is derived directly from `time_msc` with no conversion.
- **`NULL` means unknown; `0` means "none set".** Applies to `candles.tick_volume` and `candles.spread`.
- **Money and prices are `REAL`.** Compare with tolerance `abs(a - b) < 1e-9`, never `==`.
- **Account currency is `USC`.** No money figure is printed as `$`. All model metrics are in **R** (unit-free) — the only unit this feature reports.
- **Every reported statistic shows `n`; buckets with `n < 20` are suppressed** (CLAUDE.md §8).
- **Schema changes go through a migration file.** Never edit `schema.sql` in place for an existing table. New tables may be appended to `schema.sql` AND shipped as a migration; `tests/test_migrations.py` asserts both paths produce the same schema.
- **`journal rebuild` must keep succeeding.** `lab_models` is not derived from raw and is never dropped by rebuild.
- **Charts and models are cache.** `cache/models/` must be reproducible from `lab_models.config_json` plus the recorded seed.
- **Write-lock discipline:** no DB cursor stays open across a model fit. Read bars in one short transaction, close it, fit in memory, then one short write.
- **Tests before implementation** (CLAUDE.md rule 7) for everything under `lab/`.
- Fixed defaults: `n_bars = 24`, `k_atr = 1.0`, `rr = 2.0`, `er_threshold = 0.35`, `n_folds = 5`, `seed = 7`, pooled-fallback threshold `500` rows, unknown-column threshold `5%`.
- Symbols are stored twice: query with `symbol` (`XAUUSDc`), group by `symbol_base`. `lab_models` stores `symbol` verbatim.

---

### Task 1: Dependencies and the rule 9 rewrite

Gate task. Adds the two approved dependencies and replaces the project rule that this feature contradicts. Nothing else can be honestly built until the rule matches the code.

**Files:**
- Modify: `pyproject.toml:6-13`
- Modify: `CLAUDE.md` (the "Hard rules" list, rule 9)
- Test: `tests/test_lab_deps.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `sklearn` and `lightgbm` for every later task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lab_deps.py`:

```python
"""The two dependencies approved for the lab (CLAUDE.md rule 8). This test
exists so a fresh checkout fails loudly rather than at the first training run."""


def test_sklearn_importable():
    from sklearn.linear_model import LogisticRegression

    assert LogisticRegression is not None


def test_lightgbm_importable():
    import lightgbm as lgb

    assert hasattr(lgb, "LGBMClassifier")


def test_joblib_importable():
    import joblib

    assert hasattr(joblib, "dump") and hasattr(joblib, "load")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lab_deps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sklearn'`

- [ ] **Step 3: Add the dependencies**

In `pyproject.toml`, extend the `dependencies` list:

```toml
dependencies = [
    "siliconmetatrader5",
    "pandas",
    "mplfinance",
    "typer",
    "fastapi",
    "uvicorn",
    "scikit-learn",
    "lightgbm",
]
```

Then run `uv sync`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lab_deps.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Rewrite rule 9 in CLAUDE.md**

Replace the existing rule 9 under "Hard rules":

```markdown
9. **Descriptive by default; `lab/` is the one predictive part.** Everything
   outside `src/journal/lab/` describes patterns in past data and must not
   generate trade signals or recommendations. `lab/` trains models on candle
   data and does predict. Its output is bound by three conditions that are not
   optional: it is always rendered together with the model's out-of-sample
   expectancy and its age; it never places, modifies, or sizes an order —
   `trade_commands` still requires a human click; and it is never the input to
   another automated step. Do not add "should I take this trade" features
   anywhere, including inside `lab/`.
```

- [ ] **Step 6: Note the new dependencies in CLAUDE.md**

Update the stack line in rule 8 to read:

```markdown
8. **Do not add dependencies without asking.** Current stack: python 3.12,
   sqlite3 (stdlib), pandas, mplfinance, typer, pytest, fastapi, uvicorn,
   scikit-learn, lightgbm.
```

- [ ] **Step 7: Run the full suite to confirm nothing regressed**

Run: `uv run pytest`
Expected: PASS, every existing test plus the 3 new ones.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock CLAUDE.md tests/test_lab_deps.py
git commit -m "chore: add scikit-learn + lightgbm, scope rule 9 to lab/"
```

---

### Task 2: `lab/features.py` — the feature matrix

The most correctness-critical file in the feature. Every column must be computable from bars at or before `t`. The no-lookahead test is the point of this task.

**Files:**
- Create: `src/journal/lab/__init__.py`
- Create: `src/journal/lab/features.py`
- Test: `tests/test_lab_features.py`

**Interfaces:**
- Consumes: `journal.adapter.base.Candle` (fields `time_msc, open, high, low, close, tick_volume, spread, real_volume`).
- Produces:
  - `PRICE_FEATURES: tuple[str, ...]` — the 14 toggleable feature names.
  - `bars_to_frame(bars: list[Candle]) -> pd.DataFrame` — index `time_msc` (int64), columns `open/high/low/close/tick_volume/spread`, ascending, duplicates dropped.
  - `build_features(df: pd.DataFrame) -> pd.DataFrame` — same index, one column per name in `PRICE_FEATURES`, plus the untouched OHLC columns.
  - `usable_columns(df: pd.DataFrame, wanted: list[str], max_unknown: float = 0.05) -> tuple[list[str], dict[str, float]]` — drops a wanted feature whose source column is unknown for more than `max_unknown` of rows; returns the kept list and the unknown fraction per dropped name.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_features.py`:

```python
"""Feature matrix. The first test is the one that matters: a feature value at
bar t must not move when bars after t change. Everything else in the lab is
worthless if that fails."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from journal.adapter.base import Candle
from journal.lab.features import (
    PRICE_FEATURES,
    bars_to_frame,
    build_features,
    usable_columns,
)

MINUTE = 60_000


def _bars(closes: list[float], *, spread: int | None = 20,
          volume: int | None = 100) -> list[Candle]:
    """Synthetic bars: open == previous close, a symmetric 0.5 wick each side."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(
            time_msc=i * MINUTE,
            open=prev,
            high=max(prev, c) + 0.5,
            low=min(prev, c) - 0.5,
            close=c,
            tick_volume=volume,
            spread=spread,
            real_volume=0,
        ))
        prev = c
    return out


def test_no_lookahead_features_at_t_ignore_future_bars():
    closes = [100.0 + i for i in range(80)]
    base = build_features(bars_to_frame(_bars(closes)))

    tampered = list(closes)
    for i in range(60, 80):
        tampered[i] = 5_000.0          # violently different future
    after = build_features(bars_to_frame(_bars(tampered)))

    left = base.loc[: 59 * MINUTE, list(PRICE_FEATURES)]
    right = after.loc[: 59 * MINUTE, list(PRICE_FEATURES)]
    pd.testing.assert_frame_equal(left, right)


def test_bars_to_frame_sorts_and_dedupes():
    bars = _bars([100.0, 101.0, 102.0])
    shuffled = [bars[2], bars[0], bars[1], bars[1]]
    df = bars_to_frame(shuffled)
    assert list(df.index) == [0, MINUTE, 2 * MINUTE]


def test_hour_and_dow_come_from_time_msc_utc():
    # 2026-01-01T00:00:00Z is a Thursday -> dow == 3 (Monday = 0)
    epoch = 1_767_225_600_000
    bars = [Candle(time_msc=epoch + i * 3_600_000, open=1.0, high=1.5, low=0.5,
                   close=1.0, tick_volume=10, spread=5, real_volume=0)
            for i in range(3)]
    df = build_features(bars_to_frame(bars))
    assert list(df["hour_utc"]) == [0, 1, 2]
    assert set(df["dow"]) == {3}


def test_body_and_wick_ratios_are_null_on_a_flat_bar():
    flat = Candle(time_msc=0, open=100.0, high=100.0, low=100.0, close=100.0,
                  tick_volume=10, spread=5, real_volume=0)
    df = build_features(bars_to_frame([flat]))
    assert np.isnan(df.loc[0, "body_ratio"])
    assert np.isnan(df.loc[0, "upper_wick"])
    assert np.isnan(df.loc[0, "lower_wick"])


def test_atr_rel_is_positive_and_uses_previous_close():
    df = build_features(bars_to_frame(_bars([100.0 + i for i in range(40)])))
    tail = df["atr_rel"].dropna()
    assert len(tail) > 0
    assert (tail > 0).all()


def test_usable_columns_drops_a_mostly_unknown_source_column():
    bars = _bars([100.0 + i for i in range(40)], spread=None)
    df = build_features(bars_to_frame(bars))
    kept, dropped = usable_columns(df, ["ret_1", "spread"])
    assert kept == ["ret_1"]
    assert dropped["spread"] == pytest.approx(1.0)


def test_usable_columns_keeps_a_column_with_few_unknowns():
    bars = _bars([100.0 + i for i in range(40)])
    df = build_features(bars_to_frame(bars))
    df.loc[0, "spread"] = np.nan          # 1 of 40 == 2.5%, under the 5% bar
    kept, dropped = usable_columns(df, ["ret_1", "spread"])
    assert kept == ["ret_1", "spread"]
    assert dropped == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.lab'`

- [ ] **Step 3: Write the implementation**

Create `src/journal/lab/__init__.py`:

```python
"""The lab — the one predictive part of this tool (CLAUDE.md rule 9).

Trains candle-only models: a three-class regime classifier and a per-regime
triple-barrier entry-timing classifier. Nothing here imports MetaTrader5 or
FastAPI. Bars arrive from `store.candles_store.load_bars`; the only module that
touches sqlite is `lab.store`."""
```

Create `src/journal/lab/features.py`:

```python
"""Feature matrix from candles. Every column is computed from bars at or before
its own row — pandas `rolling` and `ewm` are backward-looking, and nothing here
uses `shift(-n)`. `tests/test_lab_features.py::test_no_lookahead_features_at_t_
ignore_future_bars` is the guard on that and must never be weakened.

`hour_utc` comes straight from `time_msc` with no offset: this account's
`server_utc_offset_s` is 0 (CLAUDE.md). `side` is NOT here — it belongs to the
label, and `lab.train` appends it."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..adapter.base import Candle

PRICE_FEATURES: tuple[str, ...] = (
    "ret_1", "ret_5", "ret_20",
    "atr_rel",
    "ema20_dist", "ema50_dist",
    "body_ratio", "upper_wick", "lower_wick",
    "range_pct",
    "vol_rel",
    "spread",
    "hour_utc", "dow",
)

# Which raw column each feature needs. Used by `usable_columns` to disable a
# feature whose source is mostly NULL rather than dropping most of the dataset.
_SOURCE: dict[str, str] = {"vol_rel": "tick_volume", "spread": "spread"}

_ATR_WINDOW = 14


def bars_to_frame(bars: list[Candle]) -> pd.DataFrame:
    """Candles -> DataFrame indexed by `time_msc`, ascending, duplicates dropped
    (last wins — a re-fetched bar is the corrected one)."""
    df = pd.DataFrame(
        [
            {
                "time_msc": b.time_msc,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "tick_volume": b.tick_volume,
                "spread": b.spread,
            }
            for b in bars
        ],
        columns=["time_msc", "open", "high", "low", "close", "tick_volume", "spread"],
    )
    if df.empty:
        return df.set_index(pd.Index([], dtype="int64", name="time_msc"))
    df = df.astype({"time_msc": "int64"})
    df = df.sort_values("time_msc").drop_duplicates("time_msc", keep="last")
    return df.set_index("time_msc")


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add every name in PRICE_FEATURES to `df`. OHLC columns are left intact so
    the caller can still build labels from the same frame."""
    out = df.copy()
    if out.empty:
        for name in PRICE_FEATURES:
            out[name] = pd.Series(dtype="float64")
        return out

    close = out["close"]
    high = out["high"]
    low = out["low"]
    open_ = out["open"]

    log_close = np.log(close)
    out["ret_1"] = log_close.diff(1)
    out["ret_5"] = log_close.diff(5)
    out["ret_20"] = log_close.diff(20)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.rolling(_ATR_WINDOW, min_periods=_ATR_WINDOW).mean()
    out["atr"] = atr
    out["atr_rel"] = atr / close

    safe_atr = atr.where(atr > 0)
    out["ema20_dist"] = (close - close.ewm(span=20, adjust=False).mean()) / safe_atr
    out["ema50_dist"] = (close - close.ewm(span=50, adjust=False).mean()) / safe_atr

    span = (high - low).where(lambda s: s > 0)
    out["body_ratio"] = (close - open_).abs() / span
    out["upper_wick"] = (high - close.combine(open_, max)) / span
    out["lower_wick"] = (close.combine(open_, min) - low) / span
    out["range_pct"] = (high - low) / close

    volume = pd.to_numeric(out["tick_volume"], errors="coerce")
    vol_mean = volume.rolling(20, min_periods=20).mean().where(lambda s: s > 0)
    out["vol_rel"] = volume / vol_mean

    out["spread"] = pd.to_numeric(out["spread"], errors="coerce")

    stamp = pd.to_datetime(out.index, unit="ms", utc=True)
    out["hour_utc"] = stamp.hour.astype("int16")
    out["dow"] = stamp.dayofweek.astype("int16")
    return out


def usable_columns(df: pd.DataFrame, wanted: list[str],
                   max_unknown: float = 0.05) -> tuple[list[str], dict[str, float]]:
    """Split `wanted` into the features worth training on and the ones whose
    source column is unknown too often to keep.

    `candles.tick_volume` and `candles.spread` are nullable and NULL means
    unknown, not zero (CLAUDE.md rule 4). Dropping every row with an unknown
    spread would throw away most of a range fetched before the column was
    populated, so the feature goes instead of the rows."""
    kept: list[str] = []
    dropped: dict[str, float] = {}
    n = len(df)
    for name in wanted:
        source = _SOURCE.get(name)
        if source is None or n == 0:
            kept.append(name)
            continue
        unknown = float(pd.to_numeric(df[source], errors="coerce").isna().mean())
        if unknown > max_unknown:
            dropped[name] = unknown
        else:
            kept.append(name)
    return kept, dropped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_features.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src/journal/lab/__init__.py src/journal/lab/features.py tests/test_lab_features.py
git commit -m "feat(lab): candle feature matrix with a no-lookahead guard"
```

---

### Task 3: `lab/labels.py` — regime and triple-barrier labels

**Files:**
- Create: `src/journal/lab/labels.py`
- Test: `tests/test_lab_labels.py`

**Interfaces:**
- Consumes: the DataFrame from `features.build_features` (needs `open/high/low/close/atr`).
- Produces:
  - `LabelConfig` — frozen dataclass: `n_bars: int = 24`, `k_atr: float = 1.0`, `rr: float = 2.0`, `er_threshold: float = 0.35`.
  - `REGIMES: tuple[str, ...] = ("trend_up", "trend_down", "range")`
  - `regime_labels(df, cfg) -> pd.Series` — dtype `object`, value in `REGIMES` or `None` for the trailing `n_bars` rows.
  - `barrier_labels(df, cfg, side, point, default_spread_points) -> pd.DataFrame` — index matches `df`, columns `outcome` (`"tp_first"`/`"sl_first"`/`"timeout"`/`None`), `r_gross` (float), `r_net` (float), `entry` (float).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_labels.py`:

```python
"""Labels. Two rules are load-bearing and both are tested here: entry is
`open[t+1]` (close[t] is not tradeable), and when one bar touches both barriers
the stop wins — the same pessimism the replay engine already applies."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from journal.lab.labels import LabelConfig, barrier_labels, regime_labels

MINUTE = 60_000


def _frame(rows: list[tuple[float, float, float, float]], atr: float = 1.0) -> pd.DataFrame:
    """rows are (open, high, low, close); a constant ATR keeps R arithmetic
    readable — R = k_atr * atr."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.index = pd.Index([i * MINUTE for i in range(len(rows))], name="time_msc")
    df["atr"] = atr
    return df


def test_entry_is_the_next_bar_open_not_this_bar_close():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (110.0, 110.0, 110.0, 110.0),
        (110.0, 110.0, 110.0, 110.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "entry"] == pytest.approx(110.0)


def test_stop_wins_when_one_bar_touches_both_barriers():
    # entry 100, R = 1 -> SL 99, TP 102. Bar 1 spans 98..103: both are touched.
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 103.0, 98.0, 100.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "sl_first"
    assert out.loc[0, "r_gross"] == pytest.approx(-1.0)


def test_target_first_pays_the_reward_ratio():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 102.5, 99.5, 102.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "tp_first"
    assert out.loc[0, "r_gross"] == pytest.approx(2.0)


def test_timeout_scores_the_realised_move_in_r():
    # entry 100, R = 1, nothing touches 99 or 102 within 2 bars, ends at 100.5
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.2),
        (100.2, 101.0, 99.5, 100.5),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "timeout"
    assert out.loc[0, "r_gross"] == pytest.approx(0.5)


def test_short_side_mirrors_the_long_side():
    # entry 100, R = 1 -> SL 101, TP 98. Price falls to 97.5: target first.
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 97.5, 98.0),
        (98.0, 98.0, 98.0, 98.0),
    ])
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "short", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "outcome"] == "tp_first"
    assert out.loc[0, "r_gross"] == pytest.approx(2.0)


def test_spread_is_deducted_from_the_net_result():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 102.5, 99.5, 102.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    df["spread"] = 50            # 50 points * 0.01 = 0.5 price = 0.5 R
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "long", point=0.01,
                         default_spread_points=0)
    assert out.loc[0, "r_gross"] == pytest.approx(2.0)
    assert out.loc[0, "r_net"] == pytest.approx(1.5)


def test_unknown_spread_falls_back_to_the_supplied_default():
    df = _frame([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 102.5, 99.5, 102.0),
        (100.0, 100.0, 100.0, 100.0),
    ])
    df["spread"] = np.nan
    out = barrier_labels(df, LabelConfig(n_bars=2, rr=2.0), "long", point=0.01,
                         default_spread_points=30)
    assert out.loc[0, "r_net"] == pytest.approx(2.0 - 0.3)


def test_last_n_bars_have_no_label():
    df = _frame([(100.0, 100.5, 99.5, 100.0)] * 6)
    out = barrier_labels(df, LabelConfig(n_bars=2), "long", point=0.01,
                         default_spread_points=0)
    assert out["outcome"].iloc[-2:].isna().all()
    assert out["outcome"].iloc[:-2].notna().all()


def test_regime_labels_call_a_straight_line_a_trend():
    df = _frame([(100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i) for i in range(30)])
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[0] == "trend_up"

    falling = _frame([(100.0 - i, 100.0 - i, 100.0 - i, 100.0 - i) for i in range(30)])
    assert regime_labels(falling, LabelConfig(n_bars=10)).iloc[0] == "trend_down"


def test_regime_labels_call_a_sawtooth_a_range():
    closes = [100.0 + (1.0 if i % 2 else -1.0) for i in range(30)]
    df = _frame([(c, c, c, c) for c in closes])
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[0] == "range"


def test_regime_labels_call_a_flat_window_a_range():
    df = _frame([(100.0, 100.0, 100.0, 100.0)] * 30)
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[0] == "range"


def test_regime_labels_leave_the_trailing_window_unlabelled():
    df = _frame([(100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i) for i in range(15)])
    out = regime_labels(df, LabelConfig(n_bars=10))
    assert out.iloc[-10:].isna().all()
    assert out.iloc[:-10].notna().all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.lab.labels'`

- [ ] **Step 3: Write the implementation**

Create `src/journal/lab/labels.py`:

```python
"""Forward-looking labels. Both look `n_bars` ahead, so the trailing `n_bars`
rows are unlabelled by construction — those are exactly the bars scored at
inference time.

Two decisions here are deliberate and match the replay engine so a lab number
and a replay number mean the same thing:
  * entry is `open[t+1]`. `close[t]` is not tradeable.
  * when a single bar's range touches both barriers the STOP wins. The bar has
    no intrabar path, and assuming the favourable order is how backtests lie."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REGIMES: tuple[str, ...] = ("trend_up", "trend_down", "range")
SIDES: tuple[str, ...] = ("long", "short")


@dataclass(frozen=True)
class LabelConfig:
    n_bars: int = 24
    k_atr: float = 1.0
    rr: float = 2.0
    er_threshold: float = 0.35


def regime_labels(df: pd.DataFrame, cfg: LabelConfig) -> pd.Series:
    """Efficiency ratio over the next `n_bars`:

        ER = (close[t+n] - close[t]) / sum(|close[i] - close[i-1]|)

    |ER| above the threshold is a trend in the direction of the net move;
    anything else is a range. A window with zero total movement has an
    undefined ratio and is a range."""
    close = df["close"].astype("float64")
    n = cfg.n_bars
    net = close.shift(-n) - close
    gross = close.diff().abs().rolling(n).sum().shift(-n)

    er = net / gross.where(gross > 0)
    out = pd.Series("range", index=df.index, dtype="object")
    out[er > cfg.er_threshold] = "trend_up"
    out[er < -cfg.er_threshold] = "trend_down"
    out[net.isna()] = None
    return out


def barrier_labels(df: pd.DataFrame, cfg: LabelConfig, side: str, *,
                   point: float, default_spread_points: float) -> pd.DataFrame:
    """Triple barrier for one side.

    `point` converts `candles.spread` (integer points) into price, and comes
    from `symbol_specs.point`. Where the spread is unknown (NULL means unknown,
    not zero) `default_spread_points` stands in and the caller is responsible
    for labelling that number as assumed, not measured."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")

    n = len(df)
    high = df["high"].to_numpy(dtype="float64")
    low = df["low"].to_numpy(dtype="float64")
    close = df["close"].to_numpy(dtype="float64")
    open_ = df["open"].to_numpy(dtype="float64")
    atr = df["atr"].to_numpy(dtype="float64")

    if "spread" in df.columns:
        spread = pd.to_numeric(df["spread"], errors="coerce").to_numpy(dtype="float64")
    else:
        spread = np.full(n, np.nan)
    spread = np.where(np.isnan(spread), float(default_spread_points), spread)

    outcome: list[str | None] = [None] * n
    r_gross = np.full(n, np.nan)
    entry_out = np.full(n, np.nan)

    long = side == "long"
    for t in range(n - cfg.n_bars):
        r = cfg.k_atr * atr[t]
        if not np.isfinite(r) or r <= 0:
            continue
        entry = open_[t + 1]
        if not np.isfinite(entry):
            continue
        entry_out[t] = entry
        stop = entry - r if long else entry + r
        target = entry + cfg.rr * r if long else entry - cfg.rr * r

        result: str | None = None
        for i in range(t + 1, t + 1 + cfg.n_bars):
            hit_stop = low[i] <= stop if long else high[i] >= stop
            hit_target = high[i] >= target if long else low[i] <= target
            if hit_stop:                      # pessimistic: stop before target
                result = "sl_first"
                break
            if hit_target:
                result = "tp_first"
                break
        if result == "sl_first":
            r_gross[t] = -1.0
        elif result == "tp_first":
            r_gross[t] = cfg.rr
        else:
            result = "timeout"
            exit_price = close[t + cfg.n_bars]
            move = exit_price - entry if long else entry - exit_price
            r_gross[t] = move / r
        outcome[t] = result

    cost_r = np.where(
        np.isfinite(entry_out) & (atr * cfg.k_atr > 0),
        spread * point / (atr * cfg.k_atr),
        np.nan,
    )
    return pd.DataFrame(
        {
            "outcome": pd.Series(outcome, index=df.index, dtype="object"),
            "r_gross": r_gross,
            "r_net": r_gross - cost_r,
            "entry": entry_out,
        },
        index=df.index,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_labels.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add src/journal/lab/labels.py tests/test_lab_labels.py
git commit -m "feat(lab): regime + triple-barrier labels, stop-first and net of spread"
```

---

### Task 4: `lab/evaluate.py` — purged walk-forward and honest metrics

Split from training on purpose: the metrics are the part that decides whether the whole feature says anything true, and they must be testable without fitting a model.

**Files:**
- Create: `src/journal/lab/evaluate.py`
- Test: `tests/test_lab_evaluate.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except `LabelConfig` defaults conceptually.
- Produces:
  - `MIN_BUCKET_N: int = 20`
  - `purged_folds(n_rows: int, n_folds: int, purge: int) -> list[tuple[np.ndarray, np.ndarray]]` — `(train_idx, test_idx)` per fold, expanding-window, test blocks contiguous and in time order.
  - `fold_metrics(y_true, proba, r_net, threshold) -> dict` — keys `n`, `n_taken`, `win_rate`, `expectancy_r`, `auc`, `baseline_expectancy_r`, `calibration`.
  - `aggregate(folds: list[dict]) -> dict` — same keys, `n`-weighted, plus `folds`.
  - `suppressed(value, n)` — returns `None` when `n < MIN_BUCKET_N`, else `value`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_evaluate.py`:

```python
"""Evaluation. The purge gap is the whole reason this file exists: labels look
`n_bars` ahead, so a test block adjacent to its training block has already told
the model its own answer."""
from __future__ import annotations

import numpy as np
import pytest

from journal.lab.evaluate import (
    MIN_BUCKET_N,
    aggregate,
    fold_metrics,
    purged_folds,
    suppressed,
)


def test_folds_never_overlap_and_run_forward_in_time():
    folds = purged_folds(1_000, n_folds=5, purge=24)
    assert len(folds) == 5
    last_test_end = -1
    for train_idx, test_idx in folds:
        assert set(train_idx).isdisjoint(test_idx)
        assert test_idx.min() > last_test_end
        assert train_idx.max() < test_idx.min()
        last_test_end = test_idx.max()


def test_purge_gap_separates_train_from_test():
    purge = 24
    for train_idx, test_idx in purged_folds(1_000, n_folds=5, purge=purge):
        assert test_idx.min() - train_idx.max() > purge


def test_too_few_rows_yields_no_folds_rather_than_a_bad_split():
    assert purged_folds(50, n_folds=5, purge=24) == []


def test_expectancy_is_the_mean_net_r_of_taken_entries():
    y = np.array([1, 0, 1, 0])
    proba = np.array([0.9, 0.8, 0.7, 0.1])
    r_net = np.array([2.0, -1.0, 2.0, -1.0])
    m = fold_metrics(y, proba, r_net, threshold=0.5)
    # three entries pass the threshold: 2.0, -1.0, 2.0
    assert m["n_taken"] == 3
    assert m["expectancy_r"] == pytest.approx(1.0)
    assert m["win_rate"] == pytest.approx(2 / 3)


def test_baseline_uses_every_row_not_the_selected_ones():
    y = np.array([1, 0, 0, 0])
    proba = np.array([0.9, 0.1, 0.1, 0.1])
    r_net = np.array([2.0, -1.0, -1.0, -1.0])
    m = fold_metrics(y, proba, r_net, threshold=0.5)
    assert m["expectancy_r"] == pytest.approx(2.0)
    assert m["baseline_expectancy_r"] == pytest.approx(-0.25)


def test_auc_is_one_for_a_perfect_ranking_and_none_for_one_class():
    perfect = fold_metrics(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9]),
                           np.array([-1.0, -1.0, 2.0, 2.0]), threshold=0.5)
    assert perfect["auc"] == pytest.approx(1.0)

    one_class = fold_metrics(np.array([1, 1]), np.array([0.6, 0.7]),
                             np.array([2.0, 2.0]), threshold=0.5)
    assert one_class["auc"] is None


def test_calibration_buckets_carry_their_own_n():
    y = np.array([1] * 50 + [0] * 50)
    proba = np.array([0.9] * 50 + [0.1] * 50)
    m = fold_metrics(y, proba, np.where(y == 1, 2.0, -1.0), threshold=0.5)
    for bucket in m["calibration"]:
        assert set(bucket) == {"bucket", "predicted", "realised", "n"}
    assert sum(b["n"] for b in m["calibration"]) == 100


def test_aggregate_weights_folds_by_n():
    a = {"n": 100, "n_taken": 100, "win_rate": 0.6, "expectancy_r": 1.0,
         "auc": 0.7, "baseline_expectancy_r": 0.0, "calibration": []}
    b = {"n": 300, "n_taken": 300, "win_rate": 0.2, "expectancy_r": -1.0,
         "auc": 0.5, "baseline_expectancy_r": 0.0, "calibration": []}
    out = aggregate([a, b])
    assert out["n"] == 400
    assert out["expectancy_r"] == pytest.approx((100 * 1.0 + 300 * -1.0) / 400)
    assert len(out["folds"]) == 2


def test_thin_buckets_are_suppressed_per_section_8():
    assert suppressed(0.93, MIN_BUCKET_N - 1) is None
    assert suppressed(0.93, MIN_BUCKET_N) == pytest.approx(0.93)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_evaluate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.lab.evaluate'`

- [ ] **Step 3: Write the implementation**

Create `src/journal/lab/evaluate.py`:

```python
"""Walk-forward evaluation. Nothing here fits a model; it scores predictions
that already exist, which is why it can be tested with hand-written arrays.

Three things are non-negotiable in this file:
  * splits run forward in time and never shuffle. A random split leaks the
    future into the past and produces a beautiful, meaningless score.
  * a purge gap of `n_bars` sits between every train block and its test block,
    because each label already looked `n_bars` ahead.
  * every model number is reported beside a random-entry baseline over the same
    rows. An expectancy is only interesting relative to entering at random."""
from __future__ import annotations

import numpy as np

MIN_BUCKET_N = 20          # CLAUDE.md §8
_CALIBRATION_BUCKETS = 10


def purged_folds(n_rows: int, n_folds: int,
                 purge: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds with a `purge`-row gap before each test block.

    Returns [] when the data cannot support the split — a fold with an empty
    train or test side is worse than no answer."""
    if n_rows <= 0 or n_folds < 1:
        return []
    block = n_rows // (n_folds + 1)
    if block <= purge:
        return []

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(1, n_folds + 1):
        train_end = i * block
        test_start = train_end + purge + 1
        test_end = min(test_start + block, n_rows)
        if test_start >= test_end:
            break
        folds.append((np.arange(0, train_end), np.arange(test_start, test_end)))
    return folds


def fold_metrics(y_true: np.ndarray, proba: np.ndarray, r_net: np.ndarray,
                 threshold: float) -> dict:
    """Scores one test block.

    `expectancy_r` covers only the rows the model would have taken (proba above
    the threshold); `baseline_expectancy_r` covers every row, which is what
    entering at random on this block would have returned."""
    taken = proba >= threshold
    n_taken = int(taken.sum())
    return {
        "n": int(len(y_true)),
        "n_taken": n_taken,
        "win_rate": float(y_true[taken].mean()) if n_taken else None,
        "expectancy_r": float(r_net[taken].mean()) if n_taken else None,
        "auc": _auc(y_true, proba),
        "baseline_expectancy_r": float(r_net.mean()) if len(r_net) else None,
        "calibration": _calibration(y_true, proba),
    }


def aggregate(folds: list[dict]) -> dict:
    """`n`-weighted mean across folds. A fold that took no entries contributes
    its `n` to the total but nothing to the averages it has no opinion on."""
    total_n = sum(f["n"] for f in folds)
    total_taken = sum(f["n_taken"] for f in folds)
    return {
        "n": total_n,
        "n_taken": total_taken,
        "win_rate": _weighted(folds, "win_rate", "n_taken"),
        "expectancy_r": _weighted(folds, "expectancy_r", "n_taken"),
        "auc": _weighted(folds, "auc", "n"),
        "baseline_expectancy_r": _weighted(folds, "baseline_expectancy_r", "n"),
        "calibration": _merge_calibration(folds),
        "folds": folds,
    }


def suppressed(value: float | None, n: int) -> float | None:
    """CLAUDE.md §8: a rate computed from fewer than 20 samples is noise with a
    decimal point. Callers render None as a dash, never as 0."""
    if value is None or n < MIN_BUCKET_N:
        return None
    return float(value)


def _auc(y_true: np.ndarray, proba: np.ndarray) -> float | None:
    """None when the block holds a single class — AUC is undefined there, and
    sklearn raises rather than returning it."""
    if len(np.unique(y_true)) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y_true, proba))


def _calibration(y_true: np.ndarray, proba: np.ndarray) -> list[dict]:
    edges = np.linspace(0.0, 1.0, _CALIBRATION_BUCKETS + 1)
    out: list[dict] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (proba >= lo) & (proba < hi if hi < 1.0 else proba <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        out.append({
            "bucket": float((lo + hi) / 2),
            "predicted": float(proba[mask].mean()),
            "realised": float(y_true[mask].mean()),
            "n": n,
        })
    return out


def _weighted(folds: list[dict], key: str, weight_key: str) -> float | None:
    pairs = [(f[key], f[weight_key]) for f in folds
             if f.get(key) is not None and f.get(weight_key)]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    return float(sum(v * w for v, w in pairs) / total) if total else None


def _merge_calibration(folds: list[dict]) -> list[dict]:
    merged: dict[float, dict] = {}
    for fold in folds:
        for bucket in fold.get("calibration", []):
            acc = merged.setdefault(
                bucket["bucket"],
                {"bucket": bucket["bucket"], "predicted": 0.0, "realised": 0.0, "n": 0},
            )
            acc["predicted"] += bucket["predicted"] * bucket["n"]
            acc["realised"] += bucket["realised"] * bucket["n"]
            acc["n"] += bucket["n"]
    out = []
    for acc in sorted(merged.values(), key=lambda a: a["bucket"]):
        n = acc["n"]
        out.append({
            "bucket": acc["bucket"],
            "predicted": acc["predicted"] / n,
            "realised": acc["realised"] / n,
            "n": n,
        })
    return out
```

The random-entry baseline is the deterministic mean net R over every row in the block — that is the expectation of entering at random, so it needs no sampling and no RNG.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_evaluate.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add src/journal/lab/evaluate.py tests/test_lab_evaluate.py
git commit -m "feat(lab): purged walk-forward splits and expectancy metrics"
```

---

### Task 5: `lab/train.py` — assemble the dataset and fit both models

**Files:**
- Create: `src/journal/lab/train.py`
- Test: `tests/test_lab_train.py`

**Interfaces:**
- Consumes: `features.PRICE_FEATURES/build_features/usable_columns`, `labels.LabelConfig/REGIMES/SIDES/regime_labels/barrier_labels`, `evaluate.purged_folds/fold_metrics/aggregate`.
- Produces:
  - `TrainConfig` — frozen dataclass: `label: LabelConfig`, `features: tuple[str, ...]`, `n_folds: int = 5`, `seed: int = 7`, `threshold: float = 0.5`, `point: float`, `default_spread_points: float = 0.0`, `pooled_min_rows: int = 500`.
  - `TrainedModel` — frozen dataclass: `stage`, `regime`, `kind`, `estimator`, `metrics: dict`, `n_rows: int`, `features: tuple[str, ...]`, `pooled: bool`.
  - `build_dataset(df, cfg) -> pd.DataFrame` — one row per (bar, side); columns = feature columns + `side`, `regime`, `y`, `r_net`, `outcome`, `time_msc`.
  - `train_all(df, cfg) -> list[TrainedModel]` — the regime models plus per-regime timing models, both kinds each.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_train.py`:

```python
"""Training. These tests assert the SHAPE of a run — that both kinds are fit,
that timing splits per regime, that a thin regime falls back to pooled — not
that any model is accurate. Accuracy on synthetic data would prove nothing."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from journal.adapter.base import Candle
from journal.lab.features import bars_to_frame, build_features
from journal.lab.labels import LabelConfig
from journal.lab.train import TrainConfig, build_dataset, train_all

MINUTE = 60_000
FEATURES = ("ret_1", "ret_5", "atr_rel", "hour_utc")


def _walk(n: int, seed: int = 0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    price = 2000.0
    bars = []
    for i in range(n):
        step = float(rng.normal(0, 1.5))
        open_ = price
        price = price + step
        bars.append(Candle(
            time_msc=i * MINUTE,
            open=open_,
            high=max(open_, price) + 0.4,
            low=min(open_, price) - 0.4,
            close=price,
            tick_volume=100 + i % 7,
            spread=20,
            real_volume=0,
        ))
    return bars


def _cfg(**kw) -> TrainConfig:
    base = dict(label=LabelConfig(n_bars=8), features=FEATURES, n_folds=3,
                seed=7, threshold=0.5, point=0.001, default_spread_points=0.0)
    base.update(kw)
    return TrainConfig(**base)


def _frame(n: int = 1200) -> pd.DataFrame:
    return build_features(bars_to_frame(_walk(n)))


def test_dataset_has_two_rows_per_labelled_bar_one_per_side():
    df = _frame(400)
    data = build_dataset(df, _cfg())
    per_bar = data.groupby("time_msc").size()
    assert set(per_bar) == {2}
    assert set(data["side"]) == {"long", "short"}


def test_dataset_drops_unlabelled_and_incomplete_rows():
    df = _frame(400)
    data = build_dataset(df, _cfg())
    assert data["y"].notna().all()
    assert data["regime"].notna().all()
    assert data[list(FEATURES)].notna().all().all()
    # the trailing n_bars can never be labelled
    assert data["time_msc"].max() < df.index.max()


def test_train_all_fits_both_kinds_for_every_stage():
    models = train_all(_frame(), _cfg())
    regime_kinds = {m.kind for m in models if m.stage == "regime"}
    timing_kinds = {m.kind for m in models if m.stage == "timing"}
    assert regime_kinds == {"logreg", "lgbm"}
    assert timing_kinds == {"logreg", "lgbm"}


def test_timing_models_are_split_per_regime_when_data_allows():
    models = train_all(_frame(), _cfg(pooled_min_rows=1))
    regimes = {m.regime for m in models if m.stage == "timing"}
    assert regimes <= {"trend_up", "trend_down", "range"}
    assert None not in regimes


def test_a_thin_regime_falls_back_to_a_pooled_model():
    models = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    timing = [m for m in models if m.stage == "timing"]
    assert timing
    assert all(m.pooled and m.regime is None for m in timing)


def test_metrics_carry_n_and_a_baseline():
    models = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    for m in models:
        assert m.metrics["n"] > 0
        assert "folds" in m.metrics
        if m.stage == "timing":
            assert "baseline_expectancy_r" in m.metrics


def test_training_is_deterministic_for_a_fixed_seed():
    a = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    b = train_all(_frame(), _cfg(pooled_min_rows=10**6))
    lhs = {(m.stage, m.kind, m.regime): m.metrics["n"] for m in a}
    rhs = {(m.stage, m.kind, m.regime): m.metrics["n"] for m in b}
    assert lhs == rhs


def test_too_little_data_raises_rather_than_returning_a_fake_model():
    with pytest.raises(ValueError, match="not enough"):
        train_all(_frame(60), _cfg())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_train.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.lab.train'`

- [ ] **Step 3: Write the implementation**

Create `src/journal/lab/train.py`:

```python
"""Dataset assembly and fitting. Two models are fit for every stage on the same
rows — logistic regression and LightGBM — and both are returned. If the boosted
model does not beat the glass box, the caller has the numbers to say so.

No sqlite here: `train_all` takes a DataFrame and returns objects. `lab.store`
does the persistence, which is what keeps the fit out of the WAL writer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .evaluate import aggregate, fold_metrics, purged_folds
from .labels import REGIMES, SIDES, LabelConfig, barrier_labels, regime_labels

_SIDE_CODE = {"long": 1, "short": 0}


@dataclass(frozen=True)
class TrainConfig:
    label: LabelConfig
    features: tuple[str, ...]
    point: float
    n_folds: int = 5
    seed: int = 7
    threshold: float = 0.5
    default_spread_points: float = 0.0
    pooled_min_rows: int = 500


@dataclass(frozen=True)
class TrainedModel:
    stage: str                 # 'regime' | 'timing'
    regime: str | None         # None for stage='regime' and for pooled timing
    kind: str                  # 'logreg' | 'lgbm'
    estimator: Any
    metrics: dict
    n_rows: int
    features: tuple[str, ...]
    pooled: bool = False


def build_dataset(df: pd.DataFrame, cfg: TrainConfig) -> pd.DataFrame:
    """One row per (bar, side). `side` joins the feature list here rather than
    in `features.py` because it belongs to the label, not to the bar."""
    regimes = regime_labels(df, cfg.label)
    frames = []
    for side in SIDES:
        lab = barrier_labels(df, cfg.label, side, point=cfg.point,
                             default_spread_points=cfg.default_spread_points)
        block = df[list(cfg.features)].copy()
        block["side"] = _SIDE_CODE[side]
        block["regime"] = regimes
        block["outcome"] = lab["outcome"]
        block["y"] = (lab["outcome"] == "tp_first").astype("float64")
        block.loc[lab["outcome"].isna(), "y"] = np.nan
        block["r_net"] = lab["r_net"]
        block["time_msc"] = df.index
        frames.append(block)

    data = pd.concat(frames)
    data = data.dropna(subset=["y", "regime", "r_net", *cfg.features])
    # Sort by time so the walk-forward split stays chronological; both sides of
    # a bar sit adjacent and therefore always land in the same fold.
    return data.sort_values(["time_msc", "side"]).reset_index(drop=True)


def train_all(df: pd.DataFrame, cfg: TrainConfig) -> list[TrainedModel]:
    data = build_dataset(df, cfg)
    columns = [*cfg.features, "side"]
    if len(data) < 100:
        raise ValueError(
            f"not enough labelled rows to train: {len(data)}. Fetch more candles "
            f"or lower n_bars."
        )

    out: list[TrainedModel] = []
    out.extend(_fit_stage(data, columns, cfg, stage="regime",
                          target=data["regime"], regime=None, pooled=False))

    groups: list[tuple[str | None, pd.DataFrame]] = []
    if all((data["regime"] == r).sum() >= cfg.pooled_min_rows for r in REGIMES):
        groups = [(r, data[data["regime"] == r]) for r in REGIMES]
        pooled = False
    else:
        groups = [(None, data)]
        pooled = True

    for regime, block in groups:
        out.extend(_fit_stage(block, columns, cfg, stage="timing",
                              target=block["y"], regime=regime, pooled=pooled))
    return out


def _fit_stage(data: pd.DataFrame, columns: list[str], cfg: TrainConfig, *,
               stage: str, target: pd.Series, regime: str | None,
               pooled: bool) -> list[TrainedModel]:
    x = data[columns].to_numpy(dtype="float64")
    y = target.to_numpy()
    r_net = data["r_net"].to_numpy(dtype="float64")
    folds = purged_folds(len(data), cfg.n_folds, cfg.label.n_bars * len(SIDES))
    if not folds:
        raise ValueError(
            f"not enough labelled rows for {cfg.n_folds} purged folds: {len(data)}"
        )

    models: list[TrainedModel] = []
    for kind in ("logreg", "lgbm"):
        per_fold = []
        for train_idx, test_idx in folds:
            est = _new_estimator(kind, stage, cfg.seed)
            if len(np.unique(y[train_idx])) < 2:
                continue
            est.fit(x[train_idx], y[train_idx])
            per_fold.append(
                _score(est, stage, x[test_idx], y[test_idx], r_net[test_idx], cfg)
            )
        if not per_fold:
            raise ValueError(f"{stage}/{kind}: every fold held a single class")

        final = _new_estimator(kind, stage, cfg.seed)
        final.fit(x, y)
        models.append(TrainedModel(
            stage=stage, regime=regime, kind=kind, estimator=final,
            metrics=aggregate(per_fold), n_rows=len(data),
            features=tuple(columns), pooled=pooled,
        ))
    return models


def _score(est, stage: str, x, y, r_net, cfg: TrainConfig) -> dict:
    if stage == "regime":
        pred = est.predict(x)
        hit = (pred == y).astype("float64")
        # A regime model has no R attached; score it as accuracy by reusing the
        # binary metric with a certain "probability" of 1 for its own call.
        out = fold_metrics(hit, np.ones(len(hit)), np.zeros(len(hit)),
                           threshold=cfg.threshold)
        out["confusion"] = _confusion(y, pred)
        return out
    proba = est.predict_proba(x)[:, 1]
    return fold_metrics(y.astype("int64"), proba, r_net, threshold=cfg.threshold)


def _confusion(y_true, y_pred) -> dict:
    out: dict[str, dict[str, int]] = {a: {b: 0 for b in REGIMES} for a in REGIMES}
    for a, b in zip(y_true, y_pred):
        if a in out and b in out[a]:
            out[a][b] += 1
    return out


def _new_estimator(kind: str, stage: str, seed: int):
    if kind == "logreg":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1_000, random_state=seed),
        )
    from lightgbm import LGBMClassifier

    return LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=31,
        random_state=seed, verbose=-1, deterministic=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_train.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add src/journal/lab/train.py tests/test_lab_train.py
git commit -m "feat(lab): dataset assembly and paired logreg/lgbm training"
```

---

### Task 6: Migration 010 and `lab/store.py`

**Files:**
- Create: `src/journal/store/migrations/010_lab_models.sql`
- Modify: `src/journal/store/schema.sql` (append the new table; do not touch existing tables)
- Modify: `src/journal/store/db.py:20` (`SCHEMA_VERSION = 9` → `10`)
- Create: `src/journal/lab/store.py`
- Test: `tests/test_lab_store.py`

**Interfaces:**
- Consumes: `TrainedModel` from Task 5.
- Produces:
  - `ArtifactMissing(Exception)`
  - `save_models(conn, *, symbol, timeframe, config, models, train_from_ms, train_to_ms, cache_dir, activate_new=True) -> list[int]`
  - `list_models(conn, symbol=None, timeframe=None) -> list[dict]` — newest first, `metrics` and `config` already parsed.
  - `activate(conn, model_id: int) -> None`
  - `load_active(conn, symbol, timeframe, stage, regime, cache_dir) -> tuple[dict, Any]` — raises `ArtifactMissing` when the joblib file is gone.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_store.py`:

```python
"""Model persistence. `cache/models/` is cache (CLAUDE.md rule 6): losing it
must leave the rows intact and produce a clear retrain signal, not a crash."""
from __future__ import annotations

import json

import pytest

from journal.lab.store import (
    ArtifactMissing,
    activate,
    list_models,
    load_active,
    save_models,
)
from journal.lab.train import TrainedModel
from journal.store.db import connect, init_db


class _Stub:
    """Stands in for an estimator; joblib round-trips it fine."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __eq__(self, other) -> bool:
        return isinstance(other, _Stub) and other.tag == self.tag


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    init_db(c)
    yield c
    c.close()


def _model(stage="timing", regime="trend_up", kind="lgbm") -> TrainedModel:
    return TrainedModel(
        stage=stage, regime=regime, kind=kind, estimator=_Stub(f"{stage}-{kind}"),
        metrics={"n": 500, "expectancy_r": 0.1, "folds": []}, n_rows=500,
        features=("ret_1", "side"), pooled=False,
    )


def _save(conn, tmp_path, models, symbol="XAUUSDc", timeframe="H1"):
    return save_models(
        conn, symbol=symbol, timeframe=timeframe,
        config={"n_bars": 24, "seed": 7}, models=models,
        train_from_ms=1_000, train_to_ms=2_000, cache_dir=tmp_path / "cache",
    )


def test_save_writes_a_row_and_an_artifact(conn, tmp_path):
    ids = _save(conn, tmp_path, [_model()])
    assert len(ids) == 1
    rows = list_models(conn)
    assert rows[0]["symbol"] == "XAUUSDc"
    assert rows[0]["metrics"]["expectancy_r"] == 0.1
    assert rows[0]["config"]["seed"] == 7
    assert (tmp_path / "cache" / "models" / f"{ids[0]}.joblib").exists()


def test_round_trip_returns_the_estimator(conn, tmp_path):
    _save(conn, tmp_path, [_model()])
    row, est = load_active(conn, "XAUUSDc", "H1", "timing", "trend_up",
                           tmp_path / "cache")
    assert est == _Stub("timing-lgbm")
    assert row["kind"] == "lgbm"


def test_only_one_model_is_active_per_group(conn, tmp_path):
    first = _save(conn, tmp_path, [_model(kind="logreg")])[0]
    second = _save(conn, tmp_path, [_model(kind="lgbm")])[0]
    active = [r for r in list_models(conn) if r["active"]]
    assert [r["id"] for r in active] == [second]

    activate(conn, first)
    active = [r for r in list_models(conn) if r["active"]]
    assert [r["id"] for r in active] == [first]


def test_a_pooled_model_and_a_regime_model_do_not_collide(conn, tmp_path):
    _save(conn, tmp_path, [_model(regime=None), _model(regime="range")])
    active = [r for r in list_models(conn) if r["active"]]
    assert len(active) == 2


def test_missing_artifact_raises_a_named_error(conn, tmp_path):
    ids = _save(conn, tmp_path, [_model()])
    (tmp_path / "cache" / "models" / f"{ids[0]}.joblib").unlink()
    with pytest.raises(ArtifactMissing):
        load_active(conn, "XAUUSDc", "H1", "timing", "trend_up", tmp_path / "cache")
    assert list_models(conn), "the row survives the artifact"


def test_load_active_returns_none_when_nothing_is_trained(conn, tmp_path):
    assert load_active(conn, "XAUUSDc", "H1", "timing", "range",
                       tmp_path / "cache") is None


def test_list_models_filters_by_symbol_and_timeframe(conn, tmp_path):
    _save(conn, tmp_path, [_model()], symbol="XAUUSDc", timeframe="H1")
    _save(conn, tmp_path, [_model()], symbol="BTCUSDc", timeframe="M5")
    assert len(list_models(conn, symbol="BTCUSDc")) == 1
    assert len(list_models(conn, symbol="XAUUSDc", timeframe="M5")) == 0
```

Add to `tests/test_migrations.py` — follow the existing test's naming and structure in that file, appending:

```python
def test_lab_models_table_exists_on_both_paths():
    """A fresh init_db and a migrated older DB must both end up with
    lab_models and its partial unique index."""
    for conn in _both_paths():          # the helper this module already uses
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        assert "lab_models" in names
        assert "lab_models_active" in names
```

If `tests/test_migrations.py` has no `_both_paths` helper, use whatever comparison the file already performs between a fresh `init_db` schema and a migrated schema — the existing assertion style is authoritative; do not invent a new one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_store.py tests/test_migrations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.lab.store'`

- [ ] **Step 3: Write the migration**

Create `src/journal/store/migrations/010_lab_models.sql`:

```sql
-- 010: lab_models — trained regime and timing models (CLAUDE.md rule 9's one
-- predictive corner). Not derived from raw, so `journal rebuild` never touches
-- it. The fitted estimator lives in cache/models/<id>.joblib; config_json plus
-- the recorded seed is enough to rebuild that artifact, which is what keeps
-- cache/ disposable (rule 6).
CREATE TABLE IF NOT EXISTS lab_models (
    id            INTEGER PRIMARY KEY,
    created_ms    INTEGER NOT NULL,
    symbol        TEXT NOT NULL,          -- verbatim, e.g. 'XAUUSDc' (rule 11)
    timeframe     TEXT NOT NULL,          -- matches candles.timeframe
    stage         TEXT NOT NULL,          -- 'regime' | 'timing'
    regime        TEXT,                   -- NULL for stage='regime' or pooled
    kind          TEXT NOT NULL,          -- 'logreg' | 'lgbm'
    config_json   TEXT NOT NULL,
    metrics_json  TEXT NOT NULL,
    train_from_ms INTEGER NOT NULL,
    train_to_ms   INTEGER NOT NULL,
    n_rows        INTEGER NOT NULL,
    pooled        INTEGER NOT NULL DEFAULT 0,
    artifact_path TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 0
);

-- At most one active model per group. `regime` is NULL for regime-stage and
-- pooled rows and SQLite treats NULLs as distinct in a unique index, so the
-- key is COALESCE'd. The index is partial: superseded rows stay for history.
CREATE UNIQUE INDEX IF NOT EXISTS lab_models_active
    ON lab_models (symbol, timeframe, stage, COALESCE(regime, ''))
    WHERE active = 1;

CREATE INDEX IF NOT EXISTS lab_models_lookup
    ON lab_models (symbol, timeframe, created_ms DESC);
```

Append the identical `CREATE TABLE` and both `CREATE INDEX` statements to the end of `src/journal/store/schema.sql`, and bump `SCHEMA_VERSION` in `src/journal/store/db.py:20` from `9` to `10`.

- [ ] **Step 4: Write `lab/store.py`**

```python
"""lab_models persistence. The ONLY module under lab/ that touches sqlite.

Write-lock discipline (the lesson from deals.sync and fill_range): callers fit
first and call `save_models` afterwards, so no cursor is open across a fit. The
functions here are all short-transaction."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..store.db import now_ms
from .train import TrainedModel


class ArtifactMissing(Exception):
    """The row exists but cache/models/<id>.joblib is gone. Recoverable: the
    config is in the row, so retraining rebuilds the artifact byte-for-byte."""


def save_models(conn: sqlite3.Connection, *, symbol: str, timeframe: str,
                config: dict, models: list[TrainedModel], train_from_ms: int,
                train_to_ms: int, cache_dir: Path,
                activate_new: bool = True) -> list[int]:
    import joblib

    models_dir = Path(cache_dir) / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    created = now_ms()
    ids: list[int] = []

    for model in models:
        cur = conn.execute(
            """INSERT INTO lab_models
                   (created_ms, symbol, timeframe, stage, regime, kind,
                    config_json, metrics_json, train_from_ms, train_to_ms,
                    n_rows, pooled, artifact_path, active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (created, symbol, timeframe, model.stage, model.regime, model.kind,
             json.dumps({**config, "features": list(model.features)}),
             json.dumps(model.metrics), train_from_ms, train_to_ms,
             model.n_rows, int(model.pooled), ""),
        )
        model_id = int(cur.lastrowid)
        path = models_dir / f"{model_id}.joblib"
        joblib.dump(model.estimator, path)
        conn.execute("UPDATE lab_models SET artifact_path = ? WHERE id = ?",
                     (str(path), model_id))
        ids.append(model_id)
    conn.commit()

    if activate_new:
        # Activate the LightGBM row of each group by default; the UI can switch
        # to logreg afterwards. One transaction per group keeps the partial
        # unique index satisfied at every commit point.
        for model, model_id in zip(models, ids):
            if model.kind == "lgbm":
                activate(conn, model_id)
    return ids


def activate(conn: sqlite3.Connection, model_id: int) -> None:
    """Make one model the active one for its group, clearing the previous
    holder in the same transaction so the partial unique index never rejects a
    legitimate switch."""
    row = conn.execute(
        "SELECT symbol, timeframe, stage, regime FROM lab_models WHERE id = ?",
        (model_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no lab model with id {model_id}")
    conn.execute(
        """UPDATE lab_models SET active = 0
            WHERE symbol = ? AND timeframe = ? AND stage = ?
              AND COALESCE(regime, '') = COALESCE(?, '') AND active = 1""",
        (row["symbol"], row["timeframe"], row["stage"], row["regime"]),
    )
    conn.execute("UPDATE lab_models SET active = 1 WHERE id = ?", (model_id,))
    conn.commit()


def list_models(conn: sqlite3.Connection, symbol: str | None = None,
                timeframe: str | None = None) -> list[dict]:
    sql = "SELECT * FROM lab_models"
    where, args = [], []
    if symbol:
        where.append("symbol = ?")
        args.append(symbol)
    if timeframe:
        where.append("timeframe = ?")
        args.append(timeframe)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_ms DESC, id DESC"
    return [_row_to_dict(r) for r in conn.execute(sql, args)]


def load_active(conn: sqlite3.Connection, symbol: str, timeframe: str,
                stage: str, regime: str | None,
                cache_dir: Path) -> tuple[dict, Any] | None:
    import joblib

    row = conn.execute(
        """SELECT * FROM lab_models
            WHERE symbol = ? AND timeframe = ? AND stage = ?
              AND COALESCE(regime, '') = COALESCE(?, '') AND active = 1""",
        (symbol, timeframe, stage, regime),
    ).fetchone()
    if row is None:
        return None
    path = Path(row["artifact_path"])
    if not path.exists():
        raise ArtifactMissing(
            f"lab model {row['id']} has no artifact at {path}. Retrain from /lab."
        )
    return _row_to_dict(row), joblib.load(path)


def _row_to_dict(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["config"] = json.loads(out.pop("config_json"))
    out["metrics"] = json.loads(out.pop("metrics_json"))
    out["active"] = bool(out["active"])
    out["pooled"] = bool(out["pooled"])
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_store.py tests/test_migrations.py tests/test_db.py -v`
Expected: PASS

- [ ] **Step 6: Verify rebuild still works**

Run: `uv run journal rebuild`
Expected: succeeds, and `sqlite3 data/journal.db "SELECT COUNT(*) FROM lab_models"` still returns its previous count.

- [ ] **Step 7: Commit**

```bash
git add src/journal/store/migrations/010_lab_models.sql src/journal/store/schema.sql \
        src/journal/store/db.py src/journal/lab/store.py \
        tests/test_lab_store.py tests/test_migrations.py
git commit -m "feat(lab): lab_models table, migration 010, artifact persistence"
```

---

### Task 7: `lab/score.py` — run the trained models over bars

**Files:**
- Create: `src/journal/lab/score.py`
- Test: `tests/test_lab_score.py`

**Interfaces:**
- Consumes: `store.load_active`, `features.build_features/bars_to_frame`, `labels.REGIMES`, `train._SIDE_CODE` (re-declare locally rather than importing a private name).
- Produces:
  - `BarScore` — frozen dataclass: `time_msc: int`, `regime: str`, `regime_proba: dict[str, float]`, `p_tp_long: float | None`, `p_tp_short: float | None`.
  - `ScoreReport` — frozen dataclass: `symbol`, `timeframe`, `bars: list[BarScore]`, `model_age_ms: int | None`, `expectancy_r: float | None`, `pooled: bool`, `status: str` (`"ok"` / `"no_model"` / `"artifact_missing"` / `"no_bars"`).
  - `score_bars(conn, symbol, timeframe, bars, cache_dir) -> ScoreReport`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_score.py`:

```python
"""Scoring. The states that matter are the degraded ones — no model, missing
artifact, not enough bars — because /live renders them next to order buttons
and must never show a stale or invented number."""
from __future__ import annotations

import numpy as np
import pytest

from journal.adapter.base import Candle
from journal.lab.features import bars_to_frame, build_features
from journal.lab.labels import LabelConfig
from journal.lab.score import score_bars
from journal.lab.store import save_models
from journal.lab.train import TrainConfig, train_all
from journal.store.db import connect, init_db

MINUTE = 60_000
FEATURES = ("ret_1", "ret_5", "atr_rel", "hour_utc")


def _walk(n: int, seed: int = 0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    price, bars = 2000.0, []
    for i in range(n):
        open_ = price
        price += float(rng.normal(0, 1.5))
        bars.append(Candle(time_msc=i * MINUTE, open=open_,
                           high=max(open_, price) + 0.4,
                           low=min(open_, price) - 0.4, close=price,
                           tick_volume=100, spread=20, real_volume=0))
    return bars


@pytest.fixture()
def trained(tmp_path):
    conn = connect(tmp_path / "journal.db")
    init_db(conn)
    bars = _walk(1200)
    cfg = TrainConfig(label=LabelConfig(n_bars=8), features=FEATURES,
                      point=0.001, n_folds=3, pooled_min_rows=10**6)
    models = train_all(build_features(bars_to_frame(bars)), cfg)
    save_models(conn, symbol="XAUUSDc", timeframe="M1",
                config={"n_bars": 8, "seed": 7, "features": list(FEATURES)},
                models=models, train_from_ms=0, train_to_ms=1200 * MINUTE,
                cache_dir=tmp_path / "cache")
    yield conn, bars, tmp_path / "cache"
    conn.close()


def test_scores_every_bar_it_can(trained):
    conn, bars, cache = trained
    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.status == "ok"
    assert report.bars
    for bar in report.bars:
        assert bar.regime in {"trend_up", "trend_down", "range"}
        assert 0.0 <= bar.p_tp_long <= 1.0
        assert 0.0 <= bar.p_tp_short <= 1.0
        assert bar.regime_proba.keys() == {"trend_up", "trend_down", "range"}


def test_report_carries_model_age_and_expectancy(trained):
    conn, bars, cache = trained
    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.model_age_ms is not None and report.model_age_ms >= 0
    assert "expectancy_r" in report.__dict__
    assert report.pooled is True


def test_no_model_is_a_status_not_an_exception(tmp_path):
    conn = connect(tmp_path / "journal.db")
    init_db(conn)
    report = score_bars(conn, "XAUUSDc", "H1", _walk(200), tmp_path / "cache")
    assert report.status == "no_model"
    assert report.bars == []
    conn.close()


def test_missing_artifact_is_a_status_not_an_exception(trained):
    conn, bars, cache = trained
    for path in (cache / "models").glob("*.joblib"):
        path.unlink()
    report = score_bars(conn, "XAUUSDc", "M1", bars[-200:], cache)
    assert report.status == "artifact_missing"
    assert report.bars == []


def test_too_few_bars_to_compute_features_is_a_status(trained):
    conn, bars, cache = trained
    report = score_bars(conn, "XAUUSDc", "M1", bars[:5], cache)
    assert report.status == "no_bars"
    assert report.bars == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.lab.score'`

- [ ] **Step 3: Write the implementation**

Create `src/journal/lab/score.py`:

```python
"""Scoring bars with the active models.

Every failure is a STATUS, never an exception that escapes: this output is
rendered on /live beside the order buttons, and a blank panel with a reason is
honest where a stale number is not."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..adapter.base import Candle
from ..store.db import now_ms
from .features import build_features, bars_to_frame
from .labels import REGIMES
from .store import ArtifactMissing, load_active

_SIDE_CODE = {"long": 1, "short": 0}


@dataclass(frozen=True)
class BarScore:
    time_msc: int
    regime: str
    regime_proba: dict[str, float]
    p_tp_long: float | None
    p_tp_short: float | None


@dataclass(frozen=True)
class ScoreReport:
    symbol: str
    timeframe: str
    bars: list[BarScore]
    model_age_ms: int | None
    expectancy_r: float | None
    pooled: bool
    status: str                 # ok | no_model | artifact_missing | no_bars


def score_bars(conn: sqlite3.Connection, symbol: str, timeframe: str,
               bars: list[Candle], cache_dir: Path) -> ScoreReport:
    empty = ScoreReport(symbol, timeframe, [], None, None, False, "no_model")
    try:
        loaded = load_active(conn, symbol, timeframe, "regime", None, cache_dir)
    except ArtifactMissing:
        return ScoreReport(symbol, timeframe, [], None, None, False,
                           "artifact_missing")
    if loaded is None:
        return empty
    regime_row, regime_model = loaded

    features = tuple(regime_row["config"]["features"])
    price_features = tuple(f for f in features if f != "side")

    df = build_features(bars_to_frame(bars))
    if df.empty:
        return ScoreReport(symbol, timeframe, [], None, None, False, "no_bars")
    usable = df.dropna(subset=list(price_features))
    if usable.empty:
        return ScoreReport(symbol, timeframe, [], None, None, False, "no_bars")

    x_regime = _matrix(usable, features, side=None)
    predicted = regime_model.predict(x_regime)
    proba = _class_proba(regime_model, x_regime)

    timing: dict[str | None, tuple[dict, object]] = {}
    try:
        for regime in (*REGIMES, None):
            got = load_active(conn, symbol, timeframe, "timing", regime, cache_dir)
            if got is not None:
                timing[regime] = got
    except ArtifactMissing:
        return ScoreReport(symbol, timeframe, [], None, None, False,
                           "artifact_missing")
    if not timing:
        return empty

    pooled = None in timing
    long_p = _timing_proba(timing, predicted, usable, features, "long", pooled)
    short_p = _timing_proba(timing, predicted, usable, features, "short", pooled)

    # Provenance comes from whichever timing model actually scored the LAST
    # bar. A predicted regime with no active model is possible (a run that
    # trained only some regimes), so fall back rather than KeyError.
    latest_regime = str(predicted[-1])
    row = timing.get(None) or timing.get(latest_regime) or next(iter(timing.values()))
    scored = [
        BarScore(
            time_msc=int(t),
            regime=str(predicted[i]),
            regime_proba=proba[i],
            p_tp_long=None if long_p is None else float(long_p[i]),
            p_tp_short=None if short_p is None else float(short_p[i]),
        )
        for i, t in enumerate(usable.index)
    ]
    return ScoreReport(
        symbol=symbol, timeframe=timeframe, bars=scored,
        model_age_ms=now_ms() - int(row[0]["created_ms"]),
        expectancy_r=row[0]["metrics"].get("expectancy_r"),  # out-of-sample
        pooled=pooled, status="ok",
    )


def _matrix(df, features: tuple[str, ...], side: str | None) -> np.ndarray:
    columns = []
    for name in features:
        if name == "side":
            value = _SIDE_CODE[side] if side else 0
            columns.append(np.full(len(df), float(value)))
        else:
            columns.append(df[name].to_numpy(dtype="float64"))
    return np.column_stack(columns)


def _class_proba(model, x) -> list[dict[str, float]]:
    raw = model.predict_proba(x)
    classes = list(getattr(model, "classes_", REGIMES))
    return [
        {r: float(row[classes.index(r)]) if r in classes else 0.0 for r in REGIMES}
        for row in raw
    ]


def _timing_proba(timing, predicted, df, features, side, pooled):
    """One probability per row. With per-regime models each row is scored by
    the model for ITS predicted regime, so this walks the regimes rather than
    calling predict once."""
    out = np.full(len(df), np.nan)
    if pooled:
        _, model = timing[None]
        return model.predict_proba(_matrix(df, features, side))[:, 1]
    for regime, (_, model) in timing.items():
        mask = predicted == regime
        if not mask.any():
            continue
        out[mask] = model.predict_proba(
            _matrix(df[mask], features, side)
        )[:, 1]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_score.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS, every test.

- [ ] **Step 6: Commit**

```bash
git add src/journal/lab/score.py tests/test_lab_score.py
git commit -m "feat(lab): score bars with the active models, degraded states as status"
```

---

### Task 8: HTTP layer — payloads and routes

**Files:**
- Create: `src/journal/web/lab_api.py`
- Modify: `src/journal/web/app.py` (add routes next to the existing `/api/*` group)
- Test: `tests/test_lab_api.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7, plus `store.candles_store.load_bars` and `store.symbol_specs` (for `point`).
- Produces these endpoints:

| method | path | body / query | returns |
|---|---|---|---|
| POST | `/api/lab/train` | `{symbol, timeframe, n_bars, k_atr, rr, er_threshold, features[], n_folds, threshold, default_spread_points, from_ms?, to_ms?}` | `{model_ids[], models[], dropped_features{}, spread_assumed: bool}` |
| GET | `/api/lab/models` | `?symbol&timeframe` | `{models: [...]}` |
| POST | `/api/lab/models/{id}/activate` | — | `{ok: true, id}` |
| GET | `/api/lab/score` | `?symbol&timeframe&bars=300` | `ScoreReport` as JSON |
| GET | `/api/lab/regimes` | `?symbol&timeframe&from_ms&to_ms` | `{bars: [{time_msc, regime, p_tp_long, p_tp_short}], status}` |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_api.py`, following the client/fixture style already used by `tests/test_api.py` and `tests/test_storage_api.py`:

```python
"""Lab HTTP surface. Uses the same TestClient fixture style as
tests/test_storage_api.py — read that file first and mirror its app/db setup.

If `client` and `conn` are local fixtures there rather than in a shared
conftest, copy them into this module rather than moving them; relocating a
fixture other suites depend on is a change this task did not ask for."""
from __future__ import annotations

import numpy as np
import pytest

from journal.store.candles_store import insert_candle
from journal.adapter.base import Candle

HOUR = 3_600_000


def _seed_candles(conn, n=1500, symbol="XAUUSDc", timeframe="H1"):
    # `lab_api._point_for` refuses to train without symbol_specs.point — an
    # unknown point silently rescales every spread cost, so it is not guessed.
    conn.execute(
        """INSERT OR REPLACE INTO symbol_specs
               (symbol, symbol_base, digits, point, tick_size, tick_value,
                contract_size, currency_profit, fetched_at)
           VALUES (?, ?, 3, 0.001, 0.001, 0.1, 1.0, 'USD', 0)""",
        (symbol, symbol.rstrip("c")),
    )
    rng = np.random.default_rng(0)
    price = 2000.0
    for i in range(n):
        open_ = price
        price += float(rng.normal(0, 3.0))
        insert_candle(conn, symbol, timeframe, Candle(
            time_msc=i * HOUR, open=open_, high=max(open_, price) + 1.0,
            low=min(open_, price) - 1.0, close=price, tick_volume=500,
            spread=25, real_volume=0))
    conn.commit()


def _train_body(**kw):
    body = {
        "symbol": "XAUUSDc", "timeframe": "H1", "n_bars": 8, "k_atr": 1.0,
        "rr": 2.0, "er_threshold": 0.35,
        "features": ["ret_1", "ret_5", "atr_rel", "hour_utc"],
        "n_folds": 3, "threshold": 0.5, "default_spread_points": 0.0,
    }
    body.update(kw)
    return body


def test_train_returns_models_and_persists_them(client, conn):
    _seed_candles(conn)
    r = client.post("/api/lab/train", json=_train_body())
    assert r.status_code == 200
    body = r.json()
    assert body["model_ids"]
    assert {m["stage"] for m in body["models"]} == {"regime", "timing"}
    assert {m["kind"] for m in body["models"]} == {"logreg", "lgbm"}

    listed = client.get("/api/lab/models?symbol=XAUUSDc&timeframe=H1").json()
    assert len(listed["models"]) == len(body["model_ids"])


def test_train_reports_a_feature_it_had_to_drop(client, conn):
    _seed_candles(conn)
    conn.execute("UPDATE candles SET spread = NULL")
    conn.commit()
    body = client.post("/api/lab/train",
                       json=_train_body(features=["ret_1", "spread"])).json()
    assert "spread" in body["dropped_features"]


def test_train_rejects_an_unknown_feature_name(client, conn):
    _seed_candles(conn)
    r = client.post("/api/lab/train", json=_train_body(features=["ret_1", "moon"]))
    assert r.status_code == 400
    assert "moon" in r.json()["detail"]


def test_train_refuses_when_there_are_not_enough_bars(client, conn):
    _seed_candles(conn, n=60)
    r = client.post("/api/lab/train", json=_train_body())
    assert r.status_code == 400
    assert "not enough" in r.json()["detail"].lower()


def test_activate_switches_the_active_model(client, conn):
    _seed_candles(conn)
    body = client.post("/api/lab/train", json=_train_body()).json()
    logreg = [m for m in body["models"]
              if m["stage"] == "timing" and m["kind"] == "logreg"][0]
    assert client.post(f"/api/lab/models/{logreg['id']}/activate").status_code == 200
    listed = client.get("/api/lab/models?symbol=XAUUSDc&timeframe=H1").json()
    active = [m for m in listed["models"]
              if m["active"] and m["stage"] == "timing"]
    assert [m["id"] for m in active] == [logreg["id"]]


def test_score_reports_no_model_before_training(client, conn):
    _seed_candles(conn)
    body = client.get("/api/lab/score?symbol=XAUUSDc&timeframe=H1").json()
    assert body["status"] == "no_model"


def test_score_returns_the_latest_bar_after_training(client, conn):
    _seed_candles(conn)
    client.post("/api/lab/train", json=_train_body())
    body = client.get("/api/lab/score?symbol=XAUUSDc&timeframe=H1&bars=200").json()
    assert body["status"] == "ok"
    assert body["bars"]
    assert body["model_age_ms"] >= 0


def test_train_refuses_a_symbol_with_no_point_spec(client, conn):
    _seed_candles(conn)
    conn.execute("DELETE FROM symbol_specs WHERE symbol = 'XAUUSDc'")
    conn.commit()
    r = client.post("/api/lab/train", json=_train_body())
    assert r.status_code == 400
    assert "symbol_specs" in r.json()["detail"]


def test_regimes_endpoint_covers_the_requested_window(client, conn):
    _seed_candles(conn)
    client.post("/api/lab/train", json=_train_body())
    body = client.get(
        f"/api/lab/regimes?symbol=XAUUSDc&timeframe=H1&from_ms=0&to_ms={400 * HOUR}"
    ).json()
    assert body["status"] == "ok"
    assert all(0 <= b["time_msc"] <= 400 * HOUR for b in body["bars"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lab_api.py -v`
Expected: FAIL — no `/api/lab/train` route (404) or fixture import error.

- [ ] **Step 3: Write `src/journal/web/lab_api.py`**

```python
"""Payload builders for the lab endpoints. Mirrors the existing split: this
module does the work, `app.py` only wires routes to it.

The two-phase shape here is the point. `train()` reads bars in one short call,
lets that transaction end, fits with no cursor open, and only then writes. A
training run must never hold the WAL writer — the same rule that fixed the
on-close ingest freeze."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..lab.features import PRICE_FEATURES, bars_to_frame, build_features, usable_columns
from ..lab.labels import LabelConfig
from ..lab.score import score_bars
from ..lab.store import ArtifactMissing, activate, list_models, save_models
from ..lab.train import TrainConfig, train_all
from ..store.candles_store import load_bars

DEFAULT_SCORE_BARS = 300


class LabRequestError(ValueError):
    """Caller error: a bad feature name, an unknown symbol, too little data.
    `app.py` turns this into a 400 with the message intact."""


def train(conn: sqlite3.Connection, body: dict, cache_dir: Path) -> dict:
    symbol = str(body["symbol"])
    timeframe = str(body["timeframe"])
    wanted = list(body.get("features") or PRICE_FEATURES)

    unknown = [f for f in wanted if f not in PRICE_FEATURES]
    if unknown:
        raise LabRequestError(f"unknown feature(s): {', '.join(unknown)}")

    point = _point_for(conn, symbol)
    from_ms = int(body.get("from_ms") or 0)
    to_ms = int(body.get("to_ms") or 2**62)

    # Phase 1: read. Short, and finished before anything expensive starts.
    bars = load_bars(conn, symbol, timeframe, from_ms, to_ms)
    if not bars:
        raise LabRequestError(
            f"no candles stored for {symbol} {timeframe}. Fill the range first."
        )

    # Phase 2: compute + fit. No DB handle in use.
    df = build_features(bars_to_frame(bars))
    kept, dropped = usable_columns(df, wanted)
    if not kept:
        raise LabRequestError("every requested feature was unusable on this range")

    label = LabelConfig(
        n_bars=int(body.get("n_bars", 24)),
        k_atr=float(body.get("k_atr", 1.0)),
        rr=float(body.get("rr", 2.0)),
        er_threshold=float(body.get("er_threshold", 0.35)),
    )
    cfg = TrainConfig(
        label=label, features=tuple(kept), point=point,
        n_folds=int(body.get("n_folds", 5)), seed=int(body.get("seed", 7)),
        threshold=float(body.get("threshold", 0.5)),
        default_spread_points=float(body.get("default_spread_points", 0.0)),
        pooled_min_rows=int(body.get("pooled_min_rows", 500)),
    )
    try:
        models = train_all(df, cfg)
    except ValueError as exc:
        raise LabRequestError(str(exc)) from exc

    # Phase 3: write. Short again.
    config = {
        "n_bars": label.n_bars, "k_atr": label.k_atr, "rr": label.rr,
        "er_threshold": label.er_threshold, "n_folds": cfg.n_folds,
        "seed": cfg.seed, "threshold": cfg.threshold,
        "default_spread_points": cfg.default_spread_points,
        "pooled_min_rows": cfg.pooled_min_rows, "point": point,
    }
    ids = save_models(
        conn, symbol=symbol, timeframe=timeframe, config=config, models=models,
        train_from_ms=int(bars[0].time_msc), train_to_ms=int(bars[-1].time_msc),
        cache_dir=cache_dir,
    )
    rows = {r["id"]: r for r in list_models(conn, symbol, timeframe)}
    return {
        "model_ids": ids,
        "models": [rows[i] for i in ids if i in rows],
        "dropped_features": dropped,
        "spread_assumed": "spread" in dropped,
        "n_bars_read": len(bars),
    }


def models_payload(conn: sqlite3.Connection, symbol: str | None,
                   timeframe: str | None) -> dict:
    return {"models": list_models(conn, symbol, timeframe)}


def activate_payload(conn: sqlite3.Connection, model_id: int) -> dict:
    try:
        activate(conn, model_id)
    except ValueError as exc:
        raise LabRequestError(str(exc)) from exc
    return {"ok": True, "id": model_id}


def score_payload(conn: sqlite3.Connection, symbol: str, timeframe: str,
                  n_bars: int, cache_dir: Path) -> dict:
    bars = load_bars(conn, symbol, timeframe, 0, 2**62)
    report = score_bars(conn, symbol, timeframe, bars[-n_bars:], cache_dir)
    return _report_to_dict(report)


def regimes_payload(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    from_ms: int, to_ms: int, cache_dir: Path) -> dict:
    # Features need history before `from_ms` (EMA50, ATR14, ret_20), so read
    # from the start and clip the answer back to the window the caller asked for.
    bars = load_bars(conn, symbol, timeframe, 0, to_ms)
    report = score_bars(conn, symbol, timeframe, bars, cache_dir)
    out = _report_to_dict(report)
    out["bars"] = [b for b in out["bars"] if from_ms <= b["time_msc"] <= to_ms]
    return out


def _report_to_dict(report) -> dict:
    return {
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "status": report.status,
        "model_age_ms": report.model_age_ms,
        "expectancy_r": report.expectancy_r,
        "pooled": report.pooled,
        "bars": [
            {
                "time_msc": b.time_msc,
                "regime": b.regime,
                "regime_proba": b.regime_proba,
                "p_tp_long": b.p_tp_long,
                "p_tp_short": b.p_tp_short,
            }
            for b in report.bars
        ],
    }


def _point_for(conn: sqlite3.Connection, symbol: str) -> float:
    """`symbol_specs.point` converts the spread column into price. Unknown means
    unknown (rule 4) — refuse rather than guess, because a wrong point silently
    scales every cost number."""
    row = conn.execute("SELECT point FROM symbol_specs WHERE symbol = ?",
                       (symbol,)).fetchone()
    if row is None or row["point"] is None:
        raise LabRequestError(
            f"no symbol_specs.point for {symbol}; run `journal sync` first"
        )
    return float(row["point"])
```

- [ ] **Step 4: Wire the routes in `app.py`**

Add next to the existing `/api/*` routes, following the `Depends(get_conn)` pattern already in the file:

```python
    from ..web import lab_api

    def _lab(fn, *args, **kwargs):
        """LabRequestError is a caller mistake, not a server fault."""
        from fastapi import HTTPException
        try:
            return fn(*args, **kwargs)
        except lab_api.LabRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/lab/train")
    def api_lab_train(body=Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        return _lab(lab_api.train, conn, body, cache_dir)

    @app.get("/api/lab/models")
    def api_lab_models(symbol: str | None = None, timeframe: str | None = None,
                       conn: sqlite3.Connection = Depends(get_conn)):
        return lab_api.models_payload(conn, symbol, timeframe)

    @app.post("/api/lab/models/{model_id}/activate")
    def api_lab_activate(model_id: int,
                         conn: sqlite3.Connection = Depends(get_conn)):
        return _lab(lab_api.activate_payload, conn, model_id)

    @app.get("/api/lab/score")
    def api_lab_score(symbol: str, timeframe: str,
                      bars: int = lab_api.DEFAULT_SCORE_BARS,
                      conn: sqlite3.Connection = Depends(get_conn)):
        return lab_api.score_payload(conn, symbol, timeframe, bars, cache_dir)

    @app.get("/api/lab/regimes")
    def api_lab_regimes(symbol: str, timeframe: str, from_ms: int, to_ms: int,
                        conn: sqlite3.Connection = Depends(get_conn)):
        return lab_api.regimes_payload(conn, symbol, timeframe, from_ms, to_ms,
                                       cache_dir)
```

`cache_dir` must be resolved the same way the existing chart routes resolve it in `app.py` — read how `create_app` already obtains the cache directory and reuse that exact value rather than constructing a new `Path`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_lab_api.py -v`
Expected: PASS, 9 tests

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/journal/web/lab_api.py src/journal/web/app.py tests/test_lab_api.py
git commit -m "feat(lab): /api/lab train, models, activate, score, regimes"
```

---

### Task 9: Frontend types and API client

**Files:**
- Create: `frontend/src/lib/lab.ts`
- Create: `frontend/src/lib/lab.test.ts`
- Modify: `frontend/src/lib/types.ts` (append the lab types)

**Interfaces:**
- Consumes: the endpoints from Task 8, and the existing `postJson` helper in `frontend/src/lib/api.ts`.
- Produces:
  - `LAB_FEATURES: readonly string[]` — mirrors `PRICE_FEATURES`.
  - `DEFAULT_TRAIN_FORM: TrainForm` and `type TrainForm`.
  - `type LabModel`, `type LabScore`, `type LabBarScore`.
  - `trainModels(form): Promise<TrainResponse>`, `fetchModels(symbol, timeframe)`, `activateModel(id)`, `fetchScore(symbol, timeframe, bars)`, `fetchRegimes(symbol, timeframe, fromMs, toMs)`.
  - `regimeColor(regime): string` and `formatAge(ms): string` — pure, tested.
  - `bestModel(models, stage): LabModel | null` — the active one, else the newest.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/lab.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { bestModel, formatAge, regimeColor, DEFAULT_TRAIN_FORM, LAB_FEATURES } from "./lab";
import type { LabModel } from "./types";

const model = (over: Partial<LabModel>): LabModel =>
  ({
    id: 1, created_ms: 0, symbol: "XAUUSDc", timeframe: "H1", stage: "timing",
    regime: null, kind: "lgbm", pooled: false, active: false, n_rows: 100,
    train_from_ms: 0, train_to_ms: 1, config: {}, metrics: { n: 100 },
    ...over,
  } as LabModel);

describe("regimeColor", () => {
  it("gives each regime its own colour and never returns empty", () => {
    const seen = new Set(["trend_up", "trend_down", "range"].map(regimeColor));
    expect(seen.size).toBe(3);
    expect(regimeColor("nonsense")).not.toBe("");
  });
});

describe("formatAge", () => {
  it("reads in days once past a day", () => {
    expect(formatAge(3 * 86_400_000)).toBe("3d ago");
  });
  it("reads in hours under a day", () => {
    expect(formatAge(5 * 3_600_000)).toBe("5h ago");
  });
  it("says just now under an hour", () => {
    expect(formatAge(60_000)).toBe("just now");
  });
  it("handles a missing age", () => {
    expect(formatAge(null)).toBe("never trained");
  });
});

describe("bestModel", () => {
  it("prefers the active model", () => {
    const models = [
      model({ id: 1, created_ms: 100, active: false }),
      model({ id: 2, created_ms: 50, active: true }),
    ];
    expect(bestModel(models, "timing")?.id).toBe(2);
  });
  it("falls back to the newest when none is active", () => {
    const models = [
      model({ id: 1, created_ms: 100 }),
      model({ id: 2, created_ms: 50 }),
    ];
    expect(bestModel(models, "timing")?.id).toBe(1);
  });
  it("ignores other stages", () => {
    expect(bestModel([model({ stage: "regime", active: true })], "timing")).toBeNull();
  });
});

describe("defaults", () => {
  it("starts with every feature on", () => {
    expect(DEFAULT_TRAIN_FORM.features).toEqual([...LAB_FEATURES]);
  });
  it("carries the spec's default label parameters", () => {
    expect(DEFAULT_TRAIN_FORM.n_bars).toBe(24);
    expect(DEFAULT_TRAIN_FORM.k_atr).toBe(1);
    expect(DEFAULT_TRAIN_FORM.rr).toBe(2);
    expect(DEFAULT_TRAIN_FORM.er_threshold).toBe(0.35);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/lab.test.ts`
Expected: FAIL — cannot resolve `./lab`

- [ ] **Step 3: Write the types**

Append to `frontend/src/lib/types.ts`:

```ts
export type LabStage = "regime" | "timing";
export type LabKind = "logreg" | "lgbm";
export type Regime = "trend_up" | "trend_down" | "range";

export type LabFoldMetrics = {
  n: number;
  n_taken: number;
  win_rate: number | null;
  expectancy_r: number | null;
  auc: number | null;
  baseline_expectancy_r: number | null;
  calibration: { bucket: number; predicted: number; realised: number; n: number }[];
  confusion?: Record<string, Record<string, number>>;
};

export type LabMetrics = LabFoldMetrics & { folds: LabFoldMetrics[] };

export type LabModel = {
  id: number;
  created_ms: number;
  symbol: string;
  timeframe: string;
  stage: LabStage;
  regime: Regime | null;
  kind: LabKind;
  pooled: boolean;
  active: boolean;
  n_rows: number;
  train_from_ms: number;
  train_to_ms: number;
  config: Record<string, unknown>;
  metrics: LabMetrics;
};

export type LabBarScore = {
  time_msc: number;
  regime: Regime;
  regime_proba: Record<Regime, number>;
  p_tp_long: number | null;
  p_tp_short: number | null;
};

export type LabScore = {
  symbol: string;
  timeframe: string;
  status: "ok" | "no_model" | "artifact_missing" | "no_bars";
  model_age_ms: number | null;
  expectancy_r: number | null;
  pooled: boolean;
  bars: LabBarScore[];
};
```

- [ ] **Step 4: Write `frontend/src/lib/lab.ts`**

```ts
import { postJson } from "./api";
import type { LabModel, LabScore, LabStage } from "./types";

export const LAB_FEATURES = [
  "ret_1", "ret_5", "ret_20",
  "atr_rel",
  "ema20_dist", "ema50_dist",
  "body_ratio", "upper_wick", "lower_wick",
  "range_pct",
  "vol_rel",
  "spread",
  "hour_utc", "dow",
] as const;

export type TrainForm = {
  symbol: string;
  timeframe: string;
  n_bars: number;
  k_atr: number;
  rr: number;
  er_threshold: number;
  n_folds: number;
  threshold: number;
  default_spread_points: number;
  features: string[];
};

export const DEFAULT_TRAIN_FORM: TrainForm = {
  symbol: "XAUUSDc",
  timeframe: "H1",
  n_bars: 24,
  k_atr: 1,
  rr: 2,
  er_threshold: 0.35,
  n_folds: 5,
  threshold: 0.5,
  default_spread_points: 0,
  features: [...LAB_FEATURES],
};

export type TrainResponse = {
  model_ids: number[];
  models: LabModel[];
  dropped_features: Record<string, number>;
  spread_assumed: boolean;
  n_bars_read: number;
};

// Muted enough to sit behind candles without competing with them.
const REGIME_COLORS: Record<string, string> = {
  trend_up: "rgba(38, 166, 154, 0.10)",
  trend_down: "rgba(239, 83, 80, 0.10)",
  range: "rgba(120, 120, 120, 0.08)",
};

export function regimeColor(regime: string): string {
  return REGIME_COLORS[regime] ?? "rgba(120, 120, 120, 0.05)";
}

export function formatAge(ms: number | null): string {
  if (ms === null || ms === undefined) return "never trained";
  const days = Math.floor(ms / 86_400_000);
  if (days >= 1) return `${days}d ago`;
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `${hours}h ago`;
  return "just now";
}

export function bestModel(models: LabModel[], stage: LabStage): LabModel | null {
  const ofStage = models.filter((m) => m.stage === stage);
  if (ofStage.length === 0) return null;
  const active = ofStage.find((m) => m.active);
  if (active) return active;
  return ofStage.reduce((a, b) => (b.created_ms > a.created_ms ? b : a));
}

export const trainModels = (form: TrainForm) =>
  postJson<TrainResponse>("/api/lab/train", form);

export const activateModel = (id: number) =>
  postJson<{ ok: boolean; id: number }>(`/api/lab/models/${id}/activate`, {});

export async function fetchModels(symbol: string, timeframe: string) {
  const r = await fetch(`/api/lab/models?symbol=${symbol}&timeframe=${timeframe}`);
  return (await r.json()) as { models: LabModel[] };
}

export async function fetchScore(symbol: string, timeframe: string, bars = 300) {
  const r = await fetch(
    `/api/lab/score?symbol=${symbol}&timeframe=${timeframe}&bars=${bars}`,
  );
  return (await r.json()) as LabScore;
}

export async function fetchRegimes(
  symbol: string, timeframe: string, fromMs: number, toMs: number,
) {
  const r = await fetch(
    `/api/lab/regimes?symbol=${symbol}&timeframe=${timeframe}` +
      `&from_ms=${fromMs}&to_ms=${toMs}`,
  );
  return (await r.json()) as LabScore;
}
```

Check `frontend/src/lib/api.ts` for `postJson`'s exact signature before using it; if it takes `(path, body)` in a different order or returns a wrapped shape, match the existing call sites in `replayApi.ts` rather than the sketch above.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/lab.test.ts && npx tsc --noEmit`
Expected: PASS, 10 tests; tsc reports no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/lab.ts frontend/src/lib/lab.test.ts frontend/src/lib/types.ts
git commit -m "feat(lab): frontend lab types, api client, pure helpers"
```

---

### Task 10: `/lab` page — form, training, metrics

Chart integration is Task 11. This task ships a usable page: configure, train, read the numbers.

**Files:**
- Create: `frontend/src/pages/Lab.tsx`
- Create: `frontend/src/components/LabMetrics.tsx`
- Create: `frontend/src/pages/Lab.test.tsx`
- Modify: `frontend/src/App.tsx` (add the route)
- Modify: `frontend/src/components/AppShell.tsx` (add the nav link)

**Interfaces:**
- Consumes: everything from Task 9.
- Produces: `<Lab />` at route `/lab`; `<LabMetrics models={LabModel[]} onActivate={(id) => void} />`.

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/pages/Lab.test.tsx`, mirroring the mocking style already used in `frontend/src/pages/TradeView.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Lab from "./Lab";
import type { LabModel } from "../lib/types";

const trainModels = vi.fn();
const fetchModels = vi.fn();
const activateModel = vi.fn();

vi.mock("../lib/lab", async () => {
  const actual = await vi.importActual<typeof import("../lib/lab")>("../lib/lab");
  return {
    ...actual,
    trainModels: (...a: unknown[]) => trainModels(...a),
    fetchModels: (...a: unknown[]) => fetchModels(...a),
    activateModel: (...a: unknown[]) => activateModel(...a),
    fetchRegimes: vi.fn().mockResolvedValue({ status: "no_model", bars: [] }),
  };
});

const model = (over: Partial<LabModel>): LabModel =>
  ({
    id: 1, created_ms: Date.now(), symbol: "XAUUSDc", timeframe: "H1",
    stage: "timing", regime: null, kind: "lgbm", pooled: true, active: true,
    n_rows: 900, train_from_ms: 0, train_to_ms: 1,
    config: {}, metrics: { n: 900, n_taken: 400, win_rate: 0.41,
      expectancy_r: 0.12, auc: 0.55, baseline_expectancy_r: -0.03,
      calibration: [], folds: [] },
    ...over,
  } as LabModel);

beforeEach(() => {
  trainModels.mockReset();
  fetchModels.mockReset().mockResolvedValue({ models: [] });
  activateModel.mockReset().mockResolvedValue({ ok: true, id: 1 });
});

describe("Lab page", () => {
  it("renders a checkbox per feature, all on by default", async () => {
    render(<Lab />);
    const boxes = await screen.findAllByRole("checkbox");
    expect(boxes).toHaveLength(14);
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);
  });

  it("posts the form when Train is pressed", async () => {
    trainModels.mockResolvedValue({ model_ids: [1], models: [model({})],
      dropped_features: {}, spread_assumed: false, n_bars_read: 1000 });
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: /train/i }));
    await waitFor(() => expect(trainModels).toHaveBeenCalledTimes(1));
    expect(trainModels.mock.calls[0][0]).toMatchObject({
      symbol: "XAUUSDc", n_bars: 24, rr: 2,
    });
  });

  it("shows expectancy in R beside the baseline", async () => {
    fetchModels.mockResolvedValue({ models: [model({})] });
    render(<Lab />);
    expect(await screen.findByText(/0\.12/)).toBeInTheDocument();
    expect(await screen.findByText(/-0\.03/)).toBeInTheDocument();
  });

  it("suppresses a rate computed from fewer than 20 rows", async () => {
    fetchModels.mockResolvedValue({
      models: [model({ metrics: { n: 5, n_taken: 5, win_rate: 0.8,
        expectancy_r: 3.0, auc: null, baseline_expectancy_r: 0,
        calibration: [], folds: [] } })],
    });
    render(<Lab />);
    expect(await screen.findByText(/n\s*=\s*5/)).toBeInTheDocument();
    expect(screen.queryByText(/80%/)).not.toBeInTheDocument();
  });

  it("surfaces a dropped feature after training", async () => {
    trainModels.mockResolvedValue({ model_ids: [1], models: [model({})],
      dropped_features: { spread: 0.9 }, spread_assumed: true, n_bars_read: 900 });
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: /train/i }));
    expect(await screen.findByText(/spread/i)).toBeInTheDocument();
  });

  it("shows the server's message when training is refused", async () => {
    trainModels.mockRejectedValue(new Error("not enough labelled rows to train: 12"));
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: /train/i }));
    expect(await screen.findByText(/not enough labelled rows/i)).toBeInTheDocument();
  });

  it("activates a model when its button is pressed", async () => {
    fetchModels.mockResolvedValue({
      models: [model({ id: 7, kind: "logreg", active: false })],
    });
    render(<Lab />);
    await userEvent.click(await screen.findByRole("button", { name: /activate/i }));
    await waitFor(() => expect(activateModel).toHaveBeenCalledWith(7));
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/Lab.test.tsx`
Expected: FAIL — cannot resolve `./Lab`

- [ ] **Step 3: Write `frontend/src/components/LabMetrics.tsx`**

```tsx
import type { LabModel } from "../lib/types";
import { formatAge } from "../lib/lab";

const MIN_BUCKET_N = 20;   // CLAUDE.md §8, mirrored from lab/evaluate.py

/** A rate from fewer than 20 rows is noise with a decimal point. Render a dash
 *  rather than a number that invites a decision. */
function rate(value: number | null, n: number): string {
  if (value === null || value === undefined || n < MIN_BUCKET_N) return "—";
  return `${(value * 100).toFixed(0)}%`;
}

function r(value: number | null, n: number): string {
  if (value === null || value === undefined || n < MIN_BUCKET_N) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`;
}

export default function LabMetrics({
  models, onActivate,
}: {
  models: LabModel[];
  onActivate: (id: number) => void;
}) {
  if (models.length === 0) return <p className="muted">No models trained yet.</p>;
  return (
    <table className="lab-metrics">
      <thead>
        <tr>
          <th>stage</th><th>regime</th><th>model</th>
          <th>expectancy</th><th>baseline</th><th>win</th><th>AUC</th>
          <th>n</th><th>age</th><th></th>
        </tr>
      </thead>
      <tbody>
        {models.map((m) => {
          const n = m.metrics?.n ?? 0;
          return (
            <tr key={m.id} className={m.active ? "active" : undefined}>
              <td>{m.stage}</td>
              <td>{m.pooled ? "pooled" : m.regime ?? "—"}</td>
              <td>{m.kind}</td>
              <td>{r(m.metrics?.expectancy_r ?? null, m.metrics?.n_taken ?? 0)}</td>
              <td>{r(m.metrics?.baseline_expectancy_r ?? null, n)}</td>
              <td>{rate(m.metrics?.win_rate ?? null, m.metrics?.n_taken ?? 0)}</td>
              <td>{m.metrics?.auc?.toFixed(2) ?? "—"}</td>
              <td>n = {n}</td>
              <td>{formatAge(Date.now() - m.created_ms)}</td>
              <td>
                {m.active ? (
                  <span className="badge">active</span>
                ) : (
                  <button onClick={() => onActivate(m.id)}>Activate</button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Write `frontend/src/pages/Lab.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import LabMetrics from "../components/LabMetrics";
import {
  DEFAULT_TRAIN_FORM,
  LAB_FEATURES,
  activateModel,
  fetchModels,
  trainModels,
  type TrainForm,
  type TrainResponse,
} from "../lib/lab";
import type { LabModel } from "../lib/types";

const TIMEFRAMES = ["M1", "M5", "M15", "H1"];

export default function Lab() {
  const [form, setForm] = useState<TrainForm>(DEFAULT_TRAIN_FORM);
  const [models, setModels] = useState<LabModel[]>([]);
  const [result, setResult] = useState<TrainResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const { models } = await fetchModels(form.symbol, form.timeframe);
    setModels(models);
  }, [form.symbol, form.timeframe]);

  useEffect(() => { void reload(); }, [reload]);

  const num = (key: keyof TrainForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: Number(e.target.value) }));

  const toggle = (name: string) =>
    setForm((f) => ({
      ...f,
      features: f.features.includes(name)
        ? f.features.filter((x) => x !== name)
        : [...f.features, name],
    }));

  const onTrain = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await trainModels(form));
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lab">
      <h1>Lab</h1>
      <p className="muted">
        Models trained here predict. Every number below is out-of-sample and net
        of spread; a model is only interesting where it beats the baseline.
      </p>

      <section className="lab-form">
        <label>
          Symbol
          <input value={form.symbol}
                 onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
        </label>
        <label>
          Timeframe
          <select value={form.timeframe}
                  onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
            {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
          </select>
        </label>
        <label>Bars ahead (N)
          <input type="number" value={form.n_bars} onChange={num("n_bars")} /></label>
        <label>Risk (k × ATR)
          <input type="number" step="0.1" value={form.k_atr}
                 onChange={num("k_atr")} /></label>
        <label>Reward ratio
          <input type="number" step="0.1" value={form.rr} onChange={num("rr")} /></label>
        <label>Regime threshold
          <input type="number" step="0.05" value={form.er_threshold}
                 onChange={num("er_threshold")} /></label>
        <label>Folds
          <input type="number" value={form.n_folds} onChange={num("n_folds")} /></label>
        <label>Assumed spread (points)
          <input type="number" value={form.default_spread_points}
                 onChange={num("default_spread_points")} /></label>

        <fieldset className="lab-features">
          <legend>Features</legend>
          {LAB_FEATURES.map((name) => (
            <label key={name}>
              <input type="checkbox" checked={form.features.includes(name)}
                     onChange={() => toggle(name)} />
              {name}
            </label>
          ))}
        </fieldset>

        <button onClick={onTrain} disabled={busy || form.features.length === 0}>
          {busy ? "Training…" : "Train"}
        </button>
      </section>

      {error && <p className="error">{error}</p>}

      {result && Object.keys(result.dropped_features).length > 0 && (
        <p className="warn">
          Dropped {Object.entries(result.dropped_features)
            .map(([k, v]) => `${k} (${Math.round(v * 100)}% unknown)`)
            .join(", ")}
          {result.spread_assumed
            ? " — cost uses the assumed spread, not measured spread."
            : ""}
        </p>
      )}

      <LabMetrics models={models} onActivate={async (id) => {
        await activateModel(id);
        await reload();
      }} />
    </div>
  );
}
```

- [ ] **Step 5: Add the route and nav link**

In `frontend/src/App.tsx`, import `Lab` and add `<Route path="/lab" element={<Lab />} />` next to the other routes. In `frontend/src/components/AppShell.tsx`, add a `/lab` entry alongside the existing links, following whatever link component that file already uses.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/Lab.test.tsx && npx tsc --noEmit && npm run build`
Expected: PASS, 7 tests; tsc clean; build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Lab.tsx frontend/src/pages/Lab.test.tsx \
        frontend/src/components/LabMetrics.tsx frontend/src/App.tsx \
        frontend/src/components/AppShell.tsx
git commit -m "feat(lab): /lab page with training form and out-of-sample metrics"
```

---

### Task 11: Chart integration on `/lab` — regime shading and probability strip

**Files:**
- Create: `frontend/src/components/RegimeOverlay.tsx`
- Create: `frontend/src/lib/regimeBands.ts`
- Create: `frontend/src/lib/regimeBands.test.ts`
- Modify: `frontend/src/pages/Lab.tsx` (mount the chart and overlay)

**Interfaces:**
- Consumes: `fetchRegimes` from Task 9, the existing `CandleChart` component and `useChartData` hook.
- Produces:
  - `toBands(bars: LabBarScore[]): Band[]` where `Band = { from: number; to: number; regime: Regime }` — collapses consecutive same-regime bars into one band.
  - `<RegimeOverlay bands={Band[]} toX={(timeMsc) => number | null} height={number} />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/regimeBands.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { toBands } from "./regimeBands";
import type { LabBarScore } from "./types";

const bar = (time_msc: number, regime: string): LabBarScore =>
  ({ time_msc, regime, regime_proba: {}, p_tp_long: 0.5, p_tp_short: 0.5 } as LabBarScore);

describe("toBands", () => {
  it("collapses a run of one regime into a single band", () => {
    const bands = toBands([bar(0, "trend_up"), bar(60, "trend_up"), bar(120, "trend_up")]);
    expect(bands).toEqual([{ from: 0, to: 120, regime: "trend_up" }]);
  });

  it("splits where the regime changes", () => {
    const bands = toBands([bar(0, "range"), bar(60, "trend_up"), bar(120, "trend_up")]);
    expect(bands).toHaveLength(2);
    expect(bands[0]).toEqual({ from: 0, to: 60, regime: "range" });
    expect(bands[1]).toEqual({ from: 60, to: 120, regime: "trend_up" });
  });

  it("returns nothing for no bars", () => {
    expect(toBands([])).toEqual([]);
  });

  it("handles a single bar", () => {
    expect(toBands([bar(0, "range")])).toEqual([{ from: 0, to: 0, regime: "range" }]);
  });

  it("keeps bars in time order even if the input is not", () => {
    const bands = toBands([bar(120, "range"), bar(0, "range"), bar(60, "range")]);
    expect(bands).toEqual([{ from: 0, to: 120, regime: "range" }]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/regimeBands.test.ts`
Expected: FAIL — cannot resolve `./regimeBands`

- [ ] **Step 3: Write `frontend/src/lib/regimeBands.ts`**

```ts
import type { LabBarScore, Regime } from "./types";

export type Band = { from: number; to: number; regime: Regime };

/** Consecutive bars sharing a regime become one band, so the overlay draws a
 *  handful of rectangles instead of one per bar. */
export function toBands(bars: LabBarScore[]): Band[] {
  if (bars.length === 0) return [];
  const sorted = [...bars].sort((a, b) => a.time_msc - b.time_msc);
  const out: Band[] = [];
  let current: Band = {
    from: sorted[0].time_msc, to: sorted[0].time_msc, regime: sorted[0].regime,
  };
  for (const bar of sorted.slice(1)) {
    if (bar.regime === current.regime) {
      current.to = bar.time_msc;
    } else {
      out.push(current);
      current = { from: bar.time_msc, to: bar.time_msc, regime: bar.regime };
    }
  }
  out.push(current);
  return out;
}
```

- [ ] **Step 4: Write `frontend/src/components/RegimeOverlay.tsx`**

The overlay is a sibling of the chart div and projects time to x with the chart's own time scale, exactly the way `MeasureOverlay.tsx` already does. **Read `frontend/src/components/MeasureOverlay.tsx` first and copy its projection approach**; the Spec B lesson was that a frozen overlay must project by TIME, not by logical index, so it stays correct when bars are prepended.

```tsx
import { regimeColor } from "../lib/lab";
import type { Band } from "../lib/regimeBands";

export default function RegimeOverlay({
  bands, toX, height,
}: {
  bands: Band[];
  /** Time (epoch ms) -> pixel x, supplied by the chart's time scale. */
  toX: (timeMsc: number) => number | null;
  height: number;
}) {
  return (
    <svg className="regime-overlay" width="100%" height={height}
         style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {bands.map((band) => {
        const x1 = toX(band.from);
        const x2 = toX(band.to);
        if (x1 === null || x2 === null) return null;
        return (
          <rect key={`${band.from}-${band.regime}`} x={x1} y={0}
                width={Math.max(1, x2 - x1)} height={height}
                fill={regimeColor(band.regime)} />
        );
      })}
    </svg>
  );
}
```

- [ ] **Step 5: Mount the chart on `/lab`**

In `Lab.tsx`, add below the metrics table: a `CandleChart` for the current `symbol`/`timeframe` driven by `useChartData` exactly as `Chart.tsx` does, wrapped in a `position: relative` container with `<RegimeOverlay>` as its sibling. Fetch scores with `fetchRegimes(symbol, timeframe, fromMs, toMs)` for the visible window after each successful train and on symbol/timeframe change, and feed `toBands(score.bars)` to the overlay. Beneath the chart, render the probability strip as a simple `<svg>` bar per scored bar, height proportional to `p_tp_long`, using the same `toX` projection. When `score.status !== "ok"`, render the status text instead of an empty chart area.

- [ ] **Step 6: Run tests and build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: PASS, all suites; tsc clean; build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/regimeBands.ts frontend/src/lib/regimeBands.test.ts \
        frontend/src/components/RegimeOverlay.tsx frontend/src/pages/Lab.tsx
git commit -m "feat(lab): regime shading and probability strip on the lab chart"
```

---

### Task 12: `/live` badge and regime shading

**Files:**
- Create: `frontend/src/components/LabBadge.tsx`
- Create: `frontend/src/components/LabBadge.test.tsx`
- Create: `frontend/src/hooks/useLabScore.ts`
- Modify: `frontend/src/pages/Live.tsx`

**Interfaces:**
- Consumes: `fetchScore`, `formatAge` from Task 9.
- Produces:
  - `useLabScore(symbol, timeframe, timeframeMs) -> { score: LabScore | null; loading: boolean }` — refetches when a bar closes, not on a timer faster than the timeframe.
  - `<LabBadge score={LabScore | null} />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/LabBadge.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LabBadge from "./LabBadge";
import type { LabScore } from "../lib/types";

const score = (over: Partial<LabScore> = {}): LabScore =>
  ({
    symbol: "XAUUSDc", timeframe: "M15", status: "ok",
    model_age_ms: 3 * 86_400_000, expectancy_r: 0.08, pooled: false,
    bars: [{ time_msc: 1, regime: "trend_up",
             regime_proba: { trend_up: 0.7, trend_down: 0.1, range: 0.2 },
             p_tp_long: 0.62, p_tp_short: 0.31 }],
    ...over,
  } as LabScore);

describe("LabBadge", () => {
  it("shows the regime and both probabilities", () => {
    render(<LabBadge score={score()} />);
    expect(screen.getByText(/trend up/i)).toBeInTheDocument();
    expect(screen.getByText(/62%/)).toBeInTheDocument();
    expect(screen.getByText(/31%/)).toBeInTheDocument();
  });

  it("always shows model age and out-of-sample expectancy next to them", () => {
    render(<LabBadge score={score()} />);
    expect(screen.getByText(/3d ago/)).toBeInTheDocument();
    expect(screen.getByText(/\+0\.08R/)).toBeInTheDocument();
  });

  it("marks a model older than 30 days as stale", () => {
    render(<LabBadge score={score({ model_age_ms: 40 * 86_400_000 })} />);
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
  });

  it("shows no probability at all when there is no model", () => {
    render(<LabBadge score={score({ status: "no_model", bars: [] })} />);
    expect(screen.getByText(/no model/i)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("asks for a retrain when the artifact is gone", () => {
    render(<LabBadge score={score({ status: "artifact_missing", bars: [] })} />);
    expect(screen.getByText(/retrain/i)).toBeInTheDocument();
  });

  it("says pooled when the timing model is not regime-specific", () => {
    render(<LabBadge score={score({ pooled: true })} />);
    expect(screen.getByText(/pooled/i)).toBeInTheDocument();
  });

  it("renders nothing but a placeholder while loading", () => {
    render(<LabBadge score={null} />);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/LabBadge.test.tsx`
Expected: FAIL — cannot resolve `./LabBadge`

- [ ] **Step 3: Write `frontend/src/components/LabBadge.tsx`**

```tsx
import { formatAge } from "../lib/lab";
import type { LabScore } from "../lib/types";

const STALE_MS = 30 * 86_400_000;

const LABEL: Record<string, string> = {
  trend_up: "Trend up",
  trend_down: "Trend down",
  range: "Range",
};

const STATUS_TEXT: Record<string, string> = {
  no_model: "No model trained for this symbol and timeframe.",
  artifact_missing: "Model file missing — retrain from /lab.",
  no_bars: "Not enough bars to score.",
};

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export default function LabBadge({ score }: { score: LabScore | null }) {
  if (!score) return <div className="lab-badge muted">Lab —</div>;
  if (score.status !== "ok" || score.bars.length === 0) {
    return <div className="lab-badge muted">{STATUS_TEXT[score.status] ?? "Lab —"}</div>;
  }

  const latest = score.bars[score.bars.length - 1];
  const stale = (score.model_age_ms ?? 0) > STALE_MS;
  const expectancy = score.expectancy_r;

  return (
    <div className={`lab-badge${stale ? " stale" : ""}`}>
      <div className="lab-badge-regime">{LABEL[latest.regime] ?? latest.regime}</div>
      <div className="lab-badge-probs">
        <span>long {pct(latest.p_tp_long)}</span>
        <span>short {pct(latest.p_tp_short)}</span>
      </div>
      {/* Age and out-of-sample expectancy are not optional decoration: a
          probability rendered beside an order button without them is a
          recommendation, which this tool does not make (CLAUDE.md rule 9). */}
      <div className="lab-badge-provenance">
        <span>{formatAge(score.model_age_ms)}{stale ? " · stale" : ""}</span>
        <span>
          out-of-sample{" "}
          {expectancy === null || expectancy === undefined
            ? "—"
            : `${expectancy >= 0 ? "+" : ""}${expectancy.toFixed(2)}R`}
        </span>
        {score.pooled && <span>pooled model</span>}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write `frontend/src/hooks/useLabScore.ts`**

```ts
import { useEffect, useState } from "react";
import { fetchScore } from "../lib/lab";
import type { LabScore } from "../lib/types";

/** Refetches once per closed bar. The models were trained on closed bars, so an
 *  intrabar score would be a different and untested quantity. */
export function useLabScore(symbol: string, timeframe: string, timeframeMs: number) {
  const [score, setScore] = useState<LabScore | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;

    const load = async () => {
      try {
        const next = await fetchScore(symbol, timeframe);
        if (alive) setScore(next);
      } finally {
        if (alive) setLoading(false);
      }
      if (!alive) return;
      const msToNextBar = timeframeMs - (Date.now() % timeframeMs) + 1_000;
      timer = window.setTimeout(load, msToNextBar);
    };

    void load();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [symbol, timeframe, timeframeMs]);

  return { score, loading };
}
```

- [ ] **Step 5: Mount on `/live`**

In `frontend/src/pages/Live.tsx`, call `useLabScore` for the symbol and timeframe the live chart is showing and render `<LabBadge score={score} />` in the existing panel column. Add `<RegimeOverlay>` to the live chart container in the same sibling-of-the-chart-div arrangement Task 11 established, fed from `score.bars`. Do not add any marker, arrow, or highlight to the order controls themselves.

- [ ] **Step 6: Run tests and build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: PASS, all suites; tsc clean; build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/LabBadge.tsx frontend/src/components/LabBadge.test.tsx \
        frontend/src/hooks/useLabScore.ts frontend/src/pages/Live.tsx
git commit -m "feat(lab): live regime badge with model age and out-of-sample expectancy"
```

---

### Task 13: Docs, milestone, and full verification

**Files:**
- Modify: `CLAUDE.md` (Milestones section, Layout section)
- Modify: `docs/HANDOFF.md` (§ CURRENT STATE)
- Create: `docs/lab-models.md`

**Interfaces:**
- Consumes: everything.
- Produces: the record a future session needs to not re-derive this.

- [ ] **Step 1: Write `docs/lab-models.md`**

Cover, with the actual values from the implementation: what the two stages are; the exact label definitions including the stop-first rule and the `open[t+1]` entry; the feature list and what each one means; why the walk-forward purge gap exists; how to read the metrics table (expectancy against baseline first, calibration second, accuracy last); what each `status` value on `/api/lab/score` means and what to do about it; and how to reproduce a stored model from its `config_json`.

Open the document with the honest framing: these models predict, they are the only part of the tool that does, and their output is bound by the three conditions in rule 9.

- [ ] **Step 2: Update `CLAUDE.md`**

Add to Layout:

```
  lab/       features.py | labels.py | evaluate.py | train.py | store.py | score.py
```

Add to Milestones:

```
· M10 lab: regime + entry-timing models on candle data (`/lab`, badge on `/live`)
```

Add to "Read before you edit":

```
- Touching anything in `lab/` → read `docs/lab-models.md` first. The label
  definitions and the purge gap are the parts that are easy to break silently.
```

- [ ] **Step 3: Update `docs/HANDOFF.md`**

In § CURRENT STATE, record: the lab shipped, migration 010 / `SCHEMA_VERSION = 10`, the two new dependencies, that rule 9 now scopes prediction to `lab/`, and that no model has been trained against real data yet — that is the first thing a human should do.

- [ ] **Step 4: Run every gate**

```bash
uv run pytest
cd frontend && npx vitest run && npx tsc --noEmit && npm run build && cd ..
uv run journal rebuild
```

Expected: pytest all green with the new lab tests included; vitest all green; tsc silent; build succeeds; rebuild succeeds. **Paste the actual output** — CLAUDE.md's definition of done requires the real pytest output, not a claim.

- [ ] **Step 5: Train one real model as a smoke test**

```bash
uv run journal serve    # in one shell
```

Then from `/lab` in the browser: symbol `XAUUSDc`, timeframe `H1`, defaults otherwise, press Train. Confirm the run completes, the metrics table fills, `n` appears on every figure, and the expectancy sits beside its baseline. Record the numbers in `docs/HANDOFF.md` whatever they are — including, and especially, if the model loses to the baseline.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/HANDOFF.md docs/lab-models.md
git commit -m "docs: lab model reference, M10 milestone, handoff state"
```

---

## Verification checklist

Before calling this branch done:

- [ ] `uv run pytest` — all green, output pasted
- [ ] `cd frontend && npx vitest run` — all green
- [ ] `npx tsc --noEmit` — silent
- [ ] `npm run build` — succeeds, `dist/` refreshed
- [ ] `uv run journal rebuild` — succeeds, `lab_models` rows survive
- [ ] `/lab` trains a real XAUUSDc H1 model end to end in the browser
- [ ] `/live` shows the badge with regime, both probabilities, model age, and
      out-of-sample expectancy — and no marker anywhere near the order buttons
- [ ] CLAUDE.md rule 9 reads as the new scoped version, and no code contradicts it
