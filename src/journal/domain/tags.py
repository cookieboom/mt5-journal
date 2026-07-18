"""M6 auto-tagging — the STRUCTURAL half of the tag system.

`compute_auto_tags` is a PURE function over one closed `Trade`. Every tag it
returns is a well-defined structural fact about that single trade — a duration,
two calendar dates, a weekday, or a net_profit compared to a threshold the
CALLER supplies. It computes NO thresholds itself and reads NO DB: the §9
money-magnitude discipline (outlier tags only make sense against a large enough
sample) lives in the caller (`reconstruct._fill_auto_tags`), which passes
`big_win_threshold`/`big_loss_threshold` as the account's decile net_profit only
when `n_closed >= _MIN_N`, and `None` otherwise. With `None` thresholds, no
`big-win`/`big-loss` is ever applied — so a thin account never labels an outlier
against a sample too small to define one.

All time reasoning is TRUE-UTC via `datetime.fromtimestamp(ms/1000,
tz=timezone.utc)` — never naive, never `utcfromtimestamp` (CLAUDE.md rule 3).
Deal/order times on this account are already UTC (server_utc_offset_s = 0), so
no offset correction is needed here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime cycle: reconstruct imports this module.
    from .reconstruct import Trade


def _utc(ms: int) -> datetime:
    """Epoch ms -> aware UTC datetime. The one conversion point, so the rule-3
    'never naive / never utcfromtimestamp' discipline is enforced in a single place."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def compute_auto_tags(
    trade: Trade,
    *,
    big_win_threshold: float | None = None,
    big_loss_threshold: float | None = None,
) -> set[str]:
    """The auto-tags a single closed `trade` earns. Structural facts only:

      sub-1min        duration under 60 seconds
      held-overnight  open and close fall on different UTC calendar dates
      weekend         opened on a Saturday/Sunday UTC (only BTC trades on the
                      weekend on this account, docs §7)
      big-win         net_profit >= big_win_threshold  (caller-supplied, §9-gated)
      big-loss        net_profit <= big_loss_threshold (caller-supplied, §9-gated)

    Returns a `set` (never duplicates). Intended for CLOSED trades — the caller
    skips open/partial ones, where duration/close date are undefined or a
    partial realised P&L would make an outlier tag a lie.
    """
    tags: set[str] = set()

    if trade.duration_s is not None and trade.duration_s < 60:
        tags.add("sub-1min")

    open_dt = _utc(trade.open_time_msc)

    if trade.close_time_msc is not None:
        if open_dt.date() != _utc(trade.close_time_msc).date():
            tags.add("held-overnight")

    if open_dt.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        tags.add("weekend")

    if big_win_threshold is not None and trade.net_profit >= big_win_threshold:
        tags.add("big-win")
    if big_loss_threshold is not None and trade.net_profit <= big_loss_threshold:
        tags.add("big-loss")

    return tags
