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
