"""M9 Phase 3 — command validation and request building.

Written before the implementation (CLAUDE.md rule 7).

This is the layer that stands between a click and a real order, so it is tested
harder than anything else in M9. Everything here is pure: it takes a position
row and a spec row, and returns a `TradeRequest` or raises. No DB, no bridge, no
clock.

The two facts that shape most of these tests:
  * this account is HEDGING (margin_mode=2), and
  * all three symbols are MARKET execution (trade_exemode=2, measured).
Both change what a correct request looks like, and neither is guessable from the
MQL5 docs alone.
"""

from __future__ import annotations

import pytest

from journal.adapter.base import OrderFilling, OrderType, TradeAction, TradeRetcode
from journal.domain.commands import (
    MAX_LOT,
    CommandError,
    build_request,
    classify,
    validate,
)

# A fully-known spec, as `journal sync` writes it for XAUUSDc (measured values).
_SPEC = {
    "symbol": "XAUUSDc",
    "digits": 3,
    "point": 0.001,
    "volume_min": 0.01,
    "volume_max": 200.0,
    "volume_step": 0.01,
    "stops_level": 0,
    "freeze_level": 0,
    "trade_mode": 4,        # SYMBOL_TRADE_MODE_FULL
    "filling_mode": 3,      # FOK|IOC
}

_BUY = {
    "position_id": 111,
    "symbol": "XAUUSDc",
    "symbol_base": "XAUUSD",
    "direction": "buy",
    "volume": 0.10,
    "open_price": 3300.0,
    "price_current": 3310.0,
    "sl": 0.0,
    "tp": 0.0,
}

_SELL = dict(_BUY, position_id=222, direction="sell", price_current=3290.0)


def _spec(**over):
    return dict(_SPEC, **over)


def _buy(**over):
    return dict(_BUY, **over)


def _sell(**over):
    return dict(_SELL, **over)


# ------------------------------------------------------------------ the cap


def test_max_lot_is_one():
    """The human's stated hard cap. A constant, not a UI limit, so it is
    enforced where it can be tested."""
    assert MAX_LOT == 1.0


def test_volume_above_the_cap_is_refused():
    with pytest.raises(CommandError, match="1"):
        validate("add_volume", _buy(), _spec(), volume=1.01)


def test_volume_exactly_at_the_cap_is_allowed():
    """Rule 5: compared with tolerance, never `>`. A float 1.0 that reads as
    1.0000000000000002 must not be refused."""
    validate("add_volume", _buy(), _spec(), volume=1.0)


def test_a_full_close_is_never_capped():
    """THE cap must not become a trap. A `close` closes exactly what exists and
    always REDUCES exposure — refusing it because the position is larger than
    the cap would leave the human unable to exit a position through the tool
    that opened it. The cap governs volume a human TYPES (close_partial,
    add_volume), not a full exit."""
    validate("close", _buy(volume=5.0), _spec(), volume=None)


# ------------------------------------------------------- unknown spec (rule 4)


def test_unknown_spec_is_refused_not_assumed():
    """Rule 4. A spec row written before M9 has NULL volume_step. Unknown is not
    permission — validating against a missing limit is not validating."""
    with pytest.raises(CommandError, match="unknown|belum"):
        validate("add_volume", _buy(), _spec(volume_step=None), volume=0.10)


def test_unknown_volume_min_is_refused():
    with pytest.raises(CommandError):
        validate("add_volume", _buy(), _spec(volume_min=None), volume=0.10)


def test_unknown_spec_does_not_block_a_full_close():
    """Same reasoning as the cap: a full close needs no volume validation,
    because the volume is the position's own. An unknown spec must not trap a
    human in a position."""
    validate("close", _buy(), _spec(volume_min=None, volume_step=None), volume=None)


# ---------------------------------------------------------------- lot sizing


def test_volume_below_the_symbol_minimum_is_refused():
    with pytest.raises(CommandError):
        validate("add_volume", _buy(), _spec(), volume=0.001)


def test_volume_above_the_symbol_maximum_is_refused():
    with pytest.raises(CommandError):
        validate("add_volume", _buy(), _spec(volume_max=0.05), volume=0.10)


def test_volume_off_the_step_is_refused():
    with pytest.raises(CommandError, match="step|kelipatan"):
        validate("add_volume", _buy(), _spec(), volume=0.015)


def test_volume_on_the_step_is_allowed_despite_float_error():
    """0.03 % 0.01 == 0.009999999999999998 in IEEE754. A modulo check would
    refuse a perfectly ordinary three-lot-step order. Rule 5 is not decoration."""
    for v in (0.01, 0.02, 0.03, 0.07, 0.29, 0.99):
        validate("add_volume", _buy(), _spec(), volume=v)


def test_volume_is_required_where_the_human_types_it():
    for kind in ("close_partial", "add_volume"):
        with pytest.raises(CommandError):
            validate(kind, _buy(), _spec(), volume=None)


