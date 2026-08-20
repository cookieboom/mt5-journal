"""adapter/select.py — see docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md.

get_client() itself needs a real backend to fully exercise (no bridge and no
Windows terminal are available in this test run), so these tests cover
select.py's platform-branch logic with the real backend imports patched out.
The MT5Client-Protocol contract check itself already exists at
tests/test_adapter.py:28 (`FakeMT5Client`) — not duplicated here.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# journal.adapter.native does `import MetaTrader5 as mt5` at module scope (the
# official package, Windows-only). unittest.mock.patch() must import that
# module to resolve "journal.adapter.native.NativeMT5Client" as a patch
# target, which would otherwise raise ModuleNotFoundError on this dev
# machine (and in CI, and on any non-Windows box). Stub it in sys.modules
# ONLY for the two tests that patch into native.py, and only for the
# duration of that one `with` block via patch.dict — an unscoped,
# module-level `sys.modules["MetaTrader5"] = ...` would leak for the rest of
# the pytest session and silently defeat tests/test_native_adapter.py's
# `pytest.importorskip("MetaTrader5")` guard for every test collected after
# this file.
_stub_mt5_package = patch.dict(sys.modules, {"MetaTrader5": MagicMock()})


def test_get_client_uses_bridge_on_non_windows():
    from journal.adapter import select

    fake_bridge_client = object()
    with patch.object(sys, "platform", "darwin"):
        with patch("journal.adapter.live.LiveMT5Client", return_value=fake_bridge_client):
            assert select.get_client() is fake_bridge_client


def test_get_client_falls_back_to_bridge_when_native_init_fails():
    from journal.adapter import select

    fake_bridge_client = object()
    with _stub_mt5_package, patch.object(sys, "platform", "win32"):
        with patch("journal.adapter.native.NativeMT5Client", side_effect=RuntimeError("no terminal")):
            with patch("journal.adapter.live.LiveMT5Client", return_value=fake_bridge_client):
                assert select.get_client() is fake_bridge_client


def test_get_client_uses_native_on_windows_when_available():
    from journal.adapter import select

    fake_native_client = object()
    with _stub_mt5_package, patch.object(sys, "platform", "win32"):
        with patch("journal.adapter.native.NativeMT5Client", return_value=fake_native_client):
            assert select.get_client() is fake_native_client
