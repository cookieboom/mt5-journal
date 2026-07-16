"""Symbol normalisation — the one place a broker suffix is stripped.

CLAUDE.md Hard rule 11 / docs trap 12: `symbol` is stored verbatim ('XAUUSDc')
and queried against MT5; `symbol_base` ('XAUUSD') is what analytics GROUP BY.
This function is the sole bridge between them.

The suffix set is deliberately just {"c"}. This broker uses only `c` (verified:
the traded symbols are XAUUSDc, BTCUSDc, EURUSDc, and the unsuffixed symbols do
not exist on the server). Other brokers use `.m`, `.raw`, `_ecn`, `#`, `-` — we
do NOT strip those. Every extra rule is another way to silently mangle a symbol
for zero present benefit. Widen the set only when a broker that uses one actually
appears.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# The only suffix in use on this account. See module docstring before adding.
_SUFFIXES = ("c",)

# Never strip so much that a real ticker is left mangled (e.g. don't turn a
# 3-letter thing into a 2-letter thing). Bases like EUR/USD legs are >= 3 chars.
_MIN_BASE_LEN = 3

_warned: set[str] = set()


def to_base(symbol: str) -> str:
    """Return the normalised base symbol.

    'XAUUSDc' -> 'XAUUSD', 'EURUSDc' -> 'EURUSD', 'BTCUSDc' -> 'BTCUSD'.
    'USDCAD'  -> 'USDCAD' (unchanged — must not strip a real trailing letter).

    No known suffix matches -> return verbatim, and warn once per unseen symbol.
    """
    for suffix in _SUFFIXES:
        if symbol.endswith(suffix) and len(symbol) - len(suffix) >= _MIN_BASE_LEN:
            return symbol[: -len(suffix)]

    if symbol not in _warned:
        log.warning("to_base: no known suffix for %r; returning verbatim", symbol)
        _warned.add(symbol)
    return symbol
