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
