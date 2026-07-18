"""M6.1 weekly report — `build_weekly()` + `render_weekly_md()`.

A weekly review is `journal report` scoped to one ISO week (Mon–Sun UTC),
attributing each trade to the week it CLOSED in (realized P&L). Aggregate money
stats are gated by §9's n≥20 exactly as the account report gates its buckets —
a week rarely clears the gate, so most weekly rates read n/a, by design. The
raw counts, the realized net total (a sum, not an average), and the per-trade
annotations/tags are the real content of the review.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from journal.analytics.weekly import (
    build_weekly,
    iso_week_bounds_ms,
    last_complete_iso_week,
)
from journal.annotate import add_tag, set_annotation
from journal.render.weekly import render_weekly_md
from journal.store.db import connect

_LOGIN = 0
_WEEK = (2026, 28)  # an arbitrary fixed ISO week for the fixtures


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _monday(iso_year: int, iso_week: int) -> datetime:
    return datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=timezone.utc)


def _conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    c.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, 'USC', 1)",
        (_LOGIN,),
    )
    c.commit()
    return c


def _seed_trade(conn, pid, *, close_dt, net_profit=0.0, open_dt=None,
                magic=None, status="closed", symbol="XAUUSDc"):
    open_dt = open_dt or close_dt
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, volume, open_price, "
        "net_profit, magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, 'buy', ?, ?, ?, 0.1, 4000.0, ?, ?, 2, 1)",
        (_LOGIN, pid, symbol, symbol[:-1], status, _ms(open_dt), _ms(close_dt),
         net_profit, magic),
    )
    conn.commit()


# ------------------------------------------------------------ week math


def test_iso_week_bounds_are_half_open_monday_to_monday(tmp_path):
    start_ms, end_ms = iso_week_bounds_ms(*_WEEK)
    assert start_ms == _ms(_monday(*_WEEK))
    assert end_ms == _ms(_monday(*_WEEK) + timedelta(days=7))


def test_last_complete_iso_week_steps_back_one_week():
    # A Wednesday inside 2026-W28 -> the last COMPLETE week is W27.
    now = _monday(2026, 28) + timedelta(days=2, hours=10)
    assert last_complete_iso_week(now) == (2026, 27)


# ---------------------------------------------------- attribution window


def test_attribution_is_by_close_time_and_half_open(tmp_path):
    conn = _conn(tmp_path)
    start = _monday(*_WEEK)
    end = start + timedelta(days=7)
    # closes at the very last instant of the week -> IN
    _seed_trade(conn, 1, close_dt=end - timedelta(milliseconds=1), net_profit=5.0)
    # closes exactly at next Monday 00:00 -> the NEXT week, excluded
    _seed_trade(conn, 2, close_dt=end, net_profit=99.0)
    # closed in a totally different week -> excluded
    _seed_trade(conn, 3, close_dt=start - timedelta(days=3), net_profit=42.0)

    r = build_weekly(conn, *_WEEK)
    assert r.n_closed == 1
    assert abs(r.net_total - 5.0) < 1e-9


def test_open_trades_never_counted(tmp_path):
    conn = _conn(tmp_path)
    mid = _monday(*_WEEK) + timedelta(days=2)
    _seed_trade(conn, 1, close_dt=mid, net_profit=3.0)
    _seed_trade(conn, 2, close_dt=mid, net_profit=0.0, status="open")
    r = build_weekly(conn, *_WEEK)
    assert r.n_closed == 1


# --------------------------------------------------------- gating (§9)


def test_week_money_stats_gated_below_n20_but_counts_and_total_shown(tmp_path):
    conn = _conn(tmp_path)
    mid = _monday(*_WEEK) + timedelta(days=2)
    for pid in range(1, 6):  # 5 trades, all wins -> below the n>=20 gate
        _seed_trade(conn, pid, close_dt=mid + timedelta(minutes=pid), net_profit=10.0)

    r = build_weekly(conn, *_WEEK)
    assert r.n_closed == 5 and r.n_wins == 5           # raw counts always shown
    assert abs(r.net_total - 50.0) < 1e-9              # realized total is a sum, shown
    assert r.win_rate is None                          # a rate over n<20 -> withheld
    assert r.expectancy is None and r.avg_win is None  # averages -> withheld


def test_week_money_stats_shown_at_or_above_n20(tmp_path):
    conn = _conn(tmp_path)
    mid = _monday(*_WEEK) + timedelta(days=2)
    for pid in range(1, 21):  # exactly 20 -> clears the gate
        _seed_trade(conn, pid, close_dt=mid + timedelta(minutes=pid), net_profit=10.0)

    r = build_weekly(conn, *_WEEK)
    assert r.n_closed == 20
    assert r.win_rate is not None and abs(r.win_rate - 1.0) < 1e-9
    assert r.expectancy is not None and abs(r.expectancy - 10.0) < 1e-9


# ----------------------------------------------------- human layer (notes)


def test_notes_lists_trades_with_annotation_or_manual_tag(tmp_path):
    conn = _conn(tmp_path)
    mid = _monday(*_WEEK) + timedelta(days=2)
    _seed_trade(conn, 100, close_dt=mid, net_profit=8.0)   # annotated
    _seed_trade(conn, 200, close_dt=mid, net_profit=-4.0)  # manual-tagged
    _seed_trade(conn, 300, close_dt=mid, net_profit=1.0)   # nothing -> not in notes
    set_annotation(conn, 100, setup="breakout", confidence=4, notes="clean entry")
    add_tag(conn, 200, "revenge")

    r = build_weekly(conn, *_WEEK)
    noted = {n.position_id for n in r.notes}
    assert noted == {100, 200}
    ann = next(n for n in r.notes if n.position_id == 100)
    assert ann.setup == "breakout" and ann.notes == "clean entry"
    tagged = next(n for n in r.notes if n.position_id == 200)
    assert "revenge" in tagged.tags


# --------------------------------------------------------------- render


def test_render_weekly_md_contains_every_section(tmp_path):
    conn = _conn(tmp_path)
    mid = _monday(*_WEEK) + timedelta(days=2)
    _seed_trade(conn, 100, close_dt=mid, net_profit=8.0, magic=555)  # EA
    _seed_trade(conn, 200, close_dt=mid, net_profit=-4.0)            # discretionary
    set_annotation(conn, 100, setup="breakout", notes="clean entry")

    md = render_weekly_md(build_weekly(conn, *_WEEK))
    assert "2026-W28" in md
    assert "Asian" in md and "London" in md          # session buckets present
    assert "EA" in md and "Discretionary" in md       # source buckets present
    assert "breakout" in md and "clean entry" in md   # the annotation surfaced
    assert "USC" in md                                # money carries its currency


def test_render_empty_week_is_valid_not_a_crash(tmp_path):
    conn = _conn(tmp_path)
    md = render_weekly_md(build_weekly(conn, *_WEEK))
    assert "2026-W28" in md
    assert "0" in md  # zero trades stated, not an exception
