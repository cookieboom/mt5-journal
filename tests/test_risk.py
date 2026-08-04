"""M2 risk_amount — the §8 reference figure, computed by hand.

`risk_amount` is the one number every R-multiple depends on, and the docs give an
exact reference (§8 / Trap 11 / Trap 14). Any code that disagrees is wrong. Written
before the module exists (CLAUDE.md rule 7).
"""

from __future__ import annotations

from journal.domain.risk import risk_amount

# XAUUSDc specs, measured (docs §7): tick_size 0.001, tick_value 0.1 USC, 1 lot = 1 oz.
_TICK_SIZE = 0.001
_TICK_VALUE = 0.1


def test_reference_figure_is_50_usc():
    # §8: entry 4035.000, SL 4030.000, 0.10 lot XAUUSDc.
    #   ticks = |4035 - 4030| / 0.001 = 5000
    #   risk  = 5000 * 0.1 * 0.10     = 50 USC   (NOT $0.50 — that is the USD error)
    r = risk_amount(4035.000, 4030.000, _TICK_SIZE, _TICK_VALUE, 0.10)
    assert r is not None
    assert abs(r - 50.0) < 1e-9
    # The three classic wrong answers the docs call out explicitly:
    assert abs(r - 0.50) > 1e-9   # assumed USD
    assert abs(r - 5000.0) > 1e-9  # forgot tick_value
    assert abs(r - 5.0) > 1e-9    # forgot volume


def test_direction_does_not_matter():
    # Distance is absolute — a short with SL above entry has the same risk.
    r = risk_amount(4030.000, 4035.000, _TICK_SIZE, _TICK_VALUE, 0.10)
    assert r is not None and abs(r - 50.0) < 1e-9


def test_null_sl_gives_null_risk():
    # Trap 6: unknown SL must propagate to NULL risk, never a coerced 0.
    assert risk_amount(4035.0, None, _TICK_SIZE, _TICK_VALUE, 0.10) is None


def test_missing_spec_gives_null_risk():
    # No specs for the symbol -> do not guess (docs §4 h / Trap 11).
    assert risk_amount(4035.0, 4030.0, None, _TICK_VALUE, 0.10) is None
    assert risk_amount(4035.0, 4030.0, _TICK_SIZE, None, 0.10) is None
    assert risk_amount(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, None) is None


def test_zero_tick_size_is_not_a_division_error():
    # A malformed spec (tick_size 0) must yield NULL, not blow up or infinity.
    assert risk_amount(4035.0, 4030.0, 0.0, _TICK_VALUE, 0.10) is None


# Task 1: volume_for_risk, floor_to_step, direction_for_sl
from journal.domain.risk import (
    direction_for_sl,
    floor_to_step,
    volume_for_risk,
)


def test_volume_for_risk_is_the_inverse_of_the_reference_figure():
    # §8 read backwards: to risk exactly 50 USC with entry 4035 / SL 4030 on
    # XAUUSDc, the size must be the 0.10 lot the reference figure used.
    v = volume_for_risk(4035.000, 4030.000, _TICK_SIZE, _TICK_VALUE, 50.0)
    assert v is not None
    assert abs(v - 0.10) < 1e-9
    # And it round-trips: sizing then measuring gives the budget back.
    assert abs(risk_amount(4035.000, 4030.000, _TICK_SIZE, _TICK_VALUE, v) - 50.0) < 1e-9


def test_volume_for_risk_direction_does_not_matter():
    v = volume_for_risk(4030.000, 4035.000, _TICK_SIZE, _TICK_VALUE, 50.0)
    assert v is not None and abs(v - 0.10) < 1e-9


def test_volume_for_risk_propagates_every_unknown():
    assert volume_for_risk(None, 4030.0, _TICK_SIZE, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, None, _TICK_SIZE, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, None, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, None, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, None) is None


def test_volume_for_risk_refuses_a_zero_distance():
    # entry == sl is an infinite size, not a large one. Never a ZeroDivision,
    # never inf — None, the same as every other unknown (Trap 6).
    assert volume_for_risk(4035.0, 4035.0, _TICK_SIZE, _TICK_VALUE, 50.0) is None


def test_volume_for_risk_refuses_malformed_specs():
    assert volume_for_risk(4035.0, 4030.0, 0.0, _TICK_VALUE, 50.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, 0.0, 50.0) is None


def test_volume_for_risk_refuses_a_non_positive_budget():
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, 0.0) is None
    assert volume_for_risk(4035.0, 4030.0, _TICK_SIZE, _TICK_VALUE, -5.0) is None


def test_floor_to_step_rounds_down_never_up():
    # 0.137 lot at a 0.01 step is 0.13, not 0.14 — rounding up would take more
    # risk than the human budgeted.
    assert abs(floor_to_step(0.137, 0.01) - 0.13) < 1e-9
    assert abs(floor_to_step(0.999, 0.01) - 0.99) < 1e-9


def test_floor_to_step_is_exact_on_exact_multiples():
    # The IEEE754 trap `commands._is_multiple` documents, in floor form:
    # 0.03 / 0.01 is 2.9999999999999996, so a raw floor() drops a whole step.
    assert abs(floor_to_step(0.03, 0.01) - 0.03) < 1e-9
    assert abs(floor_to_step(0.07, 0.01) - 0.07) < 1e-9
    assert abs(floor_to_step(1.0, 0.01) - 1.0) < 1e-9


def test_floor_to_step_below_one_step_is_zero_not_none():
    # 0.004 at a 0.01 step is a real answer: zero lots. The CALLER decides that
    # zero is unusable (it is below volume_min); this function does not guess.
    assert abs(floor_to_step(0.004, 0.01) - 0.0) < 1e-9


def test_floor_to_step_propagates_unknowns():
    assert floor_to_step(None, 0.01) is None
    assert floor_to_step(0.13, None) is None
    assert floor_to_step(0.13, 0.0) is None


def test_direction_for_sl_reads_the_side():
    # The whole gesture: an SL below the price means the human is buying.
    assert direction_for_sl(4035.0, 4030.0) == "buy"
    assert direction_for_sl(4035.0, 4040.0) == "sell"


def test_direction_for_sl_has_no_answer_at_the_price():
    assert direction_for_sl(4035.0, 4035.0) is None
    assert direction_for_sl(None, 4030.0) is None
    assert direction_for_sl(4035.0, None) is None
