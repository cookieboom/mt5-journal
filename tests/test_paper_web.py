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


def _order(conn, account, **kw):
    body = dict(symbol="XAUUSDc", direction="buy", kind="market", volume=0.10,
                sl=4025.0, tp=0.0)
    body.update(kw)
    return paper.place_order(conn, account, **body)


def test_a_market_buy_fills_at_the_ask_immediately(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account)
    assert out["status"] == "open"
    assert out["entry_price"] == pytest.approx(4030.5)
    assert out["sl_initial"] == pytest.approx(4025.0)
    assert out["symbol_base"] == "XAUUSD"


def test_a_stale_quote_refuses_the_order_instead_of_resizing_it(conn, account):
    live_store.upsert_quote(conn, "XAUUSDc", bid=4030.0, ask=4030.5,
                            tick_msc=1_000, now_msc=now_ms() - 60_000)
    with pytest.raises(paper.PaperError, match="basi"):
        _order(conn, account)


def test_an_order_on_a_symbol_with_no_quote_at_all_is_refused(conn, account):
    # The implementation's message is a fresh sentence ("Belum ada harga..."),
    # capitalised like every other PaperError in this module (Task 10's
    # "Tidak ada akun...", "Nama akun wajib...") -- so the match string is
    # capitalised too, not the brief's literal lowercase "belum ada harga"
    # (case-sensitive re.search would never find it against the real message).
    with pytest.raises(paper.PaperError, match="Belum ada harga"):
        _order(conn, account)


def test_a_pending_order_needs_no_quote_and_stays_pending(conn, account):
    _fresh_quote(conn)
    out = _order(conn, account, kind="limit", price=4025.0, sl=4020.0)
    assert out["status"] == "pending"
    assert out["entry_price"] is None
    assert out["request_price"] == pytest.approx(4025.0)


def test_volume_and_risk_pct_together_are_refused_and_so_is_neither(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="salah satu"):
        _order(conn, account, volume=0.10, risk_pct=1.0)
    with pytest.raises(paper.PaperError, match="salah satu"):
        _order(conn, account, volume=None, risk_pct=None)


def test_risk_pct_sizing_is_refused_by_the_shared_max_lot_cap(conn, account):
    # 1% of 1_000_000 USC equity = 10_000 USC at risk. Entry 4030.5, stop
    # 4025.0 is a 5.5 USD distance; XAUUSDc's specs (tick_size=0.001,
    # tick_value=0.1) make that 5500 ticks * 0.1 USC = 550 USC of risk per
    # 1.0-lot (the same scaling the doc's own hand-verified figure uses: 0.10
    # lot / 5.0 distance -> 50 USC). risk.volume_for_risk therefore sizes this
    # at 10_000 / 550 ~= 18.18 lots -- verified directly against the already-
    # reviewed domain.risk.volume_for_risk/floor_to_step:
    #   >>> volume_for_risk(4030.5, 4025.0, 0.001, 0.1, 10_000.0)
    #   18.181818181818183
    #   >>> floor_to_step(18.181818181818183, 0.01)
    #   18.18
    # 18.18 lots is far past commands.MAX_LOT (1.0) -- the human's hard cap
    # that check_volume enforces on every caller, risk-derived volume
    # included (its own docstring: "Paper trading is now a second caller").
    # A risk budget this large on a symbol whose whole 1.0-lot cap only ever
    # risks $5.50 (XAUUSDc, 1 lot = 1 oz) is exactly the case that cap exists
    # to refuse, not a scenario place_order can size into 0.18 lot -- 0.18
    # lot would risk under a dollar, two orders of magnitude short of the 1%
    # ($100) actually requested.
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="batas keras"):
        _order(conn, account, volume=None, risk_pct=1.0)


def test_risk_pct_sizing_needs_a_stop_to_size_against(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="SL"):
        _order(conn, account, volume=None, risk_pct=1.0, sl=0.0)


def test_a_stop_on_the_wrong_side_is_refused_by_the_shared_validator(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="BAWAH"):
        _order(conn, account, sl=4040.0)


def test_a_volume_off_the_brokers_step_is_refused(conn, account):
    _fresh_quote(conn)
    with pytest.raises(paper.PaperError, match="kelipatan"):
        _order(conn, account, volume=0.015)


def test_an_order_larger_than_the_free_margin_is_refused(conn, account):
    _fresh_quote(conn)
    small = paper.create_account(conn, name="Tipis", initial_balance=100.0,
                                 leverage=500, stopout_pct=20.0)["id"]
    with pytest.raises(paper.PaperError, match="margin"):
        _order(conn, small, volume=1.00)


def test_an_archived_account_takes_no_new_orders(conn, account):
    _fresh_quote(conn)
    paper.archive_account(conn, account)
    with pytest.raises(paper.PaperError, match="diarsipkan"):
        _order(conn, account)


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
