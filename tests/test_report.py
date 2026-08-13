"""M5 `journal report` — `build_report()`. Money-based stats at full closed-
trade coverage; R-based stats honestly gated by docs §9's n<20 rule.
"""

from __future__ import annotations

import pytest

from journal.adapter.fake import FakeMT5Client
from journal.analytics.report import BucketStat, ReportResult, build_report
from journal.analytics.sessions import SESSION_ORDER
from journal.domain.reconstruct import rebuild
from journal.ingest.deals import sync
from journal.store.db import connect

_LOGIN = 0
_TOL = 1e-9


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _seed_account(conn, currency="USC"):
    conn.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, ?, 1)",
        (_LOGIN, currency),
    )
    conn.commit()


def _seed_trade(
    conn, position_id, *, status="closed", net_profit=0.0, r_multiple=None,
    mae=None, mae_r=None, mfe_r=None, symbol="XAUUSDc",
    open_time_msc=1, magic=None, close_time_msc=None,
):
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, volume, open_price, "
        "net_profit, r_multiple, mae, mae_r, mfe_r, magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, 'buy', ?, ?, ?, 0.1, 4000.0, ?, ?, ?, ?, ?, ?, 2, 1)",
        (_LOGIN, position_id, symbol, symbol[:-1], status, open_time_msc,
         close_time_msc, net_profit, r_multiple, mae, mae_r, mfe_r, magic),
    )
    conn.commit()


