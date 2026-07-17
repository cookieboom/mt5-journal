"""Risk in account currency — the one number every R-multiple rests on.

`risk_amount` is NOT `|open - sl| * volume` (Trap 11). It needs per-symbol contract
specs, and its unit is always `accounts.currency` (USC on this account), never the
symbol's `currency_profit` (Trap 14). See docs/mt5-deal-model.md §8 for the hand-
verified reference figure (4035/4030, 0.10 lot XAUUSDc -> 50 USC).

Every unknown propagates to `None`. NULL is never coerced to 0: a 0 risk would make
`net_profit / risk` infinite and poison every downstream statistic (Trap 6/13).
"""

from __future__ import annotations


def risk_amount(
    open_price: float | None,
    sl_initial: float | None,
    tick_size: float | None,
    tick_value: float | None,
    volume: float | None,
) -> float | None:
    """`(|open_price - sl_initial| / tick_size) * tick_value * volume`, in account
    currency. Returns `None` if any input is unknown (Trap 6: unknown SL, or a symbol
    with no specs) or `tick_size` is 0 — never a coerced 0 and never a ZeroDivision."""
    if (
        open_price is None
        or sl_initial is None
        or tick_size is None
        or tick_value is None
        or volume is None
    ):
        return None
    if tick_size == 0:  # malformed spec — cannot compute, do not guess
        return None
    ticks = abs(open_price - sl_initial) / tick_size
    return ticks * tick_value * volume
