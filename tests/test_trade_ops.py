"""M9 Phase 2 — the trade-execution side of the adapter boundary.

Written before the implementation (CLAUDE.md rule 7).

This is the phase where the project stops being read-only, so the tests are
mostly about the BOUNDARY holding, not about MT5 behaving:

  * our own enums and dataclasses cross the Protocol — never an MT5 int, never
    a bridge namedtuple (rules 1 and 12);
  * `TradeRetcode` is COMPLETE, because an unlisted code makes
    `TradeRetcode(result.retcode)` raise at the worst possible moment — right
    after an order was sent, when we most need to record what happened;
  * `FakeMT5Client` records every request, so every later phase can assert on
    WHAT WOULD HAVE BEEN SENT without a bridge, a terminal, or a cent at risk.

Nothing here talks to a bridge. Nothing here may ever talk to a bridge.
"""

from __future__ import annotations

import pytest

from journal.adapter.base import (
    MT5Client,
    OrderFilling,
    OrderType,
    SymbolInfo,
    TradeAction,
    TradeRequest,
    TradeResult,
    TradeRetcode,
    filling_for,
    is_success,
)
from journal.adapter.fake import FakeMT5Client
from journal.adapter.live import _from_bridge_result, _to_bridge_request

# ------------------------------------------------------------------- enums


def test_trade_action_values_match_the_bridge():
    """Values from siliconmetatrader5/__init__.py:142-147. `live.py` asserts
    these against the bridge at init; this pins them in the enum itself."""
    assert TradeAction.DEAL == 1
    assert TradeAction.SLTP == 6


def test_order_type_values_match_the_bridge():
    """__init__.py:68-69."""
    assert OrderType.BUY == 0
    assert OrderType.SELL == 1


def test_order_filling_values_match_the_bridge():
    """__init__.py:89-92. NOTE these are the ORDER_FILLING_* values that go INTO
    a request — not the SYMBOL_FILLING_* bitmask a symbol reports. See
    `filling_for` below; conflating the two is a real MT5 trap."""
    assert OrderFilling.FOK == 0
    assert OrderFilling.IOC == 1
    assert OrderFilling.RETURN == 2


def test_trade_retcode_is_complete():
    """The whole set the bridge exposes (__init__.py:222-258). Incomplete means
    `TradeRetcode(result.retcode)` raises ValueError the first time the broker
    returns a code we did not list — losing the record of an order that WAS
    ALREADY SENT. Exactly the reasoning DealType's comment gives."""
    expected = {
        10004, 10006, 10007, 10008, 10009, 10010, 10011, 10012, 10013, 10014,
        10015, 10016, 10017, 10018, 10019, 10020, 10021, 10022, 10023, 10024,
        10025, 10026, 10027, 10028, 10029, 10030, 10031, 10032, 10033, 10034,
        10035, 10036, 10038, 10039, 10040, 10041, 10042, 10043, 10044, 10045,
    }
    assert {int(m) for m in TradeRetcode} == expected


def test_retcode_names_are_readable():
    """The audit log and the UI both show this. A bare 10016 tells the human
    nothing; INVALID_STOPS tells them their SL was too close."""
    assert TradeRetcode(10009).name == "DONE"
    assert TradeRetcode(10016).name == "INVALID_STOPS"
    assert TradeRetcode(10018).name == "MARKET_CLOSED"


# ----------------------------------------------------------------- success


def test_is_success_accepts_done_and_placed():
    assert is_success(TradeRetcode.DONE)
    assert is_success(TradeRetcode.PLACED)


def test_is_success_accepts_done_partial():
    """A partial fill DID something at the broker. Treating it as a failure
    would leave the journal believing a position it actually changed is
    untouched — the caller records the ACTUAL filled volume separately."""
    assert is_success(TradeRetcode.DONE_PARTIAL)


def test_is_success_rejects_everything_else():
    for code in (
        TradeRetcode.REQUOTE, TradeRetcode.REJECT, TradeRetcode.INVALID_STOPS,
        TradeRetcode.NO_MONEY, TradeRetcode.MARKET_CLOSED, TradeRetcode.NO_CHANGES,
        TradeRetcode.INVALID_FILL, TradeRetcode.TIMEOUT,
    ):
        assert not is_success(code), code.name