def _ms(hour: int, minute: int = 0) -> int:
    """Epoch ms (UTC) at a fixed date and the given UTC hour — for placing a
    seeded trade in a known trading session (see test_sessions.py)."""
    from datetime import datetime, timezone
    dt = datetime(2026, 1, 15, hour, minute, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------- classification


def test_win_loss_breakeven_classified_with_tolerance(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=50.0)     # win
    _seed_trade(conn, 2, net_profit=-15.0)    # loss
    _seed_trade(conn, 3, net_profit=0.0)      # exact breakeven
    _seed_trade(conn, 4, net_profit=1e-12)    # within tolerance of 0 -> breakeven
    _seed_trade(conn, 5, net_profit=-1e-12)   # within tolerance of 0 -> breakeven

    r = build_report(conn)
    assert r.n_closed == 5
    assert r.n_wins == 1
    assert r.n_losses == 1
    assert r.n_breakeven == 3


def test_open_and_partial_trades_excluded_from_closed_stats(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", net_profit=10.0)
    _seed_trade(conn, 2, status="open", net_profit=0.0)
    _seed_trade(conn, 3, status="partially_open", net_profit=0.0)

    r = build_report(conn)
    assert r.n_total == 3
    assert r.n_closed == 1


# ------------------------------------------------------------ money stats


def test_money_stats_full_coverage(conn):
    _seed_account(conn)
    for pid, net in [(1, 50.0), (2, 20.0), (3, 10.0), (4, -15.0), (5, -5.0)]:
        _seed_trade(conn, pid, net_profit=net)

    r = build_report(conn)
    assert r.n_closed == 5
    assert abs(r.win_rate - 3 / 5) < _TOL
    assert abs(r.avg_win - (50 + 20 + 10) / 3) < _TOL
    assert abs(r.avg_loss - (-15 - 5) / 2) < _TOL  # negative, reads naturally
    assert r.avg_loss < 0
    assert abs(r.expectancy - (50 + 20 + 10 - 15 - 5) / 5) < _TOL
    assert abs(r.profit_factor - (80 / 20)) < _TOL


def test_profit_factor_all_wins_is_undefined_not_a_crash(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=10.0)
    _seed_trade(conn, 2, net_profit=20.0)

    r = build_report(conn)
    assert r.profit_factor is None  # undefined -- not infinity, not a crash


def test_profit_factor_all_losses_is_zero_a_defined_value(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=-10.0)
    _seed_trade(conn, 2, net_profit=-5.0)

    r = build_report(conn)
    assert r.profit_factor == 0.0  # defined: zero wins over real losses


def test_currency_carried_on_the_result(conn):
    _seed_account(conn, currency="USC")
    _seed_trade(conn, 1, net_profit=10.0)

    r = build_report(conn)
    assert r.currency == "USC"
    # never a bare '$' anywhere the report formats money (Trap 13) -- the
    # CLI is what actually prints it, but the currency must be present on the
    # result for the CLI to have something to print.
    assert "$" not in r.currency


# --------------------------------------------------------------- R-family


def test_r_multiple_suppressed_below_n20(conn):
    _seed_account(conn)
    for pid in range(1, 7):  # 6 trades with r_multiple -- below the n>=20 gate
        _seed_trade(conn, pid, net_profit=10.0, r_multiple=1.5)

    r = build_report(conn)
    assert r.n_with_r == 6
    assert r.avg_r is None  # withheld, not a misleadingly precise number


def test_r_multiple_shown_at_or_above_n20(conn):
    _seed_account(conn)
    for pid in range(1, 21):  # exactly 20 -- clears the gate
        _seed_trade(conn, pid, net_profit=10.0, r_multiple=2.0)

    r = build_report(conn)
    assert r.n_with_r == 20
    assert r.avg_r is not None
    assert abs(r.avg_r - 2.0) < _TOL


def test_mae_coverage_shown_unconditionally_not_gated(conn):
    # n_with_mae is a plain diagnostic count -- always shown, never suppressed
    # by n<20, since it isn't an averaged statistic.
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=10.0, mae=5.0)
    _seed_trade(conn, 2, net_profit=10.0, mae=None)

    r = build_report(conn)
    assert r.n_with_mae == 1


def test_mae_r_mfe_r_suppressed_below_n20(conn):
    _seed_account(conn)
    for pid in range(1, 7):
        _seed_trade(conn, pid, net_profit=10.0, mae_r=0.3, mfe_r=0.8)

    r = build_report(conn)
    assert r.n_with_mae_r == 6 and r.avg_mae_r is None
    assert r.n_with_mfe_r == 6 and r.avg_mfe_r is None


# ------------------------------------------------------------------- misc


def test_build_report_takes_no_login_argument(conn):
    # matches rebuild/render_trade/sync_candles's convention: login is
    # resolved internally via one_account_login(conn), never passed in.
    import inspect
    sig = inspect.signature(build_report)
    assert list(sig.parameters) == ["conn"]


def test_empty_account_no_crash_all_sections_suppressed(conn):
    _seed_account(conn)  # account exists, but zero trades at all
    r = build_report(conn)
    assert r.n_total == 0
    assert r.n_closed == 0
    assert r.win_rate is None
    assert r.avg_win is None
    assert r.avg_loss is None
    assert r.profit_factor is None
    assert r.expectancy is None
    assert r.avg_r is None
    assert r.avg_mae_r is None
    assert r.avg_mfe_r is None


def test_result_is_frozen_dataclass():
    assert ReportResult.__dataclass_params__.frozen


# --------------------------------------------------------- M5.1 breakdowns


def _bucket(buckets, label) -> BucketStat:
    (b,) = [x for x in buckets if x.label == label]
    return b


def test_by_session_all_five_buckets_present_in_order_even_when_empty(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=10.0, open_time_msc=_ms(9))  # London only

    r = build_report(conn)
    assert tuple(b.label for b in r.by_session) == SESSION_ORDER
    london = _bucket(r.by_session, "London")
    assert london.n == 1
    # The other four buckets are present with n=0 and no crash (empty-bucket
    # ZeroDivision guard) -- shown, not dropped, so the table shape is stable.
    for label in ("Asian", "LDN/NY", "New York", "Late"):
        b = _bucket(r.by_session, label)
        assert b.n == 0
        assert b.win_rate is None and b.expectancy is None and b.avg_r is None


def test_by_session_assigns_each_trade_to_its_utc_session(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=1.0, open_time_msc=_ms(3))   # Asian
    _seed_trade(conn, 2, net_profit=1.0, open_time_msc=_ms(9))   # London
    _seed_trade(conn, 3, net_profit=1.0, open_time_msc=_ms(9, 30))  # London
    _seed_trade(conn, 4, net_profit=1.0, open_time_msc=_ms(14))  # LDN/NY
    _seed_trade(conn, 5, net_profit=1.0, open_time_msc=_ms(18))  # New York
    _seed_trade(conn, 6, net_profit=1.0, open_time_msc=_ms(22))  # Late

    r = build_report(conn)
    counts = {b.label: b.n for b in r.by_session}
    assert counts == {"Asian": 1, "London": 2, "LDN/NY": 1, "New York": 1, "Late": 1}
    # Session buckets partition the closed trades exactly.
    assert sum(b.n for b in r.by_session) == r.n_closed


def test_by_source_splits_ea_from_discretionary(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=5.0, magic=123456)  # EA (magic truthy)
    _seed_trade(conn, 2, net_profit=5.0, magic=0)       # Discretionary (magic 0)
    _seed_trade(conn, 3, net_profit=5.0, magic=None)    # Discretionary (magic NULL)

    r = build_report(conn)
    assert tuple(b.label for b in r.by_source) == ("EA", "Discretionary")
    assert _bucket(r.by_source, "EA").n == 1
    # Rule 4: an unknown (NULL) magic is NOT evidence of EA -> discretionary.
    assert _bucket(r.by_source, "Discretionary").n == 2
    assert sum(b.n for b in r.by_source) == r.n_closed


def test_bucket_stats_gated_below_n20(conn):
    _seed_account(conn)
    for pid in range(1, 7):  # 6 trades in one session -- below the n>=20 gate
        _seed_trade(conn, pid, net_profit=10.0, r_multiple=1.5, open_time_msc=_ms(9))

    london = _bucket(build_report(conn).by_session, "London")
    assert london.n == 6            # raw count always shown
    assert london.n_with_r == 6     # raw diagnostic always shown
    assert london.win_rate is None  # averages withheld, not misleadingly precise
    assert london.expectancy is None
    assert london.avg_r is None


def test_bucket_stats_shown_at_or_above_n20(conn):
    _seed_account(conn)
    for pid in range(1, 21):  # exactly 20 in one session -- clears the gate
        _seed_trade(conn, pid, net_profit=10.0, r_multiple=2.0, open_time_msc=_ms(9))

    london = _bucket(build_report(conn).by_session, "London")
    assert london.n == 20
    assert london.win_rate is not None and abs(london.win_rate - 1.0) < _TOL
    assert london.expectancy is not None and abs(london.expectancy - 10.0) < _TOL
    assert london.avg_r is not None and abs(london.avg_r - 2.0) < _TOL


def test_buckets_exclude_open_and_partial_trades(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", net_profit=10.0, open_time_msc=_ms(9))
    _seed_trade(conn, 2, status="open", net_profit=0.0, open_time_msc=_ms(9))
    _seed_trade(conn, 3, status="partially_open", net_profit=0.0, open_time_msc=_ms(9))

    r = build_report(conn)
    assert _bucket(r.by_session, "London").n == 1  # only the closed trade
    assert sum(b.n for b in r.by_session) == 1


def test_empty_account_has_all_buckets_present_and_suppressed(conn):
    _seed_account(conn)  # zero trades
    r = build_report(conn)
    assert tuple(b.label for b in r.by_session) == SESSION_ORDER
    assert tuple(b.label for b in r.by_source) == ("EA", "Discretionary")
    for b in (*r.by_session, *r.by_source):
        assert b.n == 0
        assert b.win_rate is None and b.expectancy is None and b.avg_r is None


# ------------------------------------------------------- M8 by_symbol breakdown


def test_by_symbol_one_bucket_per_base_ordered_ascending(conn):
    # Unlike by_session/by_source (fixed sets), symbols are data-driven, so the
    # buckets are exactly the distinct symbol_base present, ordered ascending for
    # a stable, gap-free table that grows when a new symbol appears.
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=5.0, symbol="EURUSDc")
    _seed_trade(conn, 2, net_profit=5.0, symbol="XAUUSDc")
    _seed_trade(conn, 3, net_profit=5.0, symbol="BTCUSDc")

    r = build_report(conn)
    assert tuple(b.label for b in r.by_symbol) == ("BTCUSD", "EURUSD", "XAUUSD")


def test_by_symbol_groups_by_base_not_verbatim_symbol(conn):
    # Rule 11 / trap 12: group by the normalised symbol_base ('XAUUSD'), never
    # the verbatim broker symbol ('XAUUSDc').
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=5.0, symbol="XAUUSDc")
    _seed_trade(conn, 2, net_profit=5.0, symbol="XAUUSDc")

    r = build_report(conn)
    assert tuple(b.label for b in r.by_symbol) == ("XAUUSD",)
    assert _bucket(r.by_symbol, "XAUUSD").n == 2


