"""Adapter-backend Protocol conformance — see
docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md.

Runs unconditionally (no `importorskip`, no Windows terminal, no Docker
bridge needed): both checks below only need the CLASS to exist, not a live
connection, so `MetaTrader5` is stubbed into `sys.modules` for the duration
of the native import — the same technique `tests/test_adapter_select.py`
uses for the same reason. `LiveMT5Client` needs no stubbing: `live.py`
imports `siliconmetatrader5`, which has no platform marker in
pyproject.toml and is installed on every machine including this one.

`issubclass(cls, MT5Client)` on a `runtime_checkable` Protocol checks that
every Protocol method is present on the class — a real conformance check,
unlike a hardcoded `hasattr` list (misses new Protocol methods) or
`issubclass(cls, object)` (true of every class). Functional correctness
against a real terminal/bridge is a manual follow-up, not this test's job.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from journal.adapter.base import MT5Client


def test_native_client_satisfies_mt5client_protocol():
    with patch.dict(sys.modules, {"MetaTrader5": MagicMock()}):
        from journal.adapter.native import NativeMT5Client

    assert issubclass(NativeMT5Client, MT5Client)


def test_live_client_satisfies_mt5client_protocol():
    from journal.adapter.live import LiveMT5Client

    assert issubclass(LiveMT5Client, MT5Client)
