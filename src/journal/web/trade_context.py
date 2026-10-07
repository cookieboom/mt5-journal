"""Trade context: my closed trades split by whether the active script's
same-side `signal()` fired in the `window` closed bars before each one (spec
2026-10-08-indicators-trade-context §2 A). Pure DB, never the bridge, nothing
stored (rule 2). Descriptive of past trades — no verdict (rule 9).

Real trades are §8-gated through `analytics/report.bucket_stat`, the one
definition of a win and of the gate. Replay and paper are simulated, so they
are ungated (`sim_stats`, CLAUDE.md's replay exception) and never mixed with
real. Study sessions are excluded, as in the training summary."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

import numpy as np

from ..adapter.base import TIMEFRAMES
from ..analytics.report import bucket_stat
from ..domain import sim_stats
from ..domain.indicators.context import agreement
from ..domain.indicators.engine import evaluate
from ..domain.indicators.lang import lookback, parse, resolve_consts
from ..domain.resample import timeframe_ms
from ..store import candles_store as cs
from ..store.db import now_ms, one_account_login
from . import backtest
from .indicators import _source_of, _warm_bars, load_frames

# Each source: closed rows with the moment of decision as `ref`. Real trades
# have no decision time; the fill (`open_time_msc`) is the closest known.
_REAL = ("SELECT direction, open_time_msc AS ref, net_profit, r_multiple FROM trades "
         "WHERE account_login = ? AND symbol = ? AND status = 'closed' "
         "AND net_profit IS NOT NULL")
_REPLAY = ("SELECT p.direction, p.decision_msc AS ref, p.net_profit, p.r_multiple, "
           "p.mae_r, p.mfe_r FROM training_positions p "
           "JOIN training_sessions s ON s.id = p.session_id "
           "WHERE s.symbol = ? AND s.origin = 'blind' AND p.status = 'closed' "
           "AND p.net_profit IS NOT NULL")
_PAPER = ("SELECT direction, requested_msc AS ref, net_profit, r_multiple, mae_r, mfe_r "
          "FROM paper_positions WHERE symbol = ? AND status = 'closed' "
          "AND net_profit IS NOT NULL")


def _rows(conn: sqlite3.Connection, symbol: str) -> dict[str, list[sqlite3.Row]]:
    if conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is None:
        real: list[sqlite3.Row] = []
    else:
        try:
            login = one_account_login(conn)
        except RuntimeError as e:          # several accounts: refuse, never guess
            raise ValueError(str(e)) from e
        real = conn.execute(_REAL, (login, symbol)).fetchall()
    return {"real": real,
            "replay": conn.execute(_REPLAY, (symbol,)).fetchall(),
            "paper": conn.execute(_PAPER, (symbol,)).fetchall()}


def _real_cell(rows: list[sqlite3.Row]) -> dict[str, Any]:
    b = bucket_stat("", rows)
    return {"n": b.n, "n_r": b.n_with_r, "win_rate": b.win_rate, "avg_r": b.avg_r,
            "total_r": b.avg_r * b.n_with_r if b.avg_r is not None else None, "gated": True}


def _sim_cell(rows: list[sqlite3.Row]) -> dict[str, Any]:
    s = sim_stats.summary([dict(r) for r in rows])
    return {"n": s["n"], "n_r": sum(1 for r in rows if r["r_multiple"] is not None),
            "win_rate": s["win_rate"], "avg_r": s["avg_r"],
            "total_r": s["total_r"] if s["avg_r"] is not None else None,   # no R → unknown
            "gated": False}


def run(conn: sqlite3.Connection, *, inputs: dict[str, Any], symbol: str, timeframe: str,
        window: int = 3, script: str | None = None, source: str | None = None) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}")
    if source is None:
        if script is None:
            raise ValueError("give a script id or a source")
        source = _source_of(conn, script)
    tf_ms = timeframe_ms(timeframe)
    p = parse(source)
    if not p.signals:
        raise ValueError("the script has no signal() to compare trades with")
    consts = resolve_consts(p, inputs)
    rows = _rows(conn, symbol)
    refs = [r["ref"] for rs in rows.values() for r in rs]

    labels: dict[str, list[str | None]] = {k: [] for k in rows}
    warm_from: int | None = None
    clipped_from: int | None = None
    pending = False
    if refs:
        # Start `window` stored bars before the first trade (bars, not time),
        # so its own window is inside the evaluated, warm part of the frame.
        before = _warm_bars(conn, symbol, timeframe, min(refs), window + 1)
        from_ms = before[-(window + 1)].time_msc if len(before) > window else \
            (before[0].time_msc if before else min(refs))
        assert from_ms is not None
        to_ms = max(refs)
        lb = lookback(p, consts, tf_ms)
        fr = load_frames(conn, p, consts, symbol=symbol, timeframe=timeframe,
                         from_ms=from_ms, to_ms=to_ms, lb=lb)
        view = fr.f.index[fr.f.index >= from_ms]
        cap = backtest.MAX_BACKTEST_BARS
        if len(view) > cap:
            # Too long a span (an old replay trade, or M1): evaluate the newest
            # `cap` bars only. Older trades fall before warm-up and read unknown,
            # and the clip is reported — one old trade never blanks the tab.
            from_ms = clipped_from = int(view[-cap])
            fr = load_frames(conn, p, consts, symbol=symbol, timeframe=timeframe,
                             from_ms=from_ms, to_ms=to_ms, lb=lb)
        warm_from, pending = fr.warm_from_msc, fr.pending
        res = evaluate(p, fr.f, inputs, tf_ms, htf=fr.htf)
        # A value before warm-up is not trusted, even when it reads 0.0: NaN.
        warm = fr.f.index >= warm_from if warm_from is not None else fr.f.index < fr.f.index.min()
        sigs = [(s.side, v.where(warm)) for s, v in zip(p.signals, res.signals, strict=True)]
        # Buckets M1 traded in that the frame lacks: store holes, not closures.
        # A window spanning one is not the N bars before the trade.
        # No window starts before `from_ms`; on M1 the frame IS the M1 store.
        idx = fr.f.index.to_numpy(dtype="int64")
        missing = np.array([], dtype="int64")
        if timeframe != "M1":
            m1 = np.array(cs.bar_times(conn, symbol, "M1", from_ms, to_ms), dtype="int64")
            buckets = np.unique(m1 - m1 % tf_ms)      # resample.bucket_start, vectorised
            missing = buckets[~np.isin(buckets, idx)]
        for k, rs in rows.items():
            labels[k] = [agreement(fr.f.index, tf_ms, sigs, r["ref"], r["direction"], window,
                                   missing=missing) for r in rs]

    sources: dict[str, Any] = {}
    for k, rs in rows.items():
        cell = _real_cell if k == "real" else _sim_cell
        sources[k] = {
            "true": cell([r for r, lab in zip(rs, labels[k], strict=True) if lab == "true"]),
            "false": cell([r for r, lab in zip(rs, labels[k], strict=True) if lab == "false"]),
            "n_unknown": sum(1 for lab in labels[k] if lab is None),
        }
    digest = hashlib.sha256((source + json.dumps(inputs, sort_keys=True)).encode()).hexdigest()
    return {"computed_ms": now_ms(), "source_hash": digest, "window": window,
            "warm_from_msc": warm_from, "clipped_from_msc": clipped_from, "pending": pending,
            "sources": sources}