def test_by_symbol_partitions_closed_trades(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=5.0, symbol="XAUUSDc")
    _seed_trade(conn, 2, net_profit=5.0, symbol="BTCUSDc")
    _seed_trade(conn, 3, net_profit=5.0, symbol="BTCUSDc")
    _seed_trade(conn, 4, status="open", net_profit=0.0, symbol="EURUSDc")

    r = build_report(conn)
    assert _bucket(r.by_symbol, "XAUUSD").n == 1
    assert _bucket(r.by_symbol, "BTCUSD").n == 2
    # open trade excluded -> EURUSD has no closed trade, so no bucket at all
    assert "EURUSD" not in [b.label for b in r.by_symbol]
    assert sum(b.n for b in r.by_symbol) == r.n_closed


def test_by_symbol_gated_below_n20(conn):
    _seed_account(conn)
    for pid in range(1, 7):  # 6 XAUUSD trades -- below the n>=20 gate
        _seed_trade(conn, pid, net_profit=10.0, r_multiple=1.5, symbol="XAUUSDc")

    xau = _bucket(build_report(conn).by_symbol, "XAUUSD")
    assert xau.n == 6            # raw count always shown
    assert xau.n_with_r == 6     # raw diagnostic always shown
    assert xau.win_rate is None  # averages withheld (docs §9)
    assert xau.expectancy is None
    assert xau.avg_r is None


