"""Pure replay evaluator — fixture-tested, no DB, no bridge (CLAUDE.md rule 7)."""
from __future__ import annotations

from journal.domain.replay_eval import (
    Bar, PositionState, step_bar, net_profit_usc, r_multiple,
)


def _pending(pid=1, direction="buy", sl=0.0, tp=0.0, decision_msc=1000, volume=0.1):
    return PositionState(
        id=pid, direction=direction, volume=volume, decision_msc=decision_msc,
        sl=sl, tp=tp, status="pending", entry_msc=None, entry_price=None,
        close_requested_msc=None,
    )


def _bar(t, o, h, l, c):
    return Bar(time_msc=t, open=o, high=h, low=l, close=c)


def test_pending_fills_at_next_bar_open():
    p = _pending(decision_msc=1000)
    # Same-time bar must NOT fill (needs strictly later); next bar fills at open.
    assert step_bar([p], _bar(1000, 10, 11, 9, 10)) == []
    assert p.status == "pending"
    ev = step_bar([p], _bar(2000, 12, 13, 11, 12))
    assert p.status == "open" and p.entry_price == 12 and p.entry_msc == 2000
    assert [e.kind for e in ev] == ["fill"]


def test_long_take_profit_hit_at_level():
    p = _pending(direction="buy", tp=13.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 9.5, 10))     # fills at 10
    ev = step_bar([p], _bar(3000, 11, 13.5, 10.5, 12))  # high 13.5 >= tp 13
    assert p.status == "closed" and p.exit_reason == "tp" and p.exit_price == 13.0
    assert [(e.kind, e.reason) for e in ev] == [("exit", "tp")]


def test_long_stop_loss_hit_at_level():
    p = _pending(direction="buy", sl=9.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 9.5, 10))     # fills at 10
    step_bar([p], _bar(3000, 9.8, 10, 8.5, 9))        # low 8.5 <= sl 9
    assert p.status == "closed" and p.exit_reason == "sl" and p.exit_price == 9.0


def test_both_hit_in_one_bar_is_pessimistic_sl_first():
    p = _pending(direction="buy", sl=9.0, tp=13.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10, 10, 10))         # fills at 10
    step_bar([p], _bar(3000, 10, 14, 8, 11))          # bar spans BOTH sl and tp
    assert p.exit_reason == "sl" and p.exit_price == 9.0


def test_entry_bar_itself_can_stop_out():
    # Fill at next bar open; that same bar's wick immediately hits the stop.
    p = _pending(direction="buy", sl=9.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 8.5, 9.2))     # fills at 10 AND low 8.5 <= 9
    assert p.status == "closed" and p.exit_reason == "sl" and p.exit_price == 9.0


def test_short_mirror():
    p = _pending(direction="sell", sl=11.0, tp=8.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10, 10, 10))         # fills at 10
    step_bar([p], _bar(3000, 10, 12, 9.5, 10))        # high 12 >= sl 11 → stop
    assert p.exit_reason == "sl" and p.exit_price == 11.0


def test_manual_close_fills_next_bar_open_and_beats_same_bar_wick():
    p = _pending(direction="buy", sl=9.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10.5, 9.5, 10))      # fills at 10
    p.close_requested_msc = 2000                       # requested at cursor bar 2000
    ev = step_bar([p], _bar(3000, 10.2, 11, 8.5, 9))  # open exit BEFORE the 8.5 wick
    assert p.exit_reason == "manual" and p.exit_price == 10.2
    assert [(e.kind, e.reason) for e in ev] == [("exit", "manual")]


def test_no_sl_never_stops_and_r_is_none():
    p = _pending(direction="buy", sl=0.0, tp=0.0, decision_msc=1000)
    step_bar([p], _bar(2000, 10, 10, 10, 10))
    step_bar([p], _bar(3000, 1, 10, 0.5, 2))          # huge adverse move, sl=0=none
    assert p.status == "open"
    assert r_multiple("buy", 10, 5, 0.0) is None


def test_net_profit_and_r_math_xauusd():
    # XAUUSDc: tick_size=0.001, tick_value=0.1 USC, volume 0.1.
    # +1.0 price move = 1000 ticks * 0.1 * 0.1 = 10.0 USC.
    assert abs(net_profit_usc("buy", 4000.0, 4001.0, 0.1, 0.001, 0.1) - 10.0) < 1e-9
    assert abs(net_profit_usc("sell", 4000.0, 4001.0, 0.1, 0.001, 0.1) + 10.0) < 1e-9
    # R = signed_move / |entry - sl|.
    assert abs(r_multiple("buy", 4000.0, 4002.0, 3999.0) - 2.0) < 1e-9
    assert abs(r_multiple("sell", 4000.0, 3998.0, 4001.0) - 2.0) < 1e-9
