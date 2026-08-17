"""The pure paper-trading evaluator. No DB, no bridge, no MT5 — every case here
is a plain dataclass, per CLAUDE.md rules 1, 7 and 12.

The reference figures are hand-computed for XAUUSDc as measured on this account:
tick_size=0.001, tick_value=0.1 USC, contract_size=1.0 (1 lot = 1 oz). 0.10 lot
at 4030 is 0.1 oz worth 403 USD = 40300 USC; at 1:500 that is 80.6 USC of margin.
"""
from __future__ import annotations

import pytest

from journal.domain import paper_eval as pe

XAU = pe.Specs(tick_size=0.001, tick_value=0.1, contract_size=1.0,
               currency_profit="USD")


def q(bid=4030.0, ask=4030.5, t=1_700_000_000_000, symbol="XAUUSDc"):
    return pe.Quote(symbol=symbol, bid=bid, ask=ask, time_msc=t)


def pos(**kw):
    base = dict(id=1, symbol="XAUUSDc", direction="buy", order_kind="market",
                request_price=None, volume=0.10, sl=0.0, tp=0.0, status="open",
                entry_price=4030.0, entry_msc=1_700_000_000_000, expires_msc=None)
    base.update(kw)
    return pe.PaperPos(**base)


def test_usc_per_quote_unit_is_derived_from_the_specs():
    # 0.1 USC per 0.001 USD of price = 100 USC per USD. Never a literal 100.
    assert pe.usc_per_quote_unit(XAU) == pytest.approx(100.0)


def test_margin_matches_the_hand_computed_figure():
    assert pe.margin_usc(0.10, 4030.0, XAU, 500) == pytest.approx(80.6)


def test_margin_is_unknown_for_a_non_usd_quote_currency():
    eur_quoted = pe.Specs(0.001, 0.1, 1.0, "EUR")
    assert pe.margin_usc(0.10, 4030.0, eur_quoted, 500) is None


def test_margin_is_unknown_for_a_malformed_spec_or_leverage():
    assert pe.margin_usc(0.10, 4030.0, pe.Specs(0.0, 0.1, 1.0, "USD"), 500) is None
    assert pe.margin_usc(0.10, 4030.0, XAU, 0) is None


def test_a_buy_enters_at_the_ask_and_exits_at_the_bid():
    assert pe.entry_side("buy", q()) == 4030.5
    assert pe.exit_side("buy", q()) == 4030.0
    assert pe.entry_side("sell", q()) == 4030.0
    assert pe.exit_side("sell", q()) == 4030.5


def test_floating_pnl_of_a_fresh_buy_is_negative_by_the_spread():
    # Entered at the ask (4030.5), marked at the bid (4030.0): 0.5 USD against
    # 0.1 oz = 5 USC. A simulator that showed 0 here would be flattering.
    p = pos(entry_price=4030.5)
    assert pe.floating_usc(p, q(), XAU) == pytest.approx(-5.0)


def test_floating_pnl_is_unknown_when_the_position_never_filled():
    assert pe.floating_usc(pos(status="pending", entry_price=None), q(), XAU) is None


def test_account_state_of_a_flat_account_has_no_margin_level():
    st = pe.account_state([], {}, {"XAUUSDc": XAU}, balance=1_000_000.0,
                          leverage=500)
    assert st.equity == pytest.approx(1_000_000.0)
    assert st.margin == pytest.approx(0.0)
    assert st.margin_level is None      # no margin to divide by — not infinity


def test_account_state_adds_floating_to_balance_and_divides_for_the_level():
    p = pos(entry_price=4030.5)
    st = pe.account_state([p], {"XAUUSDc": q()}, {"XAUUSDc": XAU},
                          balance=1_000_000.0, leverage=500)
    assert st.floating == pytest.approx(-5.0)
    assert st.equity == pytest.approx(999_995.0)
    assert st.margin == pytest.approx(80.61)      # 0.10 lot at the 4030.5 entry
    assert st.free_margin == pytest.approx(999_995.0 - 80.61)
    assert st.margin_level == pytest.approx(999_995.0 / 80.61 * 100)


def test_account_state_reports_unknown_rather_than_guessing_a_missing_quote():
    st = pe.account_state([pos()], {}, {"XAUUSDc": XAU}, balance=1_000_000.0,
                          leverage=500)
    assert st.floating is None
    assert st.equity is None
    assert st.margin_level is None
