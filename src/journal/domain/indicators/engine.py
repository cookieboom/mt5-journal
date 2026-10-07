"""Evaluate a checked `lang.Program` over a bar frame (`frame.to_frame`).

Every value is a float Series aligned to the frame. Booleans are three-valued
(1.0 / 0.0 / NaN, see `builtins`). Arithmetic that leaves the reals
(x/0, overflow) yields NaN, never ±inf — an unknown, not a number to plot."""
from __future__ import annotations

import ast
import operator
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import builtins as B
from .lang import (
    Program, ScriptError, call_consts, check_higher, const_value, error_at, resolve_consts, tf_of,
)

BUDGET_S = 2.0

_ARITH: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_CMP: dict[type, Callable[[Any, Any], Any]] = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}


@dataclass
class Result:
    plots: list[pd.Series]
    hlines: list[float]
    signals: list[pd.Series]
    # Per signal; None where the script gave no stop=/target=.
    stops: list[pd.Series | None]
    targets: list[pd.Series | None]
    exits: list[pd.Series]


def base_series(f: pd.DataFrame) -> dict[str, pd.Series]:
    o, h, lo, c = f["open"], f["high"], f["low"], f["close"]
    return {
        "open": o, "high": h, "low": lo, "close": c,
        "volume": f["volume"], "spread": f["spread"],
        "hl2": (h + lo) / 2.0, "hlc3": (h + lo + c) / 3.0, "ohlc4": (o + h + lo + c) / 4.0,
    }


def map_closed(values: pd.Series, htf_ms: int, index: pd.Index, tf_ms: int,
               forming_msc: int | None = None) -> pd.Series:
    """HTF `values` (indexed by bar open T) onto chart bars `index` (open t).
    Chart bar [t, t+L) gets the latest HTF bar with T+H <= t+L — closed by the
    time the chart bar closed; the forming chart bar uses t, since its close
    lies in the future. A missing HTF bar carries the previous one forward,
    unless more than 2 HTF buckets that HAVE chart bars went missing since:
    then it is stale, NaN. A market closure has no chart bars, so it carries."""
    t = index.to_numpy(dtype="int64")
    out = np.full(len(t), np.nan)
    if len(values) == 0 or len(t) == 0:
        return pd.Series(out, index=index, dtype="float64")
    ref = t + tf_ms
    if forming_msc is not None:
        ref[t == forming_msc] = forming_msc
    opens = values.index.to_numpy(dtype="int64")
    k = np.searchsorted(opens + htf_ms, ref, side="right") - 1
    hit = k >= 0
    out[hit] = values.to_numpy(dtype="float64")[k[hit]]
    buckets = np.unique(t - t % htf_ms)
    missing = (np.searchsorted(buckets, ref - htf_ms, side="right")
               - np.searchsorted(buckets, opens[np.maximum(k, 0)], side="right"))
    out[hit & (missing > 2)] = np.nan
    return pd.Series(out, index=index, dtype="float64")


