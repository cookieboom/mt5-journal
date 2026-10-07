"""Strategy Tester: simulate a script's `signal()`/`exit()` over chart bars,
then summarise the simulated trades (spec 2026-10-07-indicators-strategy-tester).

Pure — frames in, dataclasses and dicts out. Fills come from
`replay_eval.step_bar`, the one fill model: an entry decided on bar i fills at
open[i+1], a requested close exits at the next open ahead of the wicks, and a
bar reaching both stop and target stops out first.

R is net of spread: one spread (the entry bar's, in points) per round trip.
`ponytail:` the bid/ask asymmetry of SL/TP triggers is not modelled — upgrade
by shifting the trigger levels by the spread if the tester's R drifts from
live R. A trade with no stop has no R (rule 4); an open trade at the window
end has no outcome and never enters stats.

Ungated like `sim_stats` (CLAUDE.md §8 exceptions): every metric ships its `n`."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd

from ...analytics.report import sequence_stats
from ...analytics.sessions import session_of, session_window_msc
from .. import sim_stats
from ..excursion import compute_excursion
from ..replay_eval import Bar, PositionState, r_multiple, step_bar
from . import builtins as B
from .builtins import DAY_MS
from .engine import Result
from .lang import Program, const_value

Reason = Literal["sl", "tp", "exit", "opposite", "max_hold", "open"]


@dataclass(frozen=True)
class Settings:
    """The tester form. `sl`/`sl_value`/`atr_len`/`tp_r` are the defaults a
    signal without `stop=`/`target=`/`target_r=` falls back to."""
    sl: Literal["atr", "points", "none"] = "atr"
    sl_value: float = 1.5
    atr_len: int = 14
    tp_r: float | None = 2.0
    max_hold: int | None = None
    opposite_closes: bool = True
    overlap: bool = False
    spread_fallback: float | None = None      # points; None = unknown stays unknown


@dataclass
class Trade:
    id: int
    side: str
    decision_msc: int
    entry_msc: int
    entry: float
    sl: float                                 # 0.0 = none (rule 4)
    tp: float                                 # 0.0 = none
    exit_msc: int | None = None
    exit: float | None = None
    reason: Reason = "open"
    r: float | None = None
    r_gross: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    spread_fallback: bool = False


@dataclass
class Sim:
    trades: list[Trade]
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _Live:
    pos: PositionState
    side: str
    spread_pts: float | None
    fallback: bool
    entry_idx: int = -1
    reason: Reason = "exit"


def _num(s: pd.Series | None, i: int) -> float | None:
    if s is None:
        return None
    v = float(s.iloc[i])
    return v if math.isfinite(v) else None


def simulate(f: pd.DataFrame, r: Result, p: Program, consts: dict[str, float | bool],
             settings: Settings, point: float, start_msc: int | None = None) -> Sim:
    """Walk `f` bar by bar. `r` is `evaluate(p, f, …)`; `point` is the symbol's
    point size (spread is stored in points). Signals before `start_msc` (the
    warm-up) are ignored."""
    t = f.index.to_numpy(dtype="int64")
    o, h, lo, c = (f[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close"))
    spread = f["spread"].to_numpy(dtype="float64")
    atr = B.atr(f, settings.atr_len) if settings.sl == "atr" else None
    counts = dict.fromkeys(("rejected", "open", "spread_fallback", "unknown_cost",
                            "no_stop"), 0)
    live: list[_Live] = []
    trades: list[Trade] = []
    rows = list(zip(t.tolist(), lo.tolist(), h.tolist(), strict=True))
    next_id = 0

    def finish(x: _Live, exit_idx: int, reason: Reason) -> None:
        ps = x.pos
        assert ps.entry_price is not None and ps.entry_msc is not None
        assert ps.exit_price is not None and ps.exit_msc is not None
        tr = Trade(ps.id, x.side, ps.decision_msc, ps.entry_msc, ps.entry_price, ps.sl, ps.tp,
                   ps.exit_msc, ps.exit_price, reason, spread_fallback=x.fallback)
        tr.r_gross = r_multiple(ps.direction, ps.entry_price, ps.exit_price, ps.sl)
        if tr.r_gross is not None:
            risk = abs(ps.entry_price - ps.sl)
            if x.spread_pts is not None:
                tr.r = tr.r_gross - x.spread_pts * point / risk
            mae, mfe = compute_excursion(rows[x.entry_idx:exit_idx + 1], ps.entry_msc,
                                         ps.exit_msc, ps.entry_price, ps.direction)
            tr.mae_r = mae / risk if mae is not None else None
            tr.mfe_r = mfe / risk if mfe is not None else None
        trades.append(tr)

    def request(x: _Live, i: int, reason: Reason) -> None:
        if x.pos.close_requested_msc is None:
            x.pos.close_requested_msc, x.reason = int(t[i]), reason

    for i in range(len(t)):
        bar = Bar(int(t[i]), o[i], h[i], lo[i], c[i])
        events = step_bar([x.pos for x in live], bar)
        by_id = {x.pos.id: x for x in live}
        for e in events:
            x = by_id[e.position_id]
            if e.kind == "fill":
                x.entry_idx = i
            else:
                finish(x, i, x.reason if e.reason == "manual" else e.reason)  # type: ignore[arg-type]
        live = [x for x in live if x.pos.status != "closed"]
        if start_msc is not None and t[i] < start_msc:
            continue

        if settings.max_hold is not None:
            for x in live:
                if x.pos.status == "open" and i - x.entry_idx + 1 >= settings.max_hold:
                    request(x, i, "max_hold")
        for ex, series in zip(p.exits, r.exits, strict=True):
            if series.iloc[i] == 1.0:
                for x in live:
                    if x.side == ex.side and x.pos.status == "open":
                        request(x, i, "exit")

        for k, sig in enumerate(p.signals):
            if r.signals[k].iloc[i] != 1.0:
                continue
            if settings.opposite_closes:
                for x in live:
                    if x.side != sig.side:
                        request(x, i, "opposite")
                live = [x for x in live if not (x.side != sig.side and x.pos.status == "pending")]
            if not settings.overlap and any(x.pos.close_requested_msc is None for x in live):
                continue
            if i + 1 >= len(t):
                continue                                 # no next open to fill at
            fill, sign = o[i + 1], (1.0 if sig.side == "long" else -1.0)

            sl = _num(r.stops[k], i)
            if sl is None:
                a = _num(atr, i) if atr is not None else None
                if settings.sl == "atr" and a is not None:
                    sl = fill - sign * settings.sl_value * a
                elif settings.sl == "points":
                    sl = fill - sign * settings.sl_value * point
                else:
                    sl = 0.0
            tp = _num(r.targets[k], i)
            if tp is None:
                rr = (float(const_value(sig.target_r, consts)) if sig.target_r is not None
                      else settings.tp_r)
                tp = fill + sign * rr * abs(fill - sl) if rr is not None and sl else 0.0
            if (sl and sign * (fill - sl) <= 1e-9) or (tp and sign * (tp - fill) <= 1e-9):
                counts["rejected"] += 1                  # never silently "fixed"
                continue

            sp: float | None = spread[i + 1] if math.isfinite(spread[i + 1]) else None
            fallback = sp is None and settings.spread_fallback is not None
            if fallback:
                sp = settings.spread_fallback
            counts["no_stop"] += not sl
            counts["spread_fallback"] += fallback
            counts["unknown_cost"] += bool(sl) and sp is None
            next_id += 1
            live.append(_Live(PositionState(
                id=next_id, direction="buy" if sign > 0 else "sell", volume=1.0,
                decision_msc=int(t[i]), sl=sl, tp=tp, status="pending", entry_msc=None,
                entry_price=None, close_requested_msc=None), sig.side, sp, fallback))

    for x in live:
        ps = x.pos
        if ps.status == "open":
            assert ps.entry_msc is not None and ps.entry_price is not None
            counts["open"] += 1
            trades.append(Trade(ps.id, x.side, ps.decision_msc, ps.entry_msc,
                                ps.entry_price, ps.sl, ps.tp, spread_fallback=x.fallback))
    trades.sort(key=lambda tr: tr.id)
    return Sim(trades, counts)


# --- report ---------------------------------------------------------------------

def _segment(trades: list[Trade]) -> dict[str, Any]:
    rows = [{"net_profit": tr.r if tr.r is not None else 0.0, "r_multiple": tr.r,
             "mae_r": tr.mae_r, "mfe_r": tr.mfe_r} for tr in trades]
    out = sim_stats.summary(rows)
    rs = [tr.r for tr in trades if tr.r is not None]
    out["win_rate"] = sum(1 for x in rs if x > 0) / len(rs) if rs else None
    loss = -sum(x for x in rs if x < 0)
    out["profit_factor"] = sum(x for x in rs if x > 0) / loss if loss > 1e-9 else None
    seq = [{"close_time_msc": tr.exit_msc, "net_profit": tr.r} for tr in trades
           if tr.r is not None]
    n_seq, dd, win_streak, loss_streak = sequence_stats(seq)
    out.update(n_r=len(rs), max_dd_r=dd, max_win_streak=win_streak,
               max_loss_streak=loss_streak)
    return out


def _bucket(trades: list[Trade], key: Any) -> list[dict[str, Any]]:
    groups: dict[Any, list[float]] = defaultdict(list)
    counts: dict[Any, int] = defaultdict(int)
    for tr in trades:
        k = key(tr.entry_msc)
        counts[k] += 1
        if tr.r is not None:
            groups[k].append(tr.r)
    out = []
    for k in sorted(counts):
        rs = groups[k]
        out.append({"key": k, "n": counts[k],
                    "win_rate": sum(1 for x in rs if x > 0) / len(rs) if rs else None,
                    "avg_r": sum(rs) / len(rs) if rs else None, "total_r": sum(rs)})
    return out


# Epoch 0 is a Thursday: Monday 00:00 UTC sits 3 days before it, mod 7 days.
_MONDAY_MS = -3 * DAY_MS


def _day(ms: int) -> tuple[int, int]:
    start = ms - ms % DAY_MS
    return start, start + DAY_MS


def _week(ms: int) -> tuple[int, int]:
    start = ms - (ms - _MONDAY_MS) % (7 * DAY_MS)
    return start, start + 7 * DAY_MS


def _periods(trades: list[Trade], window: Any, split_msc: int) -> list[dict[str, Any]]:
    """Trades grouped by the period `[key, end_msc)` their entry falls in, each
    group a `_bucket` row. `last_exit_msc` — where a replay of the period must
    reach (a trade can exit after its period ends; exits are always stored
    bars, never the future). `segment` splits on decision time exactly as the
    segments do: `is`, `oos`, or `mixed` when the split falls inside."""
    groups: dict[tuple[int, int], list[Trade]] = defaultdict(list)
    for tr in trades:
        groups[window(tr.entry_msc)].append(tr)
    out = []
    for (start, end), trs in sorted(groups.items()):
        (row,) = _bucket(trs, lambda _ms, k=start: k)
        oos = [tr.decision_msc >= split_msc for tr in trs]
        out.append(row | {
            "end_msc": end,
            "last_exit_msc": max(tr.exit_msc for tr in trs if tr.exit_msc is not None),
            "segment": "oos" if all(oos) else "is" if not any(oos) else "mixed"})
    return out


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def report(sim: Sim, split_msc: int) -> dict[str, Any]:
    """Segments `all | is | oos` split on decision time (OOS = at/after
    `split_msc`), cumulative-R equity at each exit, and hour/session/weekday
    breakdowns (UTC — the client relabels to WIB, rule 3), plus `all`'s
    day/week/session-instance periods for the replay jump. Open trades are
    excluded: their outcome is unknown."""
    done = sorted((tr for tr in sim.trades if tr.reason != "open"),
                  key=lambda tr: (tr.exit_msc, tr.id))
    seg = {"all": done, "is": [tr for tr in done if tr.decision_msc < split_msc],
           "oos": [tr for tr in done if tr.decision_msc >= split_msc]}
    equity, cum = [], 0.0
    for tr in done:
        if tr.r is not None:
            cum += tr.r
            equity.append({"t": tr.exit_msc, "r": cum})
    out: dict[str, Any] = {
        "segments": {k: _segment(v) for k, v in seg.items()},
        "equity": equity,
        "breakdown": {k: {"hour": _bucket(seg[k], lambda ms: _utc(ms).hour),
                          "session": _bucket(seg[k], session_of),
                          "dow": _bucket(seg[k], lambda ms: _utc(ms).weekday())}
                      for k in ("all", "oos")},
    }
    out["breakdown"]["all"]["periods"] = {
        name: _periods(done, window, split_msc)
        for name, window in (("day", _day), ("week", _week), ("session", session_window_msc))}
    return out
