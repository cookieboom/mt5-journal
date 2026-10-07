"""Strategy Tester service: run a script's signals over stored candles and
return the simulated trades + IS/OOS report (spec 2026-10-07-indicators-
strategy-tester §1.4). Pure DB, never the bridge. Nothing is stored — the
result is recomputed on demand (rule 2).

No `session_id`: the tester is hidden in replay, where a backtest over the
session's range would read past the cursor."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from typing import Any

from ..adapter.base import TIMEFRAMES
from ..domain.indicators.backtest import Settings, report, simulate
from ..domain.indicators.builtins import REGISTRY
from ..domain.indicators.engine import evaluate
from ..domain.indicators.lang import lookback, parse, resolve_consts
from ..domain.resample import timeframe_ms
from ..store.db import now_ms
from .indicators import _source_of, load_frames

MAX_BACKTEST_BARS = 200_000


def _point(conn: sqlite3.Connection, symbol: str) -> float:
    row = conn.execute("SELECT point FROM symbol_specs WHERE symbol = ?", (symbol,)).fetchone()
    if row is None or not row["point"]:
        raise ValueError(f"no symbol specs for {symbol} (run `journal sync`)")
    return float(row["point"])


def run(conn: sqlite3.Connection, *, inputs: dict[str, Any], symbol: str, timeframe: str,
        from_ms: int, to_ms: int, exits: dict[str, Any], split: float = 0.7,
        spread_fallback: float | None = None, script: str | None = None,
        source: str | None = None) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}")
    if source is None:
        if script is None:
            raise ValueError("give a script id or a source")
        source = _source_of(conn, script)
    point = _point(conn, symbol)
    tf_ms = timeframe_ms(timeframe)
    p = parse(source)
    if not p.signals:
        raise ValueError("the script has no signal() to test")
    consts = resolve_consts(p, inputs)
    settings = Settings(**exits, spread_fallback=spread_fallback)
    lb = lookback(p, consts, tf_ms)
    if settings.sl == "atr":   # the form's ATR stop needs its own warm-up
        lb = max(lb, REGISTRY["atr"].cost({"n": settings.atr_len}, tf_ms))

    fr = load_frames(conn, p, consts, symbol=symbol, timeframe=timeframe, from_ms=from_ms,
                     to_ms=to_ms, lb=lb)
    n = int((fr.f.index >= from_ms).sum())
    if n > MAX_BACKTEST_BARS:
        raise ValueError(f"range holds {n} bars; the tester's limit is {MAX_BACKTEST_BARS}")
    r = evaluate(p, fr.f, inputs, tf_ms, htf=fr.htf)
    # Nothing is warm → no signal is trusted, so no trade (an empty, pending run).
    start = max(from_ms, fr.warm_from_msc) if fr.warm_from_msc is not None else to_ms + 1
    sim = simulate(fr.f, r, p, consts, settings, point, start_msc=start)
    split_msc = from_ms + int(split * (to_ms - from_ms))
    digest = hashlib.sha256(
        (source + json.dumps(inputs, sort_keys=True)).encode()).hexdigest()
    return {
        "computed_ms": now_ms(), "source_hash": digest, "split_msc": split_msc,
        **report(sim, split_msc),
        "trades": [asdict(t) for t in sim.trades],
        "counts": sim.counts, "warm_from_msc": fr.warm_from_msc, "pending": fr.pending,
    }
