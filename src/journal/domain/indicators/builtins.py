"""The indicator function set. Pure pandas, every function causal: the value at
bar t reads only bars <= t (rolling/ewm/cumsum/shift(+n) only, never
shift(-n)). `tests/test_indicator_builtins.py` proves it per function.

Every function takes the bar frame first (most ignore it) so the engine calls
them uniformly. Logical values are three-valued floats — 1.0 true, 0.0 false,
NaN unknown — so an unknown input never reads as "false" (rule 4).

`cost(consts, tf_ms)` is the warm-up a function needs, in bars, on top of its
inputs' own: `lang.lookback` composes it. ewm-based smoothing never fully
forgets its seed, so it gets enough windows for the seed's weight to fall to
≈ e^-8: 4n for an EMA (alpha 2/(n+1)), 8n for Wilder's RMA (alpha 1/n)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Literal, TypeAlias

import numpy as np
import pandas as pd

Frame: TypeAlias = pd.DataFrame | None
DAY_MS = 86_400_000


# --- three-valued logic -----------------------------------------------------

def _known(*xs: pd.Series) -> pd.Series:
    out = pd.Series(True, index=xs[0].index)
    for x in xs:
        out &= x.notna()
    return out


def tri(cond: pd.Series, *inputs: pd.Series) -> pd.Series:
    """A boolean Series -> 1.0/0.0, NaN wherever any of `inputs` is unknown."""
    return cond.astype("float64").where(_known(*inputs))


def logical_and(a: pd.Series, b: pd.Series) -> pd.Series:
    """Kleene AND: false if either is false, else unknown if either is unknown."""
    out = pd.Series(np.nan, index=a.index)
    out[(a == 1) & (b == 1)] = 1.0
    out[(a == 0) | (b == 0)] = 0.0
    return out


def logical_or(a: pd.Series, b: pd.Series) -> pd.Series:
    out = pd.Series(np.nan, index=a.index)
    out[(a == 0) & (b == 0)] = 0.0
    out[(a == 1) | (b == 1)] = 1.0
    return out


def logical_not(a: pd.Series) -> pd.Series:
    return (1.0 - (a != 0).astype("float64")).where(a.notna())


# --- moving averages and dispersion ----------------------------------------

def sma(_: Frame, x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(_: Frame, x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(_: Frame, x: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (alpha = 1/n) — what RSI/ATR/ADX are defined on."""
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def wma(_: Frame, x: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype="float64")
    return x.rolling(n, min_periods=n).apply(lambda v: float(np.dot(v, w) / w.sum()), raw=True)


def stdev(_: Frame, x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).std(ddof=0)


def highest(_: Frame, x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).max()


def lowest(_: Frame, x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).min()


def change(_: Frame, x: pd.Series, n: int) -> pd.Series:
    return x - x.shift(n)


def roc(_: Frame, x: pd.Series, n: int) -> pd.Series:
    prev = x.shift(n)
    return 100.0 * (x / prev.where(prev != 0) - 1.0)


# --- price-structure indicators ----------------------------------------------

def tr(f: pd.DataFrame) -> pd.Series:
    """True range. The first bar has no previous close: high - low."""
    prev = f["close"].shift(1)
    out = pd.concat([f["high"] - f["low"], (f["high"] - prev).abs(),
                     (f["low"] - prev).abs()], axis=1).max(axis=1)
    return out.where(f["high"].notna() & f["low"].notna())


def atr(f: pd.DataFrame, n: int) -> pd.Series:
    return rma(f, tr(f), n)


def rsi(_: Frame, x: pd.Series, n: int) -> pd.Series:
    d = x.diff()
    up = rma(None, d.clip(lower=0), n)
    dn = rma(None, (-d).clip(lower=0), n)
    out = 100.0 - 100.0 / (1.0 + up / dn.where(dn != 0))
    out = out.where(dn != 0, 100.0)            # no losses at all -> 100
    out = out.where(~((up == 0) & (dn == 0)), 50.0)
    return out.where(up.notna() & dn.notna())


def macd_line(f: Frame, x: pd.Series, fast: int, slow: int) -> pd.Series:
    return ema(f, x, fast) - ema(f, x, slow)


def macd_signal(f: Frame, x: pd.Series, fast: int, slow: int, sig: int) -> pd.Series:
    return ema(f, macd_line(f, x, fast, slow), sig)


