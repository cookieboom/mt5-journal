"""MAE/MFE — how far price ran against you and for you during a trade (M5).

`compute_excursion()` is pure: no DB, no bridge (CLAUDE.md rule 7) — it takes
plain `(time_msc, low, high)` tuples and is fixture-testable with hand-built
data. The caller (`domain/reconstruct.py::_fill_excursions`) owns fetching
those rows, scoped to ONE trade's own symbol + its own timeframe + its own
`window_for(open, close, tf)` bounds — never a bulk cross-trade scan. That
scoping matters: the central `candles` table pools every trade's window on a
symbol (schema.sql: "Dedupes across trades on the same symbol/day"), and two
overlapping trades of different durations can legitimately sit at different
timeframes (a hedging account, CLAUDE.md line 26). A global "nearest
preceding row anywhere" scan could silently pick up a different, disjoint
trade's cluster or a coarser bar's wider high/low; a query scoped to this
trade's own [open,close] window at its own TF cannot.
"""

from __future__ import annotations


def compute_excursion(
    rows: list[tuple[int, float, float]],
    open_time_msc: int,
    close_time_msc: int,
    open_price: float,
    direction: str,
) -> tuple[float | None, float | None]:
    """rows: (time_msc, low, high) tuples, already scoped to one trade's own
    padded candle window (see module docstring), sorted by time_msc ascending.

    Uses COVERING-BAR semantics, mirroring `render/chart.py`'s
    `_nearest_bar_index` (the same problem already solved once for chart
    marker placement): the excursion window is the bar *containing*
    `open_time_msc` through the bar *containing* `close_time_msc` -- found by
    scanning for the last row with `time_msc <=` the target. This is
    deliberately NOT a filter requiring a bar's open to fall strictly inside
    `[open_time_msc, close_time_msc]` -- `candles.time_msc` is a bar's OPEN
    time, and a fast trade (11/68 measured trades are sub-M1, min 1s) rarely
    contains a bar-open boundary at all; a naive in-range filter would return
    `(None, None)` for most of them despite full candle coverage.

    Returns `(mae, mfe)` as non-negative PRICE distances (never money -- see
    reconstruct.py's `_fill_excursions` for how `mae_r`/`mfe_r` are derived
    from these as pure ratios), or `(None, None)` if every row is AFTER
    `open_time_msc` (no real coverage -- e.g. `journal candles` was never run
    for this trade).

    Approximate at the edges, not exact to the trade's precise instants: the
    covering bars may extend slightly before `open_time_msc` / after
    `close_time_msc`, since trade windows rarely align to bar boundaries. Any
    single bar's `high`/`low` IS exact for that bar's own period, at any
    timeframe -- the approximation is only in how much of the bar containing
    entry/exit falls outside the trade's own instants.
    """
    if not rows:
        return None, None

    open_idx: int | None = None
    for i, (t, _, _) in enumerate(rows):
        if t <= open_time_msc:
            open_idx = i
        else:
            break
    if open_idx is None:
        # every row is AFTER open_time_msc -- no real coverage, never guess.
        return None, None

    close_idx = open_idx
    for i in range(open_idx, len(rows)):
        if rows[i][0] <= close_time_msc:
            close_idx = i
        else:
            break

    relevant = rows[open_idx : close_idx + 1]
    min_low = min(lo for _, lo, _ in relevant)
    max_high = max(hi for _, _, hi in relevant)

    # Floor at 0.0, never negative: a trade that immediately ran favorable
    # (the covering bar never dipped below/rose above open_price on its own
    # side) has a genuine adverse excursion of zero -- not an error.
    if direction == "buy":
        mae = max(0.0, open_price - min_low)
        mfe = max(0.0, max_high - open_price)
    else:
        mae = max(0.0, max_high - open_price)
        mfe = max(0.0, open_price - min_low)
    return mae, mfe
