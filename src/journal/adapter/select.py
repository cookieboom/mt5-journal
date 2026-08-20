"""Picks which `MT5Client` backend to use, automatically.

Windows + the official `MetaTrader5` package importable and initializable ->
`adapter/native.py`. Everything else (macOS, Linux, or a Windows host
without a running/logged-in terminal) -> `adapter/live.py`'s Docker bridge,
exactly as before this module existed.

Imports of both backends are lazy (matches the existing pattern in
`cli.py`), so importing THIS module never requires either MT5 package to be
installed.
"""

from __future__ import annotations

import logging
import sys

from .base import EnumMismatch, MT5Client

log = logging.getLogger(__name__)


def get_client() -> MT5Client:
    if sys.platform == "win32":
        try:
            from .native import NativeMT5Client
        except ImportError as exc:
            log.warning(
                "native MetaTrader5 package unavailable (%s); falling back to the Docker bridge",
                exc,
            )
        else:
            try:
                client = NativeMT5Client()
            except EnumMismatch:
                raise  # a rule-12 violation is a correctness bug, not an availability failure — never silently fall back
            except Exception as exc:
                log.warning(
                    "native adapter unavailable (%s); falling back to the Docker bridge",
                    exc,
                )
            else:
                log.info("adapter: native MetaTrader5 (Windows)")
                return client

    from .live import LiveMT5Client

    client = LiveMT5Client()
    log.info("adapter: siliconmetatrader5 bridge (Docker)")
    return client