def macd_hist(f: Frame, x: pd.Series, fast: int, slow: int, sig: int) -> pd.Series:
    return macd_line(f, x, fast, slow) - macd_signal(f, x, fast, slow, sig)


def bb_mid(f: Frame, x: pd.Series, n: int, k: float) -> pd.Series:
    return sma(f, x, n)


def bb_upper(f: Frame, x: pd.Series, n: int, k: float) -> pd.Series:
    return sma(f, x, n) + k * stdev(f, x, n)


def bb_lower(f: Frame, x: pd.Series, n: int, k: float) -> pd.Series:
    return sma(f, x, n) - k * stdev(f, x, n)


def stoch_k(f: pd.DataFrame, n: int, smooth: int) -> pd.Series:
    lo = lowest(f, f["low"], n)
    span = highest(f, f["high"], n) - lo
    raw = 100.0 * (f["close"] - lo) / span.where(span != 0)
    return sma(f, raw, smooth)


def stoch_d(f: pd.DataFrame, n: int, smooth: int, d: int) -> pd.Series:
    return sma(f, stoch_k(f, n, smooth), d)


def vwap(f: pd.DataFrame) -> pd.Series:
    """Volume-weighted typical price, reset at each UTC day (the broker clock is
    UTC on this account). Unknown from the first bar of a day whose volume is
    unknown until that day ends — a partial sum would be a wrong number."""
    day = pd.Series(np.asarray(f.index) // DAY_MS, index=f.index)
    typical = (f["high"] + f["low"] + f["close"]) / 3.0
    vol = f["volume"]
    bad = vol.isna().astype("int64").groupby(day).cummax().astype(bool)
    pv = (typical * vol).fillna(0.0).groupby(day).cumsum()
    cv = vol.fillna(0.0).groupby(day).cumsum()
    return (pv / cv.where(cv > 0)).where(~bad)


def donchian_upper(f: pd.DataFrame, n: int) -> pd.Series:
    return highest(f, f["high"], n)


def donchian_lower(f: pd.DataFrame, n: int) -> pd.Series:
    return lowest(f, f["low"], n)


def adx(f: pd.DataFrame, n: int) -> pd.Series:
    up = f["high"].diff()
    dn = -f["low"].diff()
    known = up.notna() & dn.notna()
    plus_dm = up.where((up > dn) & (up > 0), 0.0).where(known)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0).where(known)
    trr = rma(f, tr(f).where(known), n)
    plus_di = 100.0 * rma(f, plus_dm, n) / trr.where(trr != 0)
    minus_di = 100.0 * rma(f, minus_dm, n) / trr.where(trr != 0)
    total = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / total.where(total != 0)
    return rma(f, dx, n)


# --- signals and math -------------------------------------------------------

def crossover(_: Frame, a: pd.Series, b: pd.Series) -> pd.Series:
    pa, pb = a.shift(1), b.shift(1)
    return tri((a > b) & (pa <= pb), a, b, pa, pb)


def crossunder(_: Frame, a: pd.Series, b: pd.Series) -> pd.Series:
    pa, pb = a.shift(1), b.shift(1)
    return tri((a < b) & (pa >= pb), a, b, pa, pb)


def abs_(_: Frame, x: pd.Series) -> pd.Series:
    return x.abs()


def log(_: Frame, x: pd.Series) -> pd.Series:
    return np.log(x.where(x > 0))


def sqrt(_: Frame, x: pd.Series) -> pd.Series:
    return np.sqrt(x.where(x >= 0))


def min_(_: Frame, a: pd.Series, b: pd.Series) -> pd.Series:
    return pd.Series(np.minimum(a.to_numpy(), b.to_numpy()), index=a.index)


def max_(_: Frame, a: pd.Series, b: pd.Series) -> pd.Series:
    return pd.Series(np.maximum(a.to_numpy(), b.to_numpy()), index=a.index)


def where(_: Frame, c: pd.Series, a: pd.Series, b: pd.Series) -> pd.Series:
    return a.where(c != 0, b).where(c.notna())


def nz(_: Frame, x: pd.Series, v: float) -> pd.Series:
    return x.fillna(v)


# --- registry ---------------------------------------------------------------

Kind = Literal["series", "int", "float"]


