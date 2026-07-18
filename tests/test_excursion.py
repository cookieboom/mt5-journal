"""M5 MAE/MFE — `compute_excursion()`, pure and fixture-tested (CLAUDE.md
rule 7). The caller (`domain/reconstruct.py::_fill_excursions`) owns scoping
rows to one trade's own symbol/timeframe/window; this module only tests the
covering-bar scan and direction-aware distance math in isolation.
"""

from __future__ import annotations

from journal.domain.excursion import compute_excursion


def test_buy_direction():
    # entry 4000, price dips to 3990 then rallies to 4020.
    rows = [
        (1000, 3995, 4005),
        (1060, 3990, 4000),
        (1120, 4000, 4020),
        (1180, 4010, 4015),
    ]
    mae, mfe = compute_excursion(rows, 1000, 1180, 4000.0, "buy")
    assert abs(mae - 10.0) < 1e-9  # 4000 - 3990
    assert abs(mfe - 20.0) < 1e-9  # 4020 - 4000


def test_sell_direction_swaps_mae_and_mfe():
    # SAME bars as the buy case -- a short's adverse/favorable are reversed:
    # price rising against you is adverse, price falling is favorable.
    rows = [
        (1000, 3995, 4005),
        (1060, 3990, 4000),
        (1120, 4000, 4020),
        (1180, 4010, 4015),
    ]
    mae, mfe = compute_excursion(rows, 1000, 1180, 4000.0, "sell")
    assert abs(mae - 20.0) < 1e-9  # 4020 - 4000 (price rose against a short)
    assert abs(mfe - 10.0) < 1e-9  # 4000 - 3990 (price fell in the short's favor)


def test_sub_bar_trade_returns_real_numbers_not_none():
    # THE regression guard: candles.time_msc is a bar's OPEN time. A fast
    # trade (11/68 measured trades are sub-M1, min 1s) rarely contains a
    # bar-open boundary at all -- a naive "bar open falls inside [open,close]"
    # filter would wrongly return (None, None) here despite full coverage.
    # Covering-bar semantics must find the ONE bar containing both instants.
    rows = [(900, 3999.0, 4001.0)]
    mae, mfe = compute_excursion(rows, 950, 951, 4000.0, "buy")
    assert mae is not None and mfe is not None
    assert abs(mae - 1.0) < 1e-9  # 4000 - 3999
    assert abs(mfe - 1.0) < 1e-9  # 4001 - 4000


def test_floors_at_zero_never_negative():
    # Price only ran favorable (buy) -- adverse excursion is genuinely zero,
    # not a small negative number and not an error.
    rows = [(1000, 4000.0, 4005.0), (1060, 4003.0, 4010.0)]
    mae, mfe = compute_excursion(rows, 1000, 1060, 4000.0, "buy")
    assert mae == 0.0
    assert mfe > 0.0


def test_empty_rows_is_no_coverage():
    assert compute_excursion([], 1000, 1180, 4000.0, "buy") == (None, None)


def test_every_row_after_open_time_is_no_coverage():
    # A genuine coverage gap: nothing was fetched at/before entry. Must not
    # guess by using the earliest available (irrelevant) row.
    rows = [(2000, 4000.0, 4001.0)]
    assert compute_excursion(rows, 1000, 1180, 4000.0, "buy") == (None, None)


def test_padding_bars_outside_the_trade_window_are_excluded():
    # window_for() pads generously before/after a trade -- a huge spike in the
    # padding must NOT be counted as part of THIS trade's excursion.
    rows = [
        (700, 3000.0, 3001.0),   # far pre-entry padding: a huge spike -- excluded
        (1000, 3999.0, 4001.0),  # covers open_time_msc=1000
        (1060, 3998.0, 4002.0),  # covers close_time_msc=1060
        (1400, 5000.0, 5001.0),  # far post-exit padding: another huge spike -- excluded
    ]
    mae, mfe = compute_excursion(rows, 1000, 1060, 4000.0, "buy")
    assert abs(mae - 2.0) < 1e-9  # 4000 - 3998, NOT 4000-3000=1000
    assert abs(mfe - 2.0) < 1e-9  # 4002 - 4000, NOT 5000-4000=1000


def test_rows_must_be_sorted_ascending_by_caller():
    # Documents the contract: compute_excursion trusts the caller's ordering
    # (reconstruct.py's SQL query does `ORDER BY time_msc`). Out-of-order
    # input is out of contract -- not a case this test asserts recovery from,
    # just confirms in-order input behaves as documented.
    rows = [(1000, 3999.0, 4001.0), (1060, 3998.0, 4002.0)]
    mae, mfe = compute_excursion(rows, 1000, 1060, 4000.0, "buy")
    assert mae is not None and mfe is not None
