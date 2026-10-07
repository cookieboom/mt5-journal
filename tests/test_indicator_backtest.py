"""Strategy Tester simulator + stats (spec 2026-10-07-indicators-strategy-tester).

Frames are hand-built; the `volume` column drives the script's conditions so a
test states exactly which bar fires. Fills come from `replay_eval.step_bar` —
the one fill model — so these tests pin how the simulator drives it."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from journal.domain.indicators.backtest import Settings, report, simulate
from journal.domain.indicators.engine import evaluate
from journal.domain.indicators.frame import to_frame
from journal.domain.indicators.lang import parse, resolve_consts
from journal.domain.resample import resample_m1
from indicator_helpers import MINUTE, walk_candles

NONE = Settings(sl="none", tp_r=None)
LONG = 'signal("long", volume == 1)'
SHORT = 'signal("short", volume == 2)'


def frame(o, h=None, lo=None, c=None, vol=None, spread=None) -> pd.DataFrame:
    n = len(o)
    cols = {
        "open": o, "high": h or [x + 1 for x in o], "low": lo or [x - 1 for x in o],
        "close": c or o, "volume": vol or [0] * n, "spread": spread or [0] * n,
    }
    return pd.DataFrame({k: [float(x) for x in v] for k, v in cols.items()},
                        index=pd.Index([i * MINUTE for i in range(n)], name="time_msc"))


def run(src: str, f: pd.DataFrame, settings: Settings = NONE, point: float = 1.0,
        inputs: dict | None = None):
    p = parse(src)
    r = evaluate(p, f, inputs or {}, MINUTE)
    return simulate(f, r, p, resolve_consts(p, inputs or {}), settings, point)


def test_fills_at_next_open_and_window_end_is_open_not_resolved():
    sim = run(LONG, frame([10, 11, 12, 13], vol=[1, 0, 0, 0]))
    (t,) = sim.trades
    assert (t.side, t.decision_msc, t.entry_msc, t.entry) == ("long", 0, MINUTE, 11)
    assert t.reason == "open" and t.exit_msc is None and t.r is None
    assert sim.counts["open"] == 1


def test_signal_on_last_bar_never_enters():
    assert run(LONG, frame([10, 11], vol=[0, 1])).trades == []


def test_stop_first_when_one_bar_reaches_both():
    src = 'signal("long", volume == 1, stop=low - 4, target=high + 4)'
    #           signal bar: low 9 → stop 5, high 11 → target 15; bar 2 hits both
    f = frame([10, 10, 10], h=[11, 11, 20], lo=[9, 9, 0], vol=[1, 0, 0])
    (t,) = run(src, f).trades
    assert (t.reason, t.exit, t.sl, t.tp) == ("sl", 5, 5, 15)
    assert t.r == pytest.approx(-1.0)


def test_exit_closes_at_the_next_open():
    src = LONG + '\nexit("long", volume == 3)'
    (t,) = run(src, frame([10, 11, 12, 13, 14], vol=[1, 0, 3, 0, 0])).trades
    assert (t.reason, t.exit_msc, t.exit) == ("exit", 3 * MINUTE, 13)


def test_opposite_signal_closes_and_reverses_on_the_same_open():
    a, b = run(LONG + "\n" + SHORT, frame([10, 11, 12, 13, 14], vol=[1, 0, 2, 0, 0])).trades
    assert (a.side, a.reason, a.exit_msc, a.exit) == ("long", "opposite", 3 * MINUTE, 13)
    assert (b.side, b.entry_msc, b.entry, b.reason) == ("short", 3 * MINUTE, 13, "open")


def test_opposite_off_blocks_the_reversal_without_overlap():
    s = Settings(sl="none", tp_r=None, opposite_closes=False)
    sim = run(LONG + "\n" + SHORT, frame([10, 11, 12, 13, 14], vol=[1, 0, 2, 0, 0]), s)
    assert [(t.side, t.reason) for t in sim.trades] == [("long", "open")]


def test_one_at_a_time_unless_overlap():
    f = frame([10, 11, 12, 13, 14], vol=[1, 1, 1, 0, 0])
    assert len(run(LONG, f).trades) == 1
    s = Settings(sl="none", tp_r=None, overlap=True)
    assert [t.entry for t in run(LONG, f, s).trades] == [11, 12, 13]


def test_max_hold_closes_after_n_bars():
    s = Settings(sl="none", tp_r=None, max_hold=2)
    (t,) = run(LONG, frame([10, 11, 12, 13, 14], vol=[1, 0, 0, 0, 0]), s).trades
    assert (t.reason, t.entry_msc, t.exit_msc, t.exit) == ("max_hold", MINUTE, 3 * MINUTE, 13)


def test_atr_stop_and_target_r_are_sized_from_the_fill():
    # flat bars of range 2 → ATR(2) = 2; fill = 20 (gap up from 10)
    f = frame([10, 10, 10, 20, 20], vol=[0, 0, 1, 0, 0])
    s = Settings(sl="atr", sl_value=1.5, atr_len=2, tp_r=2.0)
    (t,) = run(LONG, f, s).trades
    assert (t.entry, t.sl, t.tp) == (20, 17, 26)


def test_script_stop_and_target_r_win_over_the_form():
    src = 'signal("long", volume == 1, stop=low - 1, target_r=3)'
    f = frame([10, 10, 10], vol=[1, 0, 0])
    (t,) = run(src, f, Settings(sl="points", sl_value=50, tp_r=1.0)).trades
    assert (t.sl, t.tp) == (8, 16)              # risk |10 − 8| = 2, 3R


def test_points_stop():
    (t,) = run(SHORT, frame([10, 10, 10], vol=[2, 0, 0]),
               Settings(sl="points", sl_value=30, tp_r=None), point=0.1).trades
    assert t.sl == pytest.approx(13) and t.tp == 0


def test_wrong_side_bracket_is_rejected_not_fixed():
    src = 'signal("long", volume == 1, stop=high + 5)'
    sim = run(src, frame([10, 10, 10], vol=[1, 0, 0]))
    assert sim.trades == [] and sim.counts["rejected"] == 1


def test_no_stop_means_no_r_and_target_r_is_ignored():
    sim = run('signal("long", volume == 1, target_r=2)', frame([10, 10, 10], vol=[1, 0, 0]))
    (t,) = sim.trades
    assert (t.sl, t.tp) == (0, 0) and sim.counts["no_stop"] == 1


def test_spread_is_charged_in_r_at_the_entry_bar():
    src = 'signal("long", volume == 1, stop=low - 1, target=high + 1)'
    # fill 10, stop 8 (risk 2), target 12; entry-bar spread 5 pts × 0.1 = 0.5 → 0.25 R
    f = frame([10, 10, 10], h=[11, 11, 13], vol=[1, 0, 0], spread=[0, 5, 0])
    (t,) = run(src, f, point=0.1).trades
    assert t.reason == "tp" and t.r_gross == pytest.approx(1.0)
    assert t.r == pytest.approx(0.75) and not t.spread_fallback


def test_unknown_spread_uses_the_fallback_else_r_is_unknown():
    src = 'signal("long", volume == 1, stop=low - 1, target=high + 1)'
    f = frame([10, 10, 10], h=[11, 11, 13], vol=[1, 0, 0])
    f.loc[MINUTE, "spread"] = np.nan
    sim = run(src, f, Settings(sl="none", tp_r=None, spread_fallback=10), point=0.1)
    assert sim.trades[0].r == pytest.approx(0.5) and sim.trades[0].spread_fallback
    assert sim.counts["spread_fallback"] == 1
    sim = run(src, f, point=0.1)
    assert sim.trades[0].r is None and sim.trades[0].r_gross == pytest.approx(1.0)
    assert sim.counts["unknown_cost"] == 1


def test_mae_mfe_in_r_over_the_bars_lived():
    src = 'signal("long", volume == 1, stop=low - 1)\nexit("long", volume == 3)'
    # fill 10 risk 2; lived bars 1..2 reach low 9 / high 13, exit at bar 3 open
    f = frame([10, 10, 10, 10], h=[11, 11, 13, 11], lo=[9, 9, 9.5, 9], vol=[1, 0, 3, 0])
    (t,) = run(src, f).trades
    assert t.reason == "exit"
    assert t.mae_r == pytest.approx(0.5) and t.mfe_r == pytest.approx(1.5)


def test_signals_before_start_are_ignored():
    p = parse(LONG)
    f = frame([10, 11, 12, 13], vol=[1, 0, 1, 0])
    sim = simulate(f, evaluate(p, f, {}, MINUTE), p, {}, NONE, 1.0, start_msc=MINUTE)
    assert [t.decision_msc for t in sim.trades] == [2 * MINUTE]


MIXED = """
x = tf("H1", ema(close, 10))
signal("long", crossover(close, x), stop=low[1] - atr(14), target_r=2)
signal("short", crossunder(close, x), stop=tf("H1", high[1]))
exit("short", close < tf("H1", sma(close, 3)) - 3)
"""


def test_no_lookahead_prefix_reproduces_every_trade_closed_before_the_cut():
    m1 = walk_candles(6000)
    f5, f60 = to_frame(resample_m1(m1, "M5")), to_frame(resample_m1(m1, "H1"))
    p, H1, M5 = parse(MIXED), 60 * MINUTE, 5 * MINUTE
    s = Settings(sl="atr", sl_value=1.0, atr_len=14, tp_r=1.5, max_hold=40)

    def sim(k: int):
        f = f5.iloc[:k]
        cut = f60[f60.index + H1 <= int(f.index[-1]) + M5]
        return simulate(f, evaluate(p, f, {}, M5, htf={"H1": cut}), p, {}, s, 0.01)

    full = sim(len(f5))
    assert len([t for t in full.trades if t.reason != "open"]) > 20
    for k in np.random.default_rng(5).integers(50, len(f5), 10):
        last = int(f5.index[k - 1])
        want = [t for t in full.trades if t.exit_msc is not None and t.exit_msc < last]
        got = [t for t in sim(int(k)).trades if t.exit_msc is not None and t.exit_msc < last]
        assert [t.__dict__ | {"id": 0} for t in got] == [t.__dict__ | {"id": 0} for t in want]


# --- report(): IS/OOS split, segments, equity, breakdown ----------------------

def trades_frame():
    """Four long trades, risk 2 each (stop = close − 2, fill = that close), each
    closed by exit() two bars in: R = +1, −0.5, +1.5, +0.5."""
    src = 'signal("long", volume == 1, stop=close - 2)\nexit("long", volume == 3)'
    o = [10, 10, 10, 12, 12, 12, 11, 11, 12, 14, 14, 14, 15, 15]
    vol = [1, 0, 3, 1, 0, 3, 1, 0, 3, 1, 0, 3, 0, 0]
    return run(src, frame(o, vol=vol))


def test_report_splits_by_decision_time_and_oos_leads():
    sim = trades_frame()
    rs = [t.r for t in sim.trades]
    assert rs == pytest.approx([1.0, -0.5, 1.5, 0.5])
    rep = report(sim, split_msc=5 * MINUTE)
    assert rep["segments"]["is"]["n"] == 2 and rep["segments"]["oos"]["n"] == 2
    assert rep["segments"]["all"]["total_r"] == pytest.approx(2.5)
    assert rep["segments"]["oos"]["profit_factor"] is None          # no loss
    assert rep["segments"]["is"]["profit_factor"] == pytest.approx(2.0)
    assert rep["segments"]["all"]["max_dd_r"] == pytest.approx(0.5)
    assert rep["segments"]["all"]["max_win_streak"] == 2
    assert [e["r"] for e in rep["equity"]] == pytest.approx([1.0, 0.5, 2.0, 2.5])


def test_report_counts_unknown_r_in_n_only():
    sim = trades_frame()
    sim.trades[1].r = None
    seg = report(sim, split_msc=10**12)["segments"]["all"]
    assert seg["n"] == 4 and seg["total_r"] == pytest.approx(3.0)
    assert len(report(sim, split_msc=10**12)["equity"]) == 3


def test_report_excludes_open_trades():
    sim = run(LONG, frame([10, 11, 12, 13], vol=[1, 0, 0, 0]))
    rep = report(sim, split_msc=0)
    assert rep["segments"]["all"]["n"] == 0 and rep["equity"] == []


def test_breakdown_buckets_by_utc_hour_session_and_weekday():
    sim = trades_frame()
    b = report(sim, split_msc=5 * MINUTE)["breakdown"]
    # epoch 0 = Thursday 00:00 UTC
    assert b["all"]["hour"] == [{"key": 0, "n": 4, "win_rate": 0.75, "avg_r": 0.625,
                                 "total_r": 2.5}]
    assert [x["key"] for x in b["all"]["dow"]] == [3]
    assert b["oos"]["hour"][0]["n"] == 2
    assert {x["key"] for x in b["all"]["session"]} and all(
        isinstance(x["key"], str) for x in b["all"]["session"])


def test_empty_report_is_all_unknowns_not_zeros():
    rep = report(run(LONG, frame([10, 11], vol=[0, 0])), split_msc=0)
    seg = rep["segments"]["oos"]
    assert seg["n"] == 0 and seg["avg_r"] is None and seg["max_dd_r"] is None
    assert math.isfinite(seg["total_r"])


# --- report(): periods for the replay jump per period (spec 2026-10-08 §2 B) ---

DAY = 86_400_000


def test_periods_day_week_session_key_on_start_with_end_and_oos():
    sim = trades_frame()
    b = report(sim, split_msc=5 * MINUTE)["breakdown"]["all"]["periods"]
    # All four trades sit inside 1970-01-01 00:00–00:14 UTC (a Thursday, Asian).
    last_exit = max(t.exit_msc for t in sim.trades)
    assert b["day"] == [{"key": 0, "end_msc": DAY, "last_exit_msc": last_exit, "segment": "mixed",
                         "n": 4, "win_rate": 0.75, "avg_r": 0.625, "total_r": 2.5}]
    monday = -3 * DAY                                     # 1969-12-29
    assert [(x["key"], x["end_msc"]) for x in b["week"]] == [(monday, monday + 7 * DAY)]
    assert [(x["key"], x["end_msc"]) for x in b["session"]] == [(0, 7 * 3_600_000)]


def test_period_segment_follows_its_trades_decisions_like_the_segments_do():
    sim = trades_frame()

    def seg(split: int) -> str:
        return report(sim, split_msc=split)["breakdown"]["all"]["periods"]["day"][0]["segment"]

    assert seg(0) == "oos"
    assert seg(10**12) == "is"
    assert seg(5 * MINUTE) == "mixed"          # the split falls inside the day: say so


def test_period_replay_reaches_the_last_exit_of_its_trades():
    # A trade entered inside a session window can exit after it ends; the jump
    # must reach that exit, and must never point past a stored bar.
    sim = trades_frame()
    sim.trades[-1].exit_msc = 9 * 3_600_000                # exits in the London session
    (asian,) = [p for p in report(sim, split_msc=0)["breakdown"]["all"]["periods"]["session"]]
    assert asian["end_msc"] == 7 * 3_600_000 and asian["last_exit_msc"] == 9 * 3_600_000


def test_periods_split_trades_across_days():
    sim = trades_frame()
    for i, tr in enumerate(sim.trades):
        tr.entry_msc += (i // 2) * DAY                    # two trades a day
    days = report(sim, split_msc=0)["breakdown"]["all"]["periods"]["day"]
    assert [(d["key"], d["n"]) for d in days] == [(0, 2), (DAY, 2)]
    assert [d["total_r"] for d in days] == pytest.approx([0.5, 2.0])


def test_periods_only_in_all_not_oos():
    b = report(trades_frame(), split_msc=0)["breakdown"]
    assert "periods" in b["all"] and "periods" not in b["oos"]
    assert isinstance(b["all"]["session"][0]["key"], str)       # the label breakdown survives


def test_a_bucket_with_no_known_r_has_unknown_total_not_zero():
    sim = trades_frame()
    for tr in sim.trades:
        tr.r = None
    b = report(sim, split_msc=0)["breakdown"]["all"]
    assert b["hour"][0]["n"] == 4 and b["hour"][0]["total_r"] is None     # rule 4
    assert b["periods"]["day"][0]["total_r"] is None