@dataclass(frozen=True)
class Param:
    name: str
    kind: Kind
    default: float | None = None


@dataclass(frozen=True)
class Builtin:
    fn: Callable[..., pd.Series]
    params: tuple[Param, ...]
    cost: Callable[[dict[str, float], int], int]


def _x() -> Param:
    return Param("x", "series")


def _n(default: int | None = None) -> Param:
    return Param("n", "int", default)


def _win(c: dict[str, float], _tf: int) -> int:
    return int(c["n"])


def _ema_cost(c: dict[str, float], _tf: int) -> int:
    return 4 * int(c["n"])


def _rma_cost(c: dict[str, float], _tf: int) -> int:
    return 8 * int(c["n"])


def _zero(_c: dict[str, float], _tf: int) -> int:
    return 0


_MACD = (_x(), Param("fast", "int", 12), Param("slow", "int", 26))
_BB = (_x(), _n(20), Param("k", "float", 2.0))

REGISTRY: dict[str, Builtin] = {
    "sma": Builtin(sma, (_x(), _n()), _win),
    "ema": Builtin(ema, (_x(), _n()), _ema_cost),
    "rma": Builtin(rma, (_x(), _n()), _rma_cost),
    "wma": Builtin(wma, (_x(), _n()), _win),
    "stdev": Builtin(stdev, (_x(), _n()), _win),
    "highest": Builtin(highest, (_x(), _n()), _win),
    "lowest": Builtin(lowest, (_x(), _n()), _win),
    "change": Builtin(change, (_x(), _n(1)), _win),
    "roc": Builtin(roc, (_x(), _n(1)), _win),
    "tr": Builtin(lambda f: tr(f), (), lambda c, t: 1),
    "atr": Builtin(atr, (_n(14),), lambda c, t: 1 + _rma_cost(c, t)),
    "rsi": Builtin(rsi, (_x(), _n(14)), lambda c, t: 1 + _rma_cost(c, t)),
    "macd_line": Builtin(macd_line, _MACD, lambda c, t: 4 * int(c["slow"])),
    "macd_signal": Builtin(macd_signal, (*_MACD, Param("sig", "int", 9)),
                           lambda c, t: 4 * int(c["slow"]) + 4 * int(c["sig"])),
    "macd_hist": Builtin(macd_hist, (*_MACD, Param("sig", "int", 9)),
                         lambda c, t: 4 * int(c["slow"]) + 4 * int(c["sig"])),
    "bb_mid": Builtin(bb_mid, _BB, _win),
    "bb_upper": Builtin(bb_upper, _BB, _win),
    "bb_lower": Builtin(bb_lower, _BB, _win),
    "stoch_k": Builtin(stoch_k, (_n(14), Param("smooth", "int", 3)),
                       lambda c, t: int(c["n"]) + int(c["smooth"])),
    "stoch_d": Builtin(stoch_d, (_n(14), Param("smooth", "int", 3), Param("d", "int", 3)),
                       lambda c, t: int(c["n"]) + int(c["smooth"]) + int(c["d"])),
    "vwap": Builtin(lambda f: vwap(f), (), lambda c, t: math.ceil(DAY_MS / t)),
    "donchian_upper": Builtin(donchian_upper, (_n(20),), _win),
    "donchian_lower": Builtin(donchian_lower, (_n(20),), _win),
    "adx": Builtin(adx, (_n(14),), lambda c, t: 1 + 2 * _rma_cost(c, t)),
    "crossover": Builtin(crossover, (Param("a", "series"), Param("b", "series")),
                         lambda c, t: 1),
    "crossunder": Builtin(crossunder, (Param("a", "series"), Param("b", "series")),
                          lambda c, t: 1),
    "abs": Builtin(abs_, (_x(),), _zero),
    "log": Builtin(log, (_x(),), _zero),
    "sqrt": Builtin(sqrt, (_x(),), _zero),
    "min": Builtin(min_, (Param("a", "series"), Param("b", "series")), _zero),
    "max": Builtin(max_, (Param("a", "series"), Param("b", "series")), _zero),
    "where": Builtin(where, (Param("c", "series"), Param("a", "series"),
                             Param("b", "series")), _zero),
    "nz": Builtin(nz, (_x(), Param("v", "float", 0.0)), _zero),
}
