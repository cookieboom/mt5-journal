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


def volume_for_risk(
    entry_price: float | None,
    sl: float | None,
    tick_size: float | None,
    tick_value: float | None,
    risk: float | None,
) -> float | None:
    """Lots that put exactly `risk` (account currency) at stake between
    `entry_price` and `sl` — the inverse of `risk_amount`.

    `risk / ((|entry - sl| / tick_size) * tick_value)`. Returns `None` for every
    unknown, for a malformed spec, for a non-positive budget, and for a zero
    distance (an infinite size is not a large one). Never raises, never returns
    `inf`: a coerced number here becomes a real order.
    """
    if (
        entry_price is None
        or sl is None
        or tick_size is None
        or tick_value is None
        or risk is None
    ):
        return None
    if tick_size <= 0 or tick_value <= 0 or risk <= 0:
        return None
    distance = abs(entry_price - sl)
    if distance < 1e-9:  # rule 5: tolerance, never `== 0`
        return None
    risk_per_lot = (distance / tick_size) * tick_value
    if risk_per_lot <= 0:
        return None
    return risk / risk_per_lot


def floor_to_step(volume: float | None, step: float | None) -> float | None:
    """Largest whole number of `step`s not exceeding `volume`.

    Rounds DOWN so the realised risk is never larger than the budget. `None` for
    unknowns and for a non-positive step.

    NOT `math.floor(volume / step) * step`: in IEEE754 `0.03 / 0.01` is
    2.9999999999999996, so a raw floor turns a perfectly ordinary 0.03 lot into
    0.02. The same trap `commands._is_multiple` documents. Snap to the nearest
    step first when the difference is within tolerance, and only then floor.
    """
    if volume is None or step is None:
        return None
    if step <= 0:
        return None
    n = volume / step
    nearest = round(n)
    if abs(n - nearest) < max(1e-9, abs(n) * 1e-9):
        n = nearest
    else:
        n = float(int(n))       # truncate toward zero; volume is never negative
    return max(0.0, n * step)


def direction_for_sl(entry_price: float | None, sl: float | None) -> str | None:
    """Which side the human is taking, read from where they put the stop.

    An SL BELOW the price is a buy's stop; ABOVE it is a sell's. At the price
    (or with anything unknown) there is no answer — `None`, never a default.
    This describes the human's own gesture; it does not suggest one (rule 9).
    """
    if entry_price is None or sl is None:
        return None
    if abs(entry_price - sl) < 1e-9:
        return None
    return "buy" if sl < entry_price else "sell"
