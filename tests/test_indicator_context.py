"""Trade context — did a same-side `signal()` fire in the N closed bars before a
trade? (spec 2026-10-08-indicators-trade-context, §2 A). Pure; series are
hand-built so each test states exactly which bar fires."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from journal.domain.indicators.context import MAX_GAP_MS, agreement
from indicator_helpers import MINUTE

TF = 5 * MINUTE


def bars(n: int, start: int = 0, step: int = TF) -> pd.Index:
    return pd.Index([start + i * step for i in range(n)], name="time_msc")


def sig(index: pd.Index, fires: set[int], nan: set[int] = frozenset()) -> pd.Series:
    return pd.Series([math.nan if i in nan else (1.0 if i in fires else 0.0)
                      for i in range(len(index))], index=index, dtype="float64")


def test_fire_inside_window_is_true():
    ix = bars(10)
    # Ref at the close of bar 9 → last closed bar is 9; window 3 = bars 7..9.
    assert agreement(ix, TF, [("long", sig(ix, {7}))], 10 * TF, "buy", 3) == "true"


def test_fire_before_window_is_false():
    ix = bars(10)
    assert agreement(ix, TF, [("long", sig(ix, {6}))], 10 * TF, "buy", 3) == "false"


def test_forming_bar_is_never_read():
    ix = bars(10)
    # Ref mid-way through bar 9: bar 9 has not closed, last closed is bar 8.
    assert agreement(ix, TF, [("long", sig(ix, {9}))], 9 * TF + MINUTE, "buy", 1) == "false"
    assert agreement(ix, TF, [("long", sig(ix, {8}))], 9 * TF + MINUTE, "buy", 1) == "true"


def test_ref_exactly_on_a_close_includes_that_bar():
    ix = bars(10)
    assert agreement(ix, TF, [("long", sig(ix, {4}))], 5 * TF, "buy", 1) == "true"


def test_opposite_side_signal_is_ignored():
    ix = bars(10)
    sigs = [("short", sig(ix, {9})), ("long", sig(ix, set()))]
    assert agreement(ix, TF, sigs, 10 * TF, "buy", 3) == "false"
    assert agreement(ix, TF, sigs, 10 * TF, "sell", 3) == "true"


def test_window_counts_stored_bars_not_time():
    # Bars 0..4, then a two-day closure, then bars 5..9 (a weekend gap).
    gap = 2 * 86_400_000
    ix = pd.Index([i * TF for i in range(5)] + [gap + i * TF for i in range(5, 10)])
    # Ref after bar 5 closes: window 3 = bars 3, 4, 5 — reaches back over the gap.
    ref = gap + 6 * TF
    assert agreement(ix, TF, [("long", sig(ix, {3}))], ref, "buy", 3) == "true"


def test_nan_in_window_is_unknown_not_false():
    ix = bars(10)
    assert agreement(ix, TF, [("long", sig(ix, set(), nan={8}))], 10 * TF, "buy", 3) is None


def test_nan_outside_window_does_not_matter():
    ix = bars(10)
    assert agreement(ix, TF, [("long", sig(ix, set(), nan={2}))], 10 * TF, "buy", 3) == "false"


def test_a_known_fire_beats_a_nan_elsewhere_in_the_window():
    # One series fires, the other is warming up: "a same-side signal fired" is known true.
    ix = bars(10)
    sigs = [("long", sig(ix, {9})), ("long", sig(ix, set(), nan={7, 8, 9}))]
    assert agreement(ix, TF, sigs, 10 * TF, "buy", 3) == "true"


def test_too_few_stored_bars_is_unknown():
    ix = bars(10)
    # Last closed bar is 1 → only 2 bars available for a window of 3.
    assert agreement(ix, TF, [("long", sig(ix, {0}))], 2 * TF, "buy", 3) is None


def test_ref_before_first_bar_is_unknown():
    ix = bars(10, start=100 * TF)
    assert agreement(ix, TF, [("long", sig(ix, {0}))], 50 * TF, "buy", 1) is None


def test_store_hole_longer_than_max_gap_is_unknown():
    ix = bars(10)
    # The last stored bar closed long before the trade: stale, not "no signal".
    ref = 10 * TF + MAX_GAP_MS + 1
    assert agreement(ix, TF, [("long", sig(ix, set()))], ref, "buy", 3) is None
    assert agreement(ix, TF, [("long", sig(ix, set()))], 10 * TF + MAX_GAP_MS, "buy", 3) == "false"


def test_script_without_a_same_side_signal_is_false():
    ix = bars(10)
    assert agreement(ix, TF, [("short", sig(ix, {9}))], 10 * TF, "buy", 3) == "false"


# --- `missing`: bars the market had (seen in M1) that the frame lacks ----------

def test_missing_bar_inside_the_window_is_unknown():
    ix = pd.Index([i * TF for i in range(10) if i != 8])        # bar 8 absent from the frame
    sigs = [("long", sig(ix, {6}))]                             # positional: fires on bar 7
    assert agreement(ix, TF, sigs, 10 * TF, "buy", 3) == "true"  # 7, 9 and the gap read as fine
    assert agreement(ix, TF, sigs, 10 * TF, "buy", 3, missing=np.array([8 * TF])) is None


def test_missing_bar_between_last_close_and_ref_is_unknown():
    ix = bars(8)                                                # store stops at bar 7
    sigs = [("long", sig(ix, set()))]
    # The market traded bars 8 and 9 (M1 has them): the frame is stale, not quiet.
    assert agreement(ix, TF, sigs, 10 * TF, "buy", 3, missing=np.array([8 * TF, 9 * TF])) is None


def test_missing_bar_still_forming_at_ref_does_not_count():
    ix = bars(10)
    sigs = [("long", sig(ix, set()))]
    # Bar 10 opened before ref but has not closed: it was never going to be read.
    assert agreement(ix, TF, sigs, 10 * TF + MINUTE, "buy", 3, missing=np.array([10 * TF])) == "false"


def test_missing_bar_before_the_window_does_not_count():
    ix = pd.Index([i * TF for i in range(10) if i != 3])
    sigs = [("long", sig(ix, set()))]
    assert agreement(ix, TF, sigs, 10 * TF, "buy", 3, missing=np.array([3 * TF])) == "false"