def test_is_success_accepts_a_plain_int():
    """Callers read `result.retcode`, which is an int off the wire."""
    assert is_success(10009)
    assert not is_success(10016)


def test_is_success_of_an_unknown_code_is_false():
    """An unrecognised code is NOT success. Rule 4's spirit: unknown is not a
    green light, and it must not raise here either — this runs right after an
    order was sent."""
    assert not is_success(99999)


# ------------------------------------------------------- filling bitmask trap


def test_filling_for_prefers_fok_when_allowed():
    """A symbol's `filling_mode` is a BITMASK in SYMBOL_FILLING_* values
    (FOK=1, IOC=2) — NOT the ORDER_FILLING_* value (FOK=0, IOC=1) that goes
    into the request. Passing the bitmask straight through as a filling type is
    how you earn TRADE_RETCODE_INVALID_FILL. All three symbols on this account
    report 3 = FOK|IOC."""
    assert filling_for(3) is OrderFilling.FOK
    assert filling_for(1) is OrderFilling.FOK


def test_filling_for_falls_back_to_ioc():
    assert filling_for(2) is OrderFilling.IOC


def test_filling_for_unknown_is_none():
    """Rule 4: an un-refetched spec has filling_mode NULL. That is unknown, so
    the caller must decide — never silently assume FOK."""
    assert filling_for(None) is None
    assert filling_for(0) is None


# ---------------------------------------------------------------- dataclasses


def test_trade_request_carries_our_enums_not_mt5_ints():
    """Rule 12: the vocabulary crossing the Protocol is ours. `live.py` is the
    only place that turns these into bridge integers."""
    req = TradeRequest(
        action=TradeAction.SLTP, position_id=123, symbol="XAUUSDc",
        sl=3290.0, tp=3320.0,
    )
    assert isinstance(req.action, TradeAction)
    assert req.position_id == 123


def test_trade_request_is_frozen():
    """An intent must not be mutated between validation and sending."""
    req = TradeRequest(action=TradeAction.DEAL, symbol="XAUUSDc")
    with pytest.raises(Exception):
        req.symbol = "BTCUSDc"


def test_trade_request_defaults_sl_tp_to_none_not_zero():
    """Rule 4, and it is load-bearing here: None = leave the level untouched,
    0.0 = clear it. A default of 0.0 would silently WIPE a live stop-loss on
    every modify that only meant to set a take-profit."""
    req = TradeRequest(action=TradeAction.SLTP, position_id=1)
    assert req.sl is None
    assert req.tp is None


def test_trade_result_defaults_are_unknown():
    r = TradeResult()
    assert r.retcode is None
    assert r.deal is None
    assert r.volume is None


# ------------------------------------------------------------------ protocol


def test_fake_still_conforms_to_the_protocol():
    """The Protocol gained order_check/order_send; the fake must implement them
    or every downstream test loses its bridge-free guarantee."""
    assert isinstance(FakeMT5Client(), MT5Client)


# ---------------------------------------------------------------------- fake


def test_fake_records_every_request_it_is_sent():
    """This is what makes the rest of M9 testable: assert on what WOULD have
    been sent, with nothing at risk."""
    c = FakeMT5Client()
    req = TradeRequest(action=TradeAction.SLTP, position_id=9, symbol="XAUUSDc", sl=1.0)
    c.order_send(req)
    assert c.sent == [req]


def test_fake_records_checks_separately_from_sends():
    """A dry run must never be mistaken for a real send when a test asserts
    'nothing was sent'."""
    c = FakeMT5Client()
    req = TradeRequest(action=TradeAction.DEAL, position_id=9, symbol="XAUUSDc")
    c.order_check(req)
    assert c.checked == [req]
    assert c.sent == []


def test_fake_returns_done_by_default():
    """The happy path costs a test nothing to set up; failures are opt-in."""
    c = FakeMT5Client()
    res = c.order_send(TradeRequest(action=TradeAction.DEAL, symbol="XAUUSDc"))
    assert res.retcode == TradeRetcode.DONE