# ------------------------------------------------------------- partial close


def test_partial_close_must_be_smaller_than_the_position():
    with pytest.raises(CommandError):
        validate("close_partial", _buy(volume=0.10), _spec(), volume=0.10)


def test_partial_close_larger_than_the_position_is_refused():
    with pytest.raises(CommandError):
        validate("close_partial", _buy(volume=0.10), _spec(), volume=0.20)


def test_partial_close_smaller_than_the_position_is_allowed():
    validate("close_partial", _buy(volume=0.10), _spec(), volume=0.04)


# ------------------------------------------------------------------- SL / TP


def test_buy_stop_loss_must_sit_below_the_price():
    with pytest.raises(CommandError, match="sl|SL"):
        validate("modify_sltp", _buy(price_current=3310.0), _spec(), sl=3320.0)


def test_buy_take_profit_must_sit_above_the_price():
    with pytest.raises(CommandError, match="tp|TP"):
        validate("modify_sltp", _buy(price_current=3310.0), _spec(), tp=3300.0)


def test_sell_stop_loss_must_sit_above_the_price():
    with pytest.raises(CommandError):
        validate("modify_sltp", _sell(price_current=3290.0), _spec(), sl=3280.0)


def test_sell_take_profit_must_sit_below_the_price():
    with pytest.raises(CommandError):
        validate("modify_sltp", _sell(price_current=3290.0), _spec(), tp=3300.0)


def test_correct_sides_are_allowed():
    validate("modify_sltp", _buy(price_current=3310.0), _spec(), sl=3300.0, tp=3320.0)
    validate("modify_sltp", _sell(price_current=3290.0), _spec(), sl=3300.0, tp=3280.0)


def test_zero_clears_a_level_and_skips_the_side_check():
    """Rule 4: 0.0 means "clear this level". It has no side, so the buy/sell
    comparison must not be applied to it — otherwise clearing a stop on a buy
    would be refused for sitting 'below' the price."""
    validate("modify_sltp", _buy(), _spec(), sl=0.0, tp=0.0)


def test_modify_with_nothing_to_change_is_refused():
    """Both None means 'leave both levels alone' — an empty instruction. Sending
    it would earn TRADE_RETCODE_NO_CHANGES; refusing it locally is cheaper and
    tells the human something useful."""
    with pytest.raises(CommandError):
        validate("modify_sltp", _buy(), _spec(), sl=None, tp=None)


def test_stops_level_distance_is_enforced_when_the_broker_sets_one():
    """stops_level is in POINTS, so the minimum distance is
    stops_level * point. This broker reports 0 (no restriction), but a spec
    refetch could change that — brokers widen it around news."""
    # XAUUSDc point = 0.001, so 1000 points is 1.00 in price units. (A first
    # draft of this test used 100 points and expected 1.00 — the code was right
    # and the test's arithmetic was wrong, which is exactly the kind of unit slip
    # `stops_level` invites: it is POINTS, never price.)
    spec = _spec(stops_level=1000)
    with pytest.raises(CommandError, match="dekat"):
        validate("modify_sltp", _buy(price_current=3310.0), spec, sl=3309.5)
    validate("modify_sltp", _buy(price_current=3310.0), spec, sl=3308.0)


def test_zero_stops_level_imposes_no_distance():
    """0 here is a MEASURED zero from the broker, not an unknown — this account
    genuinely has no minimum stop distance."""
    validate("modify_sltp", _buy(price_current=3310.0), _spec(stops_level=0), sl=3309.999)


# ------------------------------------------------------------- trade_mode gate


def test_a_disabled_symbol_refuses_everything():
    with pytest.raises(CommandError):
        validate("close", _buy(), _spec(trade_mode=0), volume=None)


def test_close_only_symbol_refuses_adding_volume_but_allows_closing():
    """SYMBOL_TRADE_MODE_CLOSE_ONLY = 3. Refusing the close here would again
    trap the human in a position."""
    spec = _spec(trade_mode=3)
    validate("close", _buy(), spec, volume=None)
    with pytest.raises(CommandError):
        validate("add_volume", _buy(), spec, volume=0.01)


def test_unknown_trade_mode_refuses_opening_but_allows_closing():
    """Rule 4 applied consistently: unknown is not permission to increase
    exposure, but it must never block reducing it."""
    spec = _spec(trade_mode=None)
    validate("close", _buy(), spec, volume=None)
    with pytest.raises(CommandError):
        validate("add_volume", _buy(), spec, volume=0.01)


# ------------------------------------------------------------- unknown things


def test_an_unknown_kind_is_refused():
    with pytest.raises(CommandError):
        validate("liquidate_everything", _buy(), _spec(), volume=None)


# --------------------------------------------------------------- build_request


def test_closing_a_buy_sells():
    req = build_request("close", _buy(volume=0.10), _SPEC)
    assert req.action is TradeAction.DEAL
    assert req.order_type is OrderType.SELL
    assert req.volume == 0.10