def evaluate(p: Program, f: pd.DataFrame, inputs: dict[str, Any], tf_ms: int,
             htf: dict[str, pd.DataFrame] | None = None,
             forming_msc: int | None = None) -> Result:
    """`htf`: bar frames per tf() timeframe (missing = no bars, all NaN).
    `forming_msc`: open time of the chart's forming bar, if any (§1.2)."""
    consts = resolve_consts(p, inputs)
    env: dict[str, pd.Series] = base_series(f)
    deadline = time.monotonic() + BUDGET_S
    cache: dict[tuple[str, str], pd.Series] = {}

    def tf_call(node: ast.Call) -> pd.Series:
        h = check_higher(node, tf_ms)
        x = tf_of(node)
        key = (x, ast.dump(node.args[1]))
        if key not in cache:
            hf = (htf or {}).get(x)
            if hf is None:
                hf = f.iloc[:0]
            inner = _evaluator(hf, base_series(hf), consts, tf_call)(node.args[1])
            cache[key] = map_closed(inner, h, f.index, tf_ms, forming_msc)
        return cache[key]

    ev = _evaluator(f, env, consts, tf_call)
    index = f.index

    def series(v: pd.Series | float | bool) -> pd.Series:
        if isinstance(v, pd.Series):
            return v
        return pd.Series(float(v), index=index, dtype="float64")

    plots: list[pd.Series] = [series(np.nan)] * len(p.plots)
    signals: list[pd.Series] = [series(np.nan)] * len(p.signals)
    stops: list[pd.Series | None] = [None] * len(p.signals)
    targets: list[pd.Series | None] = [None] * len(p.signals)
    exits: list[pd.Series] = [series(np.nan)] * len(p.exits)
    for step in p.steps:
        if time.monotonic() > deadline:
            raise error_at(step.expr, f"evaluation exceeded the {BUDGET_S:g} s budget")
        v = ev(step.expr)
        if step.kind == "assign":
            assert isinstance(step.target, str)
            env[step.target] = v
        else:
            assert isinstance(step.target, int)
            if step.kind == "plot":
                plots[step.target] = v
            elif step.kind == "stop":
                stops[step.target] = series(v)
            elif step.kind == "target":
                targets[step.target] = series(v)
            elif step.kind == "exit":
                exits[step.target] = B.tri(v != 0, v)
            else:
                signals[step.target] = B.tri(v != 0, v)
    hlines = [float(const_value(h.value, consts)) for h in p.hlines]
    return Result(plots, hlines, signals, stops, targets, exits)


def _evaluator(f: pd.DataFrame, env: dict[str, pd.Series], consts: dict[str, float | bool],
               tf_call: Callable[[ast.Call], pd.Series]) -> Callable[[ast.expr], pd.Series]:
    """`ev(node)` over frame `f`; `env` holds base series and assigned names.
    tf() is handed back to the caller — it alone knows the other frames."""
    index = f.index

    def series(v: pd.Series | float | bool) -> pd.Series:
        if isinstance(v, pd.Series):
            return v
        return pd.Series(float(v), index=index, dtype="float64")

    def clean(s: pd.Series) -> pd.Series:
        return s.astype("float64").replace([np.inf, -np.inf], np.nan)

    def ev(node: ast.expr) -> pd.Series:
        if isinstance(node, ast.Constant):
            return series(node.value)
        if isinstance(node, ast.Name):
            return env[node.id] if node.id in env else series(consts[node.id])
        if isinstance(node, ast.BinOp):
            with np.errstate(all="ignore"):
                return clean(_ARITH[type(node.op)](ev(node.left), ev(node.right)))
        if isinstance(node, ast.UnaryOp):
            x = ev(node.operand)
            if isinstance(node.op, ast.Not):
                return B.logical_not(x)
            return -x if isinstance(node.op, ast.USub) else x
        if isinstance(node, ast.BoolOp):
            join = B.logical_and if isinstance(node.op, ast.And) else B.logical_or
            out = ev(node.values[0])
            for v in node.values[1:]:
                out = join(out, ev(v))
            return out
        if isinstance(node, ast.Compare):
            left, out = ev(node.left), None
            for op, comp in zip(node.ops, node.comparators, strict=True):
                right = ev(comp)
                part = B.tri(_CMP[type(op)](left, right), left, right)
                out = part if out is None else B.logical_and(out, part)
                left = right
            assert out is not None
            return out
        if isinstance(node, ast.Subscript):
            n = node.slice
            assert isinstance(n, ast.Constant) and type(n.value) is int  # lang guarantees it
            return ev(node.value).shift(n.value)
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        if node.func.id == "tf":
            return tf_call(node)
        b = B.REGISTRY[node.func.id]
        c = call_consts(node, consts)
        args: list[Any] = []
        for i, prm in enumerate(b.params):
            if prm.kind == "series":
                args.append(ev(node.args[i]))
            else:
                args.append(int(c[prm.name]) if prm.kind == "int" else c[prm.name])
        with np.errstate(all="ignore"):
            return clean(b.fn(f, *args))

    return ev


__all__ = ["Result", "ScriptError", "evaluate", "base_series", "map_closed"]