def test_fake_results_can_be_scripted_in_order():
    """Phase 4 needs to drive a broker that rejects, then succeeds."""
    c = FakeMT5Client()
    c.script_results(
        TradeResult(retcode=TradeRetcode.REQUOTE),
        TradeResult(retcode=TradeRetcode.DONE, deal=555, volume=0.01),
    )
    req = TradeRequest(action=TradeAction.DEAL, symbol="XAUUSDc")
    assert c.order_send(req).retcode == TradeRetcode.REQUOTE
    second = c.order_send(req)
    assert second.retcode == TradeRetcode.DONE
    assert second.deal == 555


def test_fake_can_raise_to_simulate_a_dead_bridge():
    """`journal live` must survive the container going away mid-command; it
    cannot be tested for that without a fake that can fail."""
    c = FakeMT5Client()
    c.script_results(ConnectionError("bridge gone"))
    with pytest.raises(ConnectionError):
        c.order_send(TradeRequest(action=TradeAction.DEAL, symbol="XAUUSDc"))


# --------------------------------------------------------------- symbol info


def test_symbol_info_exposes_the_order_validation_fields():
    """Phase 3 validates lot size and SL distance from these. Names mirror MT5's
    raw names so `_build` needs no rename map (base.py's stated contract)."""
    c = FakeMT5Client()
    info = c.symbol_info("XAUUSDc")
    assert info is not None
    assert info.volume_min == 0.01
    assert info.volume_max == 200.0
    assert info.volume_step == 0.01
    assert info.trade_stops_level == 0
    assert info.trade_freeze_level == 0
    assert info.trade_mode == 4          # SYMBOL_TRADE_MODE_FULL
    assert info.filling_mode == 3        # FOK|IOC


def test_symbol_info_fields_default_to_none_when_absent():
    """Rule 4 at the dataclass level: a partial fixture yields unknown, not 0."""
    info = SymbolInfo(name="XAUUSDc")
    assert info.volume_min is None
    assert info.trade_stops_level is None
    assert info.filling_mode is None


# ------------------------------------------------- live.py request/result map
# These import live.py, which imports the bridge PACKAGE — installed, so the
# import succeeds — but never construct a LiveMT5Client and never open a socket.
# Nothing here needs anything listening on :8001.


def test_enum_values_never_reach_the_bridge_as_enums():
    """`order_send` is bridge-side `eval(repr(request))`
    (siliconmetatrader5/__init__.py:772). An IntEnum's repr is
    `<TradeAction.SLTP: 6>`, which is a SyntaxError on the far side — so every
    value must be a plain builtin. This is not cosmetic: without the int()
    coercion nothing sends at all."""
    payload = _to_bridge_request(
        TradeRequest(
            action=TradeAction.SLTP, position_id=1, symbol="XAUUSDc",
            order_type=OrderType.SELL, filling=OrderFilling.FOK,
        )
    )
    for key, value in payload.items():
        assert type(value) in (int, float, str), f"{key}={value!r} is {type(value)}"
    # repr must be round-trippable, which is precisely what the bridge does.
    assert eval(repr(payload)) == payload


def test_none_sl_is_omitted_not_sent_as_zero():
    """THE bug this mapping exists to prevent. MT5 reads sl=0.0 as 'clear the
    stop-loss'. A modify that only sets a take-profit must not mention sl at
    all, or it silently wipes a live stop on a real position."""
    payload = _to_bridge_request(
        TradeRequest(action=TradeAction.SLTP, position_id=1, tp=3320.0)
    )
    assert "sl" not in payload
    assert payload["tp"] == 3320.0


def test_explicit_zero_sl_is_sent():
    """The other half of rule 4: 0.0 means 'clear it', and that intent must
    survive the mapping. Omitting it here would make clearing a stop impossible."""
    payload = _to_bridge_request(
        TradeRequest(action=TradeAction.SLTP, position_id=1, sl=0.0)
    )
    assert payload["sl"] == 0.0


def test_position_id_maps_to_the_field_mt5_calls_position():
    """MT5's request key is `position`, not `position_id`. On this HEDGING
    account, a close missing it does not close anything — it opens a second,
    opposite position."""
    payload = _to_bridge_request(
        TradeRequest(action=TradeAction.DEAL, position_id=987654, symbol="XAUUSDc")
    )
    assert payload["position"] == 987654
    assert "position_id" not in payload


