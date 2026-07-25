"""Pure replay evaluator — the single source of truth for fake-position fills,
SL/TP resolution, P&L and R during Chart Phase D training. No DB, no bridge, no
MT5 (CLAUDE.md rules 1, 7): it takes plain dataclasses and is fixture-testable.

Fill model: a decision made while bar N is the newest revealed bar creates a
PENDING position that fills at the OPEN of the first bar strictly later than the
decision. The entry bar itself is then evaluated for SL/TP (a gap can stop you
on the bar you entered on). When a single bar's wick reaches BOTH sl and tp,
the STOP fills first (pessimistic — OHLC cannot reveal true intra-bar order, and
an honest trainer never flatters). Exit price is the SL/TP level itself
(gap-through-level slippage is not modelled). A manual close is a market order
filled at the next bar's open, and executes at that open BEFORE the bar's wicks.

Money is USC (account currency); R is a unit-free ratio (rule 4: NULL when no SL).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Bar:
    time_msc: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class PositionState:
    id: int
    direction: str            # "buy" | "sell"
    volume: float
    decision_msc: int
    sl: float                 # 0.0 = none set (rule 4)
    tp: float                 # 0.0 = none set (rule 4)
    status: str               # "pending" | "open" | "closed"
    entry_msc: int | None
    entry_price: float | None
    close_requested_msc: int | None
    exit_msc: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None   # "tp" | "sl" | "manual" | "eod"


@dataclass
class FillEvent:
    position_id: int
    kind: str                 # "fill" | "exit"
    price: float
    time_msc: int
    reason: str | None        # exit: "tp"|"sl"|"manual"; fill: None


def _close(p: PositionState, price: float, time_msc: int, reason: str) -> FillEvent:
    p.status = "closed"
    p.exit_price = price
    p.exit_msc = time_msc
    p.exit_reason = reason
    return FillEvent(p.id, "exit", price, time_msc, reason)


def step_bar(positions: list[PositionState], bar: Bar) -> list[FillEvent]:
    """Advance every position by one revealed `bar`. Mutates `positions` in place
    and returns the fills/exits that happened ON this bar, in position order.

    Order per position: (1) fill if pending and this bar is strictly later than
    the decision; (2) if a manual close is pending, exit at this bar's OPEN
    (market order, ahead of the wicks); (3) otherwise resolve SL/TP against the
    bar's wicks, stop-first when both are inside the bar.
    """
    events: list[FillEvent] = []
    for p in positions:
        if p.status == "closed":
            continue

        if p.status == "pending":
            if bar.time_msc <= p.decision_msc:
                continue                       # not tradable until strictly later
            p.status = "open"
            p.entry_price = bar.open
            p.entry_msc = bar.time_msc
            events.append(FillEvent(p.id, "fill", bar.open, bar.time_msc, None))

        # p is now open (either already, or just filled above and evaluated same bar).
        if p.status != "open":
            continue

        if p.close_requested_msc is not None and bar.time_msc > p.close_requested_msc:
            events.append(_close(p, bar.open, bar.time_msc, "manual"))
            continue

        if p.direction == "buy":
            sl_hit = p.sl > 0 and bar.low <= p.sl
            tp_hit = p.tp > 0 and bar.high >= p.tp
        else:
            sl_hit = p.sl > 0 and bar.high >= p.sl
            tp_hit = p.tp > 0 and bar.low <= p.tp

        if sl_hit:                              # stop-first when both are hit
            events.append(_close(p, p.sl, bar.time_msc, "sl"))
        elif tp_hit:
            events.append(_close(p, p.tp, bar.time_msc, "tp"))

    return events


def _signed_move(direction: str, entry: float, exit: float) -> float:
    return (exit - entry) if direction == "buy" else (entry - exit)


def net_profit_usc(direction: str, entry: float, exit: float, volume: float,
                   tick_size: float, tick_value: float) -> float:
    """Signed P&L in account currency (USC). `tick_value` is per lot per tick,
    already in account currency (symbol_specs). Never a bare '$'."""
    ticks = _signed_move(direction, entry, exit) / tick_size
    return ticks * tick_value * volume


def r_multiple(direction: str, entry: float, exit: float, sl: float) -> float | None:
    """Unit-free R = signed move / initial risk distance. NULL when no SL is set
    (sl == 0) or the SL sits exactly at entry (known-zero risk — Trap 6 shape)."""
    risk = abs(entry - sl)
    if sl == 0 or risk < 1e-9:
        return None
    return _signed_move(direction, entry, exit) / risk