def test_by_symbol_shown_at_or_above_n20(conn):
    _seed_account(conn)
    for pid in range(1, 21):  # exactly 20 XAUUSD -- clears the gate
        _seed_trade(conn, pid, net_profit=10.0, r_multiple=2.0, symbol="XAUUSDc")

    xau = _bucket(build_report(conn).by_symbol, "XAUUSD")
    assert xau.n == 20
    assert xau.win_rate is not None and abs(xau.win_rate - 1.0) < _TOL
    assert xau.expectancy is not None and abs(xau.expectancy - 10.0) < _TOL
    assert xau.avg_r is not None and abs(xau.avg_r - 2.0) < _TOL


def test_by_symbol_empty_account_is_empty_tuple(conn):
    # No fixed set to fall back on -- zero closed trades means zero buckets.
    _seed_account(conn)
    assert build_report(conn).by_symbol == ()


# ------------------------------------------------------- drawdown and streaks


def test_drawdown_and_streaks_read_the_sequence_in_close_time_order(conn):
    # Inserted deliberately out of order: the sequence these three statistics
    # read is the order the account LIVED, which is close time, not rowid.
    _seed_account(conn)
    _seed_trade(conn, 3, net_profit=-40.0, close_time_msc=3000)
    _seed_trade(conn, 1, net_profit=100.0, close_time_msc=1000)
    _seed_trade(conn, 2, net_profit=-30.0, close_time_msc=2000)
    _seed_trade(conn, 4, net_profit=10.0, close_time_msc=4000)

    r = build_report(conn)
    # cumulative: 100, 70, 30, 40 -> peak 100, trough 30
    assert r.n_sequenced == 4
    assert abs(r.max_drawdown - 70.0) < _TOL
    assert r.max_loss_streak == 2
    assert r.max_win_streak == 1


def test_drawdown_is_zero_when_the_curve_only_rises(conn):
    _seed_account(conn)
    for pid, net in [(1, 5.0), (2, 5.0), (3, 5.0)]:
        _seed_trade(conn, pid, net_profit=net, close_time_msc=pid * 1000)

    r = build_report(conn)
    assert r.max_drawdown == 0.0        # never drew down, not "unknown"
    assert r.max_win_streak == 3
    assert r.max_loss_streak == 0