def test_closing_a_sell_buys():
    req = build_request("close", _sell(volume=0.10), _SPEC)
    assert req.order_type is OrderType.BUY


def test_a_close_carries_the_position_id():
    """On a HEDGING account this is the difference between closing a position
    and opening a second, opposite one. There is no more consequential field in
    the request."""
    req = build_request("close", _buy(position_id=111), _SPEC)
    assert req.position_id == 111


def test_a_partial_close_sends_the_requested_volume_not_the_full_one():
    req = build_request("close_partial", _buy(volume=0.10), _SPEC, volume=0.04)
    assert req.volume == 0.04
    assert req.position_id == 111


def test_a_close_omits_price_because_execution_is_market():
    """trade_exemode=2 (MARKET) on all three symbols, measured from the
    fixtures. The broker fills at its own price and ignores this field, so
    sending a `price_current` that is already stale by the time it lands buys
    nothing and invites TRADE_RETCODE_INVALID_PRICE / PRICE_OFF."""
    req = build_request("close", _buy(), _SPEC)
    assert req.price is None


def test_adding_volume_keeps_the_direction():
    req = build_request("add_volume", _buy(), _SPEC, volume=0.02)
    assert req.order_type is OrderType.BUY
    assert req.action is TradeAction.DEAL
    assert req.volume == 0.02


def test_adding_volume_does_not_carry_a_position_id():
    """HEDGING TRUTH, and the one thing about this feature the human must know:
    a market order in the SAME direction cannot grow an existing position on a
    hedging account — MT5 opens a SECOND position with its own ticket. Sending
    `position` here would be asking to close, which is the opposite of the
    intent. The journal will show two trades, not one larger one."""
    req = build_request("add_volume", _buy(position_id=111), _SPEC, volume=0.02)
    assert req.position_id is None


def test_modify_sltp_uses_the_sltp_action_and_no_volume():
    req = build_request("modify_sltp", _buy(), _SPEC, sl=3300.0, tp=3320.0)
    assert req.action is TradeAction.SLTP
    assert req.position_id == 111
    assert req.volume is None
    assert req.sl == 3300.0
    assert req.tp == 3320.0


def test_modify_sltp_passes_none_through_untouched():
    """None must survive as None all the way to `_to_bridge_request`, which
    omits it. Coercing it to 0.0 anywhere along the way silently clears a live
    stop-loss."""
    req = build_request("modify_sltp", _buy(), _SPEC, tp=3320.0)
    assert req.sl is None


def test_modify_sltp_passes_an_explicit_zero_through():
    req = build_request("modify_sltp", _buy(), _SPEC, sl=0.0)
    assert req.sl == 0.0


def test_request_picks_a_filling_mode_from_the_symbol_bitmask():
    """filling_mode=3 is FOK|IOC in SYMBOL_FILLING_* values, so FOK is chosen —
    NOT 3, which as an ORDER_FILLING_* value would be BOC and earn
    TRADE_RETCODE_INVALID_FILL."""
    req = build_request("close", _buy(), _SPEC)
    assert req.filling is OrderFilling.FOK


def test_sltp_request_needs_no_filling():
    """A modify is not a fill. Sending type_filling on an SLTP request is noise
    at best."""
    req = build_request("modify_sltp", _buy(), _SPEC, sl=3300.0)
    assert req.filling is None


def test_request_carries_the_verbatim_symbol_not_the_base():
    """CLAUDE.md rule 11: query MT5 with `symbol`, group stats by `symbol_base`.
    'XAUUSD' does not exist on this server."""
    req = build_request("close", _buy(), _SPEC)
    assert req.symbol == "XAUUSDc"


def test_build_request_validates_first():
    """`build_request` must not be a way around `validate`. A caller that forgot
    to validate still cannot produce an over-cap request."""
    with pytest.raises(CommandError):
        build_request("add_volume", _buy(), _SPEC, volume=99.0)


# ------------------------------------------------------------------- classify


def test_classify_done_is_done():
    assert classify(TradeRetcode.DONE) == "done"
    assert classify(TradeRetcode.PLACED) == "done"


def test_classify_partial_is_done():
    """It changed the account. See is_success's reasoning in adapter/base.py."""
    assert classify(TradeRetcode.DONE_PARTIAL) == "done"


def test_classify_everything_else_is_failed():
    for code in (TradeRetcode.REQUOTE, TradeRetcode.INVALID_STOPS, TradeRetcode.NO_MONEY):
        assert classify(code) == "failed"


def test_classify_none_is_failed_not_done():
    """No answer from the bridge is not success. It is also not proof of
    failure — the status is 'failed' but the human-facing error says so."""
    assert classify(None) == "failed"


def test_classify_accepts_a_raw_int():
    assert classify(10009) == "done"
    assert classify(99999) == "failed"
