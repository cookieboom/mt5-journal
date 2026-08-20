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

from .base import MT5Client

log = logging.getLogger(__name__)


def get_client() -> MT5Client:
    if sys.platform == "win32":
        try:
            from .native import NativeMT5Client

            client = NativeMT5Client()
            log.info("adapter: native MetaTrader5 (Windows)")
            return client
        except Exception as exc:
            log.warning(
                "native adapter unavailable (%s); falling back to the Docker bridge",
                exc,
            )

    from .live import LiveMT5Client

    client = LiveMT5Client()
    log.info("adapter: siliconmetatrader5 bridge (Docker)")
    return client