def test_order_type_maps_to_the_field_mt5_calls_type():
    payload = _to_bridge_request(
        TradeRequest(action=TradeAction.DEAL, order_type=OrderType.SELL)
    )
    assert payload["type"] == 1
    assert "order_type" not in payload


def test_filling_maps_to_type_filling():
    payload = _to_bridge_request(
        TradeRequest(action=TradeAction.DEAL, filling=OrderFilling.IOC)
    )
    assert payload["type_filling"] == 1


def test_an_empty_request_sends_an_empty_dict():
    """No field is invented with a default. Everything MT5 receives was asked
    for explicitly by the caller."""
    assert _to_bridge_request(TradeRequest()) == {}


class _FakeMqlResult:
    """Shaped like the bridge's MqlTradeResult netref."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def _asdict(self):
        return dict(self.__dict__)


def test_result_mapping_reads_the_broker_verdict():
    res = _from_bridge_result(
        _FakeMqlResult(
            retcode=10009, deal=777, order=888, volume=0.01,
            price=3301.5, comment="Request executed", request_id=42,
        )
    )
    assert res.retcode == 10009
    assert res.deal == 777
    assert res.volume == 0.01
    assert res.comment == "Request executed"
    assert res.raw["retcode"] == 10009      # forward-compat dump kept


def test_result_mapping_keeps_the_actual_partial_fill_volume():
    """A DONE_PARTIAL filled less than was asked. If the mapping substituted the
    requested volume, the journal would record a position size that does not
    exist at the broker."""
    res = _from_bridge_result(_FakeMqlResult(retcode=10010, volume=0.005))
    assert res.retcode == TradeRetcode.DONE_PARTIAL
    assert res.volume == 0.005


def test_result_mapping_of_none_is_unknown_not_failure():
    """The bridge returning nothing does NOT prove the order failed — it may
    have reached the broker. Rule 4: that is unknown, and unknown must not read
    as success."""
    res = _from_bridge_result(None)
    assert res.retcode is None
    assert not is_success(res.retcode)


def test_result_mapping_survives_missing_fields():
    """An `order_check` result has no `deal`. Reading it must not explode while
    recording what happened to a real order."""
    res = _from_bridge_result(_FakeMqlResult(retcode=10009))
    assert res.retcode == 10009
    assert res.deal is None


# ------------------------------------------------------------ spec persistence


def test_sync_stores_the_order_validation_specs(tmp_path):
    """Phase 3 validates against the DB, not against a live symbol_info call —
    so a spec that never lands in `symbol_specs` makes every command
    unvalidatable and therefore rejected."""
    from journal.ingest.deals import sync
    from journal.store.db import connect

    conn = connect(tmp_path / "j.db")
    try:
        sync(FakeMT5Client(), conn)
        row = conn.execute(
            "SELECT volume_min, volume_max, volume_step, stops_level, "
            "freeze_level, trade_mode, filling_mode "
            "FROM symbol_specs WHERE symbol = 'XAUUSDc'"
        ).fetchone()
        assert row is not None
        assert row["volume_min"] == 0.01
        assert row["volume_max"] == 200.0
        assert row["volume_step"] == 0.01
        # Genuinely 0 on this broker — no minimum stop distance is enforced.
        # That is a MEASURED zero, not an unknown one (rule 4).
        assert row["stops_level"] == 0
        assert row["freeze_level"] == 0
        assert row["trade_mode"] == 4      # SYMBOL_TRADE_MODE_FULL
        assert row["filling_mode"] == 3    # FOK|IOC
    finally:
        conn.close()


def test_resync_refreshes_the_specs(tmp_path):
    """Brokers change these; the upsert must overwrite, not ignore. A stale
    volume_max is a command validated against a limit that no longer exists."""
    from journal.ingest.deals import sync
    from journal.store.db import connect

    conn = connect(tmp_path / "j.db")
    try:
        sync(FakeMT5Client(), conn)
        conn.execute("UPDATE symbol_specs SET volume_max = 1.0 WHERE symbol='XAUUSDc'")
        conn.commit()
        sync(FakeMT5Client(), conn)
        row = conn.execute(
            "SELECT volume_max FROM symbol_specs WHERE symbol='XAUUSDc'"
        ).fetchone()
        assert row["volume_max"] == 200.0
    finally:
        conn.close()
