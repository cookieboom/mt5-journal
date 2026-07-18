"""M5 `journal report` — `build_report()`. Money-based stats at full closed-
trade coverage; R-based stats honestly gated by docs §9's n<20 rule.
"""

from __future__ import annotations

import pytest

from journal.adapter.fake import FakeMT5Client
from journal.analytics.report import ReportResult, build_report
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
):
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, volume, open_price, net_profit, "
        "r_multiple, mae, mae_r, mfe_r, deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, 'buy', ?, 1, 0.1, 4000.0, ?, ?, ?, ?, ?, 2, 1)",
        (_LOGIN, position_id, symbol, symbol[:-1], status, net_profit,
         r_multiple, mae, mae_r, mfe_r),
    )
    conn.commit()


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
