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
