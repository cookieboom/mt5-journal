"""The paper-trading service functions, called directly against a seeded DB with
no HTTP layer — the discipline `tests/test_web.py` states, and why this project
carries no TestClient dependency.

What a UI can silently violate, and is therefore tested here: money always
carries its unit, an unknown never reads as 0 (rule 4), and a stale feed refuses
rather than resizes.
"""
from __future__ import annotations

import pytest

from journal.store import live_store, paper_store
from journal.store.db import connect, now_ms
from journal.web import paper


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    _seed_specs(c)
    yield c
    c.close()


def _seed_specs(conn):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, digits, point, tick_size, "
        "tick_value, contract_size, currency_profit, fetched_at, volume_min, "
        "volume_max, volume_step, stops_level, freeze_level, trade_mode, "
        "filling_mode) VALUES ('XAUUSDc', 'XAUUSD', 3, 0.001, 0.001, 0.1, 1.0, "
        "'USD', 1, 0.01, 100.0, 0.01, 0, 0, 4, 1)"
    )
    conn.commit()


def _fresh_quote(conn, bid=4030.0, ask=4030.5):
    live_store.upsert_quote(conn, "XAUUSDc", bid=bid, ask=ask,
                            tick_msc=now_ms(), now_msc=now_ms())


@pytest.fixture
def account(conn):
    return paper.create_account(conn, name="Scalping XAU",
                                initial_balance=1_000_000.0, leverage=500,
                                stopout_pct=20.0)["id"]


def test_a_new_account_is_flat_and_says_its_currency(conn, account):
    view = paper.account_view(conn, account)
    assert view["header"]["currency"] == "USC"
    assert view["header"]["balance"] == pytest.approx(1_000_000.0)
    assert view["header"]["equity"] == pytest.approx(1_000_000.0)
    assert view["header"]["margin_level"] is None      # flat, not infinite
    assert view["open"] == [] and view["pending"] == []


def test_an_account_that_does_not_exist_is_none_not_an_empty_account(conn):
    assert paper.account_view(conn, 999) is None


def test_a_duplicate_name_is_refused_with_a_readable_message(conn, account):
    with pytest.raises(paper.PaperError, match="sudah dipakai"):
        paper.create_account(conn, name="Scalping XAU", initial_balance=1.0,
                             leverage=500, stopout_pct=20.0)


def test_a_nonsense_account_is_refused_rather_than_created(conn):
    for kwargs in (
        dict(initial_balance=0.0, leverage=500, stopout_pct=20.0),
        dict(initial_balance=1_000.0, leverage=0, stopout_pct=20.0),
        dict(initial_balance=1_000.0, leverage=500, stopout_pct=-1.0),
    ):
        with pytest.raises(paper.PaperError):
            paper.create_account(conn, name=f"x{kwargs}", **kwargs)


def test_the_header_marks_an_open_position_at_the_stored_quote(conn, account):
    _fresh_quote(conn)
    paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="open", entry_price=4030.5, entry_msc=1,
        expires_msc=None,
    )
    header = paper.account_view(conn, account)["header"]
    assert header["floating"] == pytest.approx(-5.0)      # the spread, honestly
    assert header["equity"] == pytest.approx(999_995.0)
    assert header["margin"] == pytest.approx(80.61)
    assert header["margin_level"] is not None


def test_the_header_reports_unknown_when_no_quote_has_ever_arrived(conn, account):
    paper_store.insert_position(
        conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
        direction="buy", order_kind="market", request_price=None, volume=0.10,
        sl=0.0, tp=0.0, status="open", entry_price=4030.5, entry_msc=1,
        expires_msc=None,
    )
    header = paper.account_view(conn, account)["header"]
    assert header["equity"] is None and header["margin_level"] is None


def test_the_equity_curve_and_drawdown_read_closed_slices_in_exit_order(conn, account):
    for net, exit_msc in ((-200.0, 3_000), (500.0, 1_000), (-100.0, 2_000)):
        pid = paper_store.insert_position(
            conn, account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
            direction="buy", order_kind="market", request_price=None, volume=0.01,
            sl=0.0, tp=0.0, status="open", entry_price=4030.0, entry_msc=1,
            expires_msc=None,
        )
        paper_store.mark_close(conn, pid, exit_msc=exit_msc, exit_price=4031.0,
                               exit_reason="manual", net_profit=net,
                               r_multiple=None, mae=None, mfe=None,
                               mae_r=None, mfe_r=None)
        paper_store.add_balance(conn, account, net)

    view = paper.account_view(conn, account)
    curve = view["equity_curve"]
    assert [p["exit_msc"] for p in curve] == [1_000, 2_000, 3_000]
    assert [p["balance"] for p in curve] == pytest.approx(
        [1_000_500.0, 1_000_400.0, 1_000_200.0])
    assert view["summary"]["n"] == 3
    assert view["summary"]["win_rate"] == pytest.approx(1 / 3)
    assert view["max_drawdown"] == pytest.approx(300.0)   # 1_000_500 → 1_000_200
