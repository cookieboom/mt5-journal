"""adapter/select.py — see docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md.

get_client() itself needs a real backend to fully exercise (no bridge and no
Windows terminal are available in this test run), so these tests cover what
IS checkable here: both concrete clients satisfy the MT5Client Protocol
shape, and select.py's platform-branch logic is exercised with the real
imports patched out.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Stub MetaTrader5 before patch() tries to import journal.adapter.native
# (which does `import MetaTrader5 as mt5` at module scope)
sys.modules["MetaTrader5"] = MagicMock()

import pytest

from journal.adapter.base import MT5Client


def test_fake_client_satisfies_mt5client_protocol():
    # The one backend guaranteed importable and constructible everywhere —
    # anchors the contract check even where neither real backend is available.
    from journal.adapter.fake import FakeMT5Client

    assert isinstance(FakeMT5Client(fixtures_dir="tests/fixtures"), MT5Client)


def test_get_client_uses_bridge_on_non_windows():
    from journal.adapter import select

    fake_bridge_client = object()
    with patch.object(sys, "platform", "darwin"):
        with patch("journal.adapter.live.LiveMT5Client", return_value=fake_bridge_client):
            assert select.get_client() is fake_bridge_client


def test_get_client_falls_back_to_bridge_when_native_init_fails():
    from journal.adapter import select

    fake_bridge_client = object()
    with patch.object(sys, "platform", "win32"):
        with patch("journal.adapter.native.NativeMT5Client", side_effect=RuntimeError("no terminal")):
            with patch("journal.adapter.live.LiveMT5Client", return_value=fake_bridge_client):
                assert select.get_client() is fake_bridge_client


def test_get_client_uses_native_on_windows_when_available():
    from journal.adapter import select

    fake_native_client = object()
    with patch.object(sys, "platform", "win32"):
        with patch("journal.adapter.native.NativeMT5Client", return_value=fake_native_client):
            assert select.get_client() is fake_native_client
