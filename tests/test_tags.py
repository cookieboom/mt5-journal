"""M6 auto-tag computation — `domain/tags.py`.

Written before the implementation (CLAUDE.md rule 7). `compute_auto_tags` is a
PURE function over a single closed `Trade`: structural facts only (duration,
calendar dates, weekday) plus caller-supplied outlier thresholds. No DB, no
threshold computation inside — the caller (rebuild) owns the §9 gate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from journal.domain.reconstruct import Trade
from journal.domain.tags import compute_auto_tags


def _ms(y, mo, d, h=0, mi=0) -> int:
    """Epoch ms (UTC) at a fixed UTC wall-clock — for placing a trade's open/close
    on a known calendar date / weekday."""
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def _trade(
    *, duration_s=None, open_time_msc=None, close_time_msc=None, net_profit=0.0,
    status="closed", direction="buy", position_id=1,
) -> Trade:
    if open_time_msc is None:
        open_time_msc = _ms(2026, 1, 14, 12)  # a Wednesday noon by default
    return Trade(
        account_login=0, position_id=position_id, symbol="BTCUSDc",
        symbol_base="BTCUSD", direction=direction, status=status,
        open_time_msc=open_time_msc, volume=0.1, open_price=100.0,
        net_profit=net_profit, commission=0.0, swap=0.0, profit_gross=net_profit,
        deal_count=2, close_time_msc=close_time_msc, duration_s=duration_s,
    )


# --------------------------------------------------------------- sub-1min


def test_sub_1min_tagged_below_60s():
    assert "sub-1min" in compute_auto_tags(_trade(duration_s=59))


def test_sub_1min_not_tagged_at_60s():
    assert "sub-1min" not in compute_auto_tags(_trade(duration_s=60))


def test_sub_1min_not_tagged_when_duration_none():
    assert "sub-1min" not in compute_auto_tags(_trade(duration_s=None))


# ------------------------------------------------------------ held-overnight


def test_held_overnight_tagged_across_utc_dates():
    # open 2026-01-14 23:30 UTC, close 2026-01-15 00:10 UTC — different UTC dates.
    t = _trade(
        open_time_msc=_ms(2026, 1, 14, 23, 30),
        close_time_msc=_ms(2026, 1, 15, 0, 10),
        duration_s=40 * 60,
    )
    assert "held-overnight" in compute_auto_tags(t)


def test_held_overnight_not_tagged_same_utc_date():
    # a multi-hour trade that opens and closes on the SAME UTC date.
    t = _trade(
        open_time_msc=_ms(2026, 1, 14, 8, 0),
        close_time_msc=_ms(2026, 1, 14, 20, 0),
        duration_s=12 * 3600,
    )
    assert "held-overnight" not in compute_auto_tags(t)


# ------------------------------------------------------------------ weekend


def test_weekend_tagged_on_saturday_open():
    # 2026-01-17 is a Saturday.
    t = _trade(open_time_msc=_ms(2026, 1, 17, 10))
    assert "weekend" in compute_auto_tags(t)


def test_weekend_tagged_on_sunday_open():
    # 2026-01-18 is a Sunday.
    t = _trade(open_time_msc=_ms(2026, 1, 18, 10))
    assert "weekend" in compute_auto_tags(t)


def test_weekend_not_tagged_on_wednesday_open():
    # 2026-01-14 is a Wednesday.
    t = _trade(open_time_msc=_ms(2026, 1, 14, 10))
    assert "weekend" not in compute_auto_tags(t)


# ----------------------------------------------------------- big-win/big-loss


def test_big_win_boundary_inclusive():
    t = _trade(net_profit=100.0)
    assert "big-win" in compute_auto_tags(t, big_win_threshold=100.0)  # >= boundary
    assert "big-win" in compute_auto_tags(t, big_win_threshold=99.0)
    assert "big-win" not in compute_auto_tags(t, big_win_threshold=101.0)


def test_big_loss_boundary_inclusive():
    t = _trade(net_profit=-100.0)
    assert "big-loss" in compute_auto_tags(t, big_loss_threshold=-100.0)  # <= boundary
    assert "big-loss" in compute_auto_tags(t, big_loss_threshold=-99.0)
    assert "big-loss" not in compute_auto_tags(t, big_loss_threshold=-101.0)


def test_no_outlier_tags_when_thresholds_none():
    # The §9 gate: on a sub-20 account the caller passes None, so no outlier tag is
    # ever applied regardless of net_profit magnitude.
    big = _trade(net_profit=1_000_000.0)
    small = _trade(net_profit=-1_000_000.0)
    assert "big-win" not in compute_auto_tags(big)
    assert "big-loss" not in compute_auto_tags(small)


def test_returns_a_set_no_duplicates():
    # a sub-1min weekend trade earns multiple tags — a set, never dupes.
    t = _trade(open_time_msc=_ms(2026, 1, 17, 10), duration_s=30)
    tags = compute_auto_tags(t)
    assert isinstance(tags, set)
    assert {"sub-1min", "weekend"} <= tags
