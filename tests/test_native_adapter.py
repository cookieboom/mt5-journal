"""Native (Windows) MT5 adapter — see docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md.

No Windows machine is available to this repo's test run, so this only
verifies what's checkable without a live terminal: the module imports (or is
skipped where the `MetaTrader5` package isn't installed) and the class shape
matches the `MT5Client` Protocol. Functional correctness against a real
terminal is a manual follow-up (`journal doctor` on Windows).
"""

from __future__ import annotations

import pytest

pytest.importorskip("MetaTrader5")


def test_native_client_satisfies_mt5client_protocol():
    from journal.adapter.base import MT5Client
    from journal.adapter.native import NativeMT5Client

    assert issubclass(NativeMT5Client, object)
    for method in (
        "account_info", "symbol_info", "symbol_info_tick", "symbols_get",
        "copy_rates_range", "history_deals_get", "history_orders_get",
        "positions_get", "order_check", "order_send",
    ):
        assert hasattr(NativeMT5Client, method), f"missing {method}"
    # runtime_checkable Protocol: an unconnected instance still can't be
    # built without a terminal, so this checks the class shape, not an
    # instance — an isinstance check on a real instance is Task 3's contract
    # test, run against both backends together.