def test_drawdown_measures_the_deepest_trough_not_the_last_one(conn):
    _seed_account(conn)
    # cumulative: 10, -40, 60, 35 -> deepest decline is 50 (10 -> -40),
    # the later 60 -> 35 dip is only 25.
    for pid, net in [(1, 10.0), (2, -50.0), (3, 100.0), (4, -25.0)]:
        _seed_trade(conn, pid, net_profit=net, close_time_msc=pid * 1000)

    r = build_report(conn)
    assert abs(r.max_drawdown - 50.0) < _TOL


def test_breakeven_breaks_both_streaks_with_the_same_tolerance(conn):
    _seed_account(conn)
    # win, win, breakeven-within-tolerance, win  ->  longest win streak is 2
    for pid, net in [(1, 5.0), (2, 5.0), (3, 1e-12), (4, 5.0)]:
        _seed_trade(conn, pid, net_profit=net, close_time_msc=pid * 1000)

    r = build_report(conn)
    assert r.n_breakeven == 1
    assert r.max_win_streak == 2
    assert r.max_loss_streak == 0


def test_trade_without_a_close_time_cannot_be_placed_in_the_sequence(conn):
    # rule 4: NULL close_time_msc is UNKNOWN, not "at the start of time".
    # It must not silently take a position in the streak/drawdown sequence.
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=-10.0, close_time_msc=1000)
    _seed_trade(conn, 2, net_profit=-10.0, close_time_msc=None)
    _seed_trade(conn, 3, net_profit=-10.0, close_time_msc=2000)

    r = build_report(conn)
    assert r.n_closed == 3      # money stats still see all three
    assert r.n_sequenced == 2   # the sequence honestly sees two
    assert r.max_loss_streak == 2
    assert abs(r.max_drawdown - 20.0) < _TOL


def test_open_trades_never_enter_the_sequence(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=10.0, close_time_msc=1000)
    _seed_trade(conn, 2, status="open", net_profit=-999.0, close_time_msc=2000)

    r = build_report(conn)
    assert r.n_sequenced == 1
    assert r.max_drawdown == 0.0


def test_empty_account_has_no_drawdown_rather_than_zero(conn):
    _seed_account(conn)
    r = build_report(conn)
    assert r.n_sequenced == 0
    assert r.max_drawdown is None   # unknown, not "never drew down" (rule 4)
    assert r.max_win_streak == 0
    assert r.max_loss_streak == 0


# --------------------------------------------------------------- integration


def test_report_against_real_fixture_does_not_crash(conn):
    client = FakeMT5Client()  # real recorded fixtures (68 trades)
    sync(client, conn)
    rebuild(conn)

    r = build_report(conn)
    assert r.n_closed == 68
    assert r.n_wins + r.n_losses + r.n_breakeven == 68
    # docs §7: only 6/68 trades have a recoverable sl_initial -- R stats are
    # expected to be suppressed on this fixture, not a bug.
    assert r.n_with_r == 6
    assert r.avg_r is None
    # money stats have full coverage and must be real numbers, not None.
    assert r.win_rate is not None
    assert r.expectancy is not None
    assert r.currency == "USC"
    # docs §7: exactly 6 trades are EA (magic != 0); the rest discretionary.
    # Both breakdowns must partition all 68 closed trades with no leakage.
    assert _bucket(r.by_source, "EA").n == 6
    assert _bucket(r.by_source, "Discretionary").n == 62
    assert sum(b.n for b in r.by_session) == 68
    # by_symbol partitions the same 68 closed trades; labels are the normalised
    # bases actually traded on this account (docs "This account"), no verbatim
    # 'c' suffix and no symbol absent from the data.
    assert sum(b.n for b in r.by_symbol) == 68
    assert set(b.label for b in r.by_symbol) <= {"XAUUSD", "BTCUSD", "EURUSD"}
    assert all(not b.label.endswith("c") for b in r.by_symbol)
    # every closed trade in this fixture has a close time, so the sequence
    # statistics see the whole population — no silent subset.
    assert r.n_sequenced == 68
    assert r.max_drawdown is not None and r.max_drawdown > 0
    assert r.max_loss_streak >= 1
