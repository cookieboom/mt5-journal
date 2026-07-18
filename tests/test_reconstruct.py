"""M2 reconstruction — deals -> trades, the hard milestone.

Every §5 case in docs/mt5-deal-model.md, plus the Trap-1 None guard, the two
balance identities of §6, and the killer test over the real 140-deal fixture.
Written before the implementation (CLAUDE.md rule 7): the tests certify the spec,
not the code.

Most cases drive the PURE `reconstruct(deals, orders, specs)` with hand-built
`Deal`/`Order` objects — no DB, no bridge. The DB-level cases (`rebuild`
idempotency, the killer identity, verify's empty-trades branches) use FakeMT5Client
against tests/fixtures, exactly as the M1 suite does.
"""

from __future__ import annotations

import logging

import pytest

from journal.adapter.base import Deal, DealEntry, DealReason, DealType, Order
from journal.adapter.fake import FakeMT5Client
from journal.domain.reconstruct import SlTpSnapshot, SymbolSpec, Trade, rebuild, reconstruct
from journal.ingest.deals import add_reconciliation, sync, verify
from journal.store.db import connect

_LOGIN = 0
_GAP = 14.50
_TOL = 0.01
_BALANCE = 6047.22

# XAUUSDc, measured (docs §7). risk of a 1.000 price move on 0.10 lot:
#   (1.000 / 0.001) * 0.1 * 0.10 = 10.0 USC per 1.000 of distance.
_XAU_SPEC = SymbolSpec(symbol="XAUUSDc", symbol_base="XAUUSD",
                       tick_size=0.001, tick_value=0.1, contract_size=1.0)
_SPECS = {"XAUUSDc": _XAU_SPEC}


# --- builders ---------------------------------------------------------------


def _deal(pid, entry, dtype, price, volume, ticket, *, order=0, time_msc=0,
          reason=DealReason.CLIENT, profit=0.0, commission=0.0, swap=0.0,
          fee=0.0, magic=0, symbol="XAUUSDc"):
    return Deal(
        ticket=ticket, order=order, position_id=pid, entry=int(entry),
        type=int(dtype), price=price, volume=volume, time_msc=time_msc,
        reason=int(reason), profit=profit, commission=commission, swap=swap,
        fee=fee, magic=magic, symbol=symbol,
    )


def _order(ticket, *, sl=0.0, tp=0.0, symbol="XAUUSDc"):
    return Order(ticket=ticket, sl=sl, tp=tp, symbol=symbol)


def _snap(observed_msc, *, sl=None, tp=None, volume=None):
    return SlTpSnapshot(observed_msc=observed_msc, sl=sl, tp=tp, volume=volume)


def _one(trades) -> Trade:
    assert len(trades) == 1, f"expected exactly one trade, got {len(trades)}"
    return trades[0]


# --- §5 unit cases ----------------------------------------------------------


def test_simple_long():
    deals = [
        _deal(555, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1000),
        _deal(555, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=5000,
              profit=100.0),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))
    assert t.direction == "buy"
    assert t.status == "closed"
    assert abs(t.open_price - 4000.0) < 1e-9
    assert abs(t.close_price - 4010.0) < 1e-9
    assert abs(t.volume - 0.10) < 1e-9
    assert t.open_time_msc == 1000 and t.close_time_msc == 5000
    assert t.duration_s == 4  # (5000 - 1000) ms
    assert t.deal_count == 2
    assert abs(t.net_profit - 100.0) < 1e-9


def test_simple_short():
    deals = [
        _deal(9, DealEntry.IN, DealType.SELL, 4010.0, 0.10, 1, time_msc=1000),
        _deal(9, DealEntry.OUT, DealType.BUY, 4000.0, 0.10, 2, time_msc=2000,
              profit=100.0),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))
    assert t.direction == "sell"
    assert t.status == "closed"


def test_partial_fill_vwap_entry():
    # 0.06 @ 4000 + 0.04 @ 4010 -> vwap = (240 + 160.4) / 0.10 = 4004.0
    deals = [
        _deal(1, DealEntry.IN, DealType.BUY, 4000.0, 0.06, 1, time_msc=1000),
        _deal(1, DealEntry.IN, DealType.BUY, 4010.0, 0.04, 2, time_msc=1500),
        _deal(1, DealEntry.OUT, DealType.SELL, 4020.0, 0.10, 3, time_msc=9000),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))
    assert abs(t.open_price - 4004.0) < 1e-9
    assert abs(t.volume - 0.10) < 1e-9
    assert t.status == "closed"


def test_partial_close_vwap_exit_and_last_close_time():
    # one IN 0.30, three OUTs 0.10 each at 4010/4020/4030 -> vwap 4020, last t=8000
    deals = [
        _deal(2, DealEntry.IN, DealType.BUY, 4000.0, 0.30, 1, time_msc=1000),
        _deal(2, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=6000),
        _deal(2, DealEntry.OUT, DealType.SELL, 4030.0, 0.10, 3, time_msc=8000),
        _deal(2, DealEntry.OUT, DealType.SELL, 4020.0, 0.10, 4, time_msc=7000),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))
    assert t.status == "closed"
    assert abs(t.close_price - 4020.0) < 1e-9
    assert t.close_time_msc == 8000  # the LAST out by time, not the last in the list


def test_partial_close_leaving_remainder_open():
    # IN 0.30, OUT 0.20 -> still partly open. r_multiple must be NULL even though the
    # SL is known: realised P&L is incomplete, so R is meaningless (docs §5).
    deals = [
        _deal(3, DealEntry.IN, DealType.BUY, 4000.0, 0.30, 1, order=77, time_msc=1000),
        _deal(3, DealEntry.OUT, DealType.SELL, 4010.0, 0.20, 2, time_msc=6000,
              profit=20.0),
    ]
    orders = {77: _order(77, sl=3990.0)}
    t = _one(reconstruct(deals, orders, _SPECS))
    assert t.status == "partially_open"
    assert t.sl_initial is not None       # SL is known...
    assert t.risk_amount is not None      # ...so risk is computable...
    assert t.r_multiple is None           # ...but R is not, while it is still open


def test_still_open():
    deals = [
        _deal(4, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1000,
              commission=-2.0),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))
    assert t.status == "open"
    assert t.close_time_msc is None
    assert t.close_price is None
    assert t.duration_s is None
    assert t.r_multiple is None
    assert abs(t.net_profit - (-2.0)) < 1e-9  # entry cost is realised cash already


def test_orphan_out_with_no_in_is_skipped(caplog):
    deals = [
        _deal(5, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 1, time_msc=5000),
    ]
    with caplog.at_level(logging.WARNING):
        trades = reconstruct(deals, {}, _SPECS)
    assert trades == []                       # skipped, not crashed
    assert any("5" in r.message for r in caplog.records)  # warned, naming the pid


def test_balance_and_credit_deals_are_ignored():
    deals = [
        _deal(0, DealEntry.IN, DealType.BALANCE, 0.0, 0.0, 1, symbol="",
              profit=5000.0),  # a deposit: type BALANCE, position_id 0
        _deal(6, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 2, time_msc=1000),
        _deal(6, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 3, time_msc=2000),
    ]
    trades = reconstruct(deals, {}, _SPECS)
    assert len(trades) == 1                    # only the real trade
    assert trades[0].position_id == 6


def test_costs_scattered_across_deals_are_all_summed():
    # commission on IN, swap on OUT, profit on OUT -> net = 10 - 2 - 1 = 7 (Trap 9)
    deals = [
        _deal(7, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1000,
              commission=-2.0),
        _deal(7, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2000,
              profit=10.0, swap=-1.0),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))
    assert abs(t.commission - (-2.0)) < 1e-9
    assert abs(t.swap - (-1.0)) < 1e-9
    assert abs(t.profit_gross - 10.0) < 1e-9
    assert abs(t.net_profit - 7.0) < 1e-9


def test_opening_order_sl_zero_gives_null_sl_and_null_r():
    # Trap 6: sl == 0.0 means "not set on this order", NOT "no SL" -> NULL.
    deals = [
        _deal(8, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=100, time_msc=1),
        _deal(8, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {100: _order(100, sl=0.0)}
    t = _one(reconstruct(deals, orders, _SPECS))
    assert t.sl_initial is None
    assert t.sl_source == "unknown"
    assert t.risk_amount is None
    assert t.r_multiple is None


def test_opening_order_missing_gives_null_sl_no_crash():
    deals = [
        _deal(9, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=999, time_msc=1),
        _deal(9, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2),
    ]
    t = _one(reconstruct(deals, {}, _SPECS))  # order 999 not in the map
    assert t.sl_initial is None
    assert t.sl_source == "unknown"


def test_sl_from_order_gives_risk_and_r():
    # SL known -> risk & R computable. entry 4000, sl 3999, 0.10 lot:
    #   risk = (1.000 / 0.001) * 0.1 * 0.10 = 10.0 USC; net 20 -> R = 2.0
    deals = [
        _deal(10, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=200, time_msc=1),
        _deal(10, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2,
              profit=20.0),
    ]
    orders = {200: _order(200, sl=3999.0, tp=4050.0)}
    t = _one(reconstruct(deals, orders, _SPECS))
    assert t.sl_source == "order"
    assert abs(t.sl_initial - 3999.0) < 1e-9
    assert abs(t.tp_initial - 4050.0) < 1e-9   # tp_initial mirrors sl_initial
    assert abs(t.risk_amount - 10.0) < 1e-9
    assert abs(t.r_multiple - 2.0) < 1e-9


def test_sl_exactly_at_entry_is_known_zero_risk_not_unknown():
    # Trap 6 table: SL sitting on the entry price is a KNOWN risk of 0.0 — distinct
    # from unknown (NULL). risk_amount stays 0.0 (a real fact); r_multiple is NULL
    # because R is undefined, NOT because we know nothing. And it must not
    # ZeroDivisionError (which would abort the whole rebuild, not one row).
    deals = [
        _deal(13, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=300, time_msc=1),
        _deal(13, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {300: _order(300, sl=4000.0)}  # SL == entry
    t = _one(reconstruct(deals, orders, _SPECS))
    assert t.sl_initial is not None                  # SL is known...
    assert t.risk_amount is not None and t.risk_amount == 0.0  # ...and it is zero
    assert t.r_multiple is None                       # R undefined, but no crash


# --- M4: SL/TP resolved from the poller when the order gives nothing --------


def test_poller_earliest_nonzero_becomes_sl_initial():
    # Order shows no SL (the 62-discretionary-trade case, docs §7). The poller
    # observed no SL at t=1, then a real one at t=2 -- Trap 6's "actual first SL,
    # regardless of how it was set". Risk & R become computable, same as the
    # order-derived case.
    deals = [
        _deal(20, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=400, time_msc=1),
        _deal(20, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {400: _order(400, sl=0.0)}
    snaps = {20: [_snap(1, sl=0.0), _snap(2, sl=3999.0)]}
    t = _one(reconstruct(deals, orders, _SPECS, snapshots=snaps))
    assert t.sl_source == "poller"
    assert abs(t.sl_initial - 3999.0) < 1e-9
    assert abs(t.risk_amount - 10.0) < 1e-9   # (1.0/0.001)*0.1*0.10
    assert abs(t.r_multiple - 1.0) < 1e-9


def test_poller_confirmed_no_sl_stores_zero_but_risk_stays_none():
    # THE key guard test (plan decision 2). Every poller observation shows sl=0
    # -> a POSITIVE confirmation (rule 4: "0 means none set"), stored as a real
    # 0.0 -- but that 0.0 must NEVER reach risk_amount() as a price, or it
    # computes a huge garbage number (|4000 - 0|/0.001 * 0.1 * 0.10 = 4000.0,
    # not the correct "undefined"). risk_amount/r_multiple must be None, exactly
    # like the fully-unknown case -- confirmed-absent is not the same as
    # zero-risk (Trap 6: an SL AT entry is zero risk; NO SL is unbounded risk).
    deals = [
        _deal(21, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=401, time_msc=1),
        _deal(21, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {401: _order(401, sl=0.0)}
    snaps = {21: [_snap(1, sl=0.0), _snap(2, sl=0.0)]}
    t = _one(reconstruct(deals, orders, _SPECS, snapshots=snaps))
    assert t.sl_initial == 0.0            # stored: a real, auditable fact
    assert t.sl_source == "poller"
    assert t.risk_amount is None          # NOT a huge number, NOT a false 0
    assert t.r_multiple is None


def test_no_poller_coverage_is_unchanged_m2_behavior():
    # No snapshots at all for this position (poller wasn't running, or the trade
    # closed before the poller ever saw it) -> falls back exactly as M2 did.
    deals = [
        _deal(22, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=402, time_msc=1),
        _deal(22, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2),
    ]
    orders = {402: _order(402, sl=0.0)}
    t = _one(reconstruct(deals, orders, _SPECS, snapshots={}))
    assert t.sl_initial is None
    assert t.sl_source == "unknown"
    assert t.risk_amount is None
    assert t.r_multiple is None
    # Omitting `snapshots` entirely must behave identically (backward compat).
    t2 = _one(reconstruct(deals, orders, _SPECS))
    assert t2.sl_initial is None and t2.sl_source == "unknown"


def test_order_sl_wins_over_poller_data():
    # Priority regression guard: when the order DOES give a real SL, poller data
    # present for the same position must be irrelevant -- order still wins.
    deals = [
        _deal(23, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=403, time_msc=1),
        _deal(23, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {403: _order(403, sl=3990.0)}
    snaps = {23: [_snap(1, sl=3900.0)]}  # a DIFFERENT value -- must be ignored
    t = _one(reconstruct(deals, orders, _SPECS, snapshots=snaps))
    assert t.sl_source == "order"
    assert abs(t.sl_initial - 3990.0) < 1e-9


def test_poller_scans_past_leading_zeros_to_first_real_price():
    # [0, 0, 3990, 3990] must resolve to 3990, not misclassify as "confirmed
    # none" by looking only at the first row.
    deals = [
        _deal(24, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=404, time_msc=1),
        _deal(24, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {404: _order(404, sl=0.0)}
    snaps = {24: [_snap(1, sl=0.0), _snap(2, sl=0.0), _snap(3, sl=3990.0), _snap(4, sl=3990.0)]}
    t = _one(reconstruct(deals, orders, _SPECS, snapshots=snaps))
    assert t.sl_source == "poller"
    assert abs(t.sl_initial - 3990.0) < 1e-9


def test_poller_observed_sl_at_entry_is_known_zero_risk():
    # Mirrors test_sl_exactly_at_entry_is_known_zero_risk_not_unknown, but the
    # price comes from the poller instead of the order.
    deals = [
        _deal(25, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=405, time_msc=1),
        _deal(25, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {405: _order(405, sl=0.0)}
    snaps = {25: [_snap(1, sl=4000.0)]}  # == open_price
    t = _one(reconstruct(deals, orders, _SPECS, snapshots=snaps))
    assert t.sl_source == "poller"
    assert t.risk_amount is not None and t.risk_amount == 0.0
    assert t.r_multiple is None  # undefined, not a crash


def test_tp_resolves_from_poller_independently_of_sl():
    # A position can have SL confirmed-none but a real TP -- they resolve
    # independently (plan decision 1). tp has no dedicated source column.
    deals = [
        _deal(26, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, order=406, time_msc=1),
        _deal(26, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, profit=10.0),
    ]
    orders = {406: _order(406, sl=0.0, tp=0.0)}
    snaps = {26: [_snap(1, sl=0.0, tp=0.0), _snap(2, sl=0.0, tp=4050.0)]}
    t = _one(reconstruct(deals, orders, _SPECS, snapshots=snaps))
    assert t.sl_initial == 0.0 and t.sl_source == "poller"   # confirmed none
    assert abs(t.tp_initial - 4050.0) < 1e-9                  # real TP recovered


def test_deal_with_null_time_msc_is_filtered_not_sorted_as_1970():
    # Amendment #2: a trade deal with time_msc=None must be rejected by the Trap-1
    # filter, never sorted as an epoch-0 (1970) timestamp — the silent error the whole
    # doc guards against. The one clean trade survives; no crash.
    deals = [
        _deal(14, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=None),  # malformed
        _deal(15, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 2, time_msc=1000),
        _deal(15, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 3, time_msc=2000),
    ]
    trades = reconstruct(deals, {}, _SPECS)
    assert [t.position_id for t in trades] == [15]  # 14 dropped, 15 intact


def test_close_reason_read_off_last_out():
    for reason in (DealReason.SL, DealReason.TP, DealReason.CLIENT):
        deals = [
            _deal(11, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1),
            _deal(11, DealEntry.OUT, DealType.SELL, 4005.0, 0.05, 2, time_msc=2,
                  reason=DealReason.CLIENT),
            _deal(11, DealEntry.OUT, DealType.SELL, 4010.0, 0.05, 3, time_msc=3,
                  reason=reason),  # the LAST out carries the discipline metric
        ]
        t = _one(reconstruct(deals, {}, _SPECS))
        assert t.close_reason == int(reason)


def test_inout_raises_naming_position_id():
    deals = [
        _deal(4242, DealEntry.INOUT, DealType.BUY, 4000.0, 0.10, 1, time_msc=1),
    ]
    with pytest.raises(NotImplementedError, match="4242"):
        reconstruct(deals, {}, _SPECS)


def test_out_by_raises_naming_position_id():
    # OUT_BY must be caught BEFORE the OUT path — its volume/price look plausible and
    # would silently corrupt the VWAP exit if it fell through (Trap 5).
    deals = [
        _deal(7373, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1),
        _deal(7373, DealEntry.OUT_BY, DealType.SELL, 4010.0, 0.10, 2, time_msc=2),
    ]
    with pytest.raises(NotImplementedError, match="7373"):
        reconstruct(deals, {}, _SPECS)


def test_two_hedged_positions_same_symbol_do_not_contaminate():
    # Hedging: a long and a short open on XAUUSDc at once -> two independent trades.
    deals = [
        _deal(100, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1000),
        _deal(200, DealEntry.IN, DealType.SELL, 4005.0, 0.20, 2, time_msc=1100),
        _deal(200, DealEntry.OUT, DealType.BUY, 3995.0, 0.20, 3, time_msc=3000,
              profit=200.0),
        _deal(100, DealEntry.OUT, DealType.SELL, 4020.0, 0.10, 4, time_msc=4000,
              profit=200.0),
    ]
    trades = reconstruct(deals, {}, _SPECS)
    by_pid = {t.position_id: t for t in trades}
    assert set(by_pid) == {100, 200}
    assert by_pid[100].direction == "buy" and abs(by_pid[100].volume - 0.10) < 1e-9
    assert by_pid[200].direction == "sell" and abs(by_pid[200].volume - 0.20) < 1e-9
    assert abs(by_pid[100].open_price - 4000.0) < 1e-9  # not blended with 200's price
    assert abs(by_pid[200].open_price - 4005.0) < 1e-9


def test_symbol_base_normalised():
    deals = [
        _deal(1, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1, symbol="XAUUSDc"),
        _deal(1, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 2, time_msc=2, symbol="XAUUSDc"),
        _deal(2, DealEntry.IN, DealType.BUY, 1.3, 0.10, 3, time_msc=1, symbol="USDCAD"),
        _deal(2, DealEntry.OUT, DealType.SELL, 1.31, 0.10, 4, time_msc=2, symbol="USDCAD"),
    ]
    by_pid = {t.position_id: t for t in reconstruct(deals, {}, _SPECS)}
    assert by_pid[1].symbol == "XAUUSDc" and by_pid[1].symbol_base == "XAUUSD"
    # USDCAD has no 'c' suffix to strip -> unchanged (must not eat a real letter).
    assert by_pid[2].symbol == "USDCAD" and by_pid[2].symbol_base == "USDCAD"


def test_trap1_none_position_id_is_filtered_explicitly():
    # A malformed deal with position_id=None must NOT survive the filter. `!= 0`
    # would let None through; the filter is a bare truthiness check on position_id.
    deals = [
        _deal(None, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 1, time_msc=1),
        _deal(12, DealEntry.IN, DealType.BUY, 4000.0, 0.10, 2, time_msc=1),
        _deal(12, DealEntry.OUT, DealType.SELL, 4010.0, 0.10, 3, time_msc=2),
    ]
    trades = reconstruct(deals, {}, _SPECS)  # must not crash on the None deal
    assert len(trades) == 1
    assert trades[0].position_id == 12


# --- DB-level cases: rebuild, the killer identity, verify branches ----------


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


@pytest.fixture
def client():
    return FakeMT5Client()


# Everything except these differs BY DESIGN across a rebuild: `id` is AUTOINCREMENT
# and renumbers after the DELETE, `rebuilt_at` is a wall-clock stamp.
_REBUILD_VOLATILE = {"id", "rebuilt_at"}


def _trade_value_cols(conn):
    """Every trades column except the two that are expected to differ. Derived from
    PRAGMA so a newly-populated column is compared automatically — a hand-listed set
    would silently go stale the next time reconstruction fills another field."""
    return [
        r["name"]
        for r in conn.execute("PRAGMA table_info(trades)")
        if r["name"] not in _REBUILD_VOLATILE
    ]


def _snapshot(conn, cols):
    return {
        (r["account_login"], r["position_id"], r["segment"]): tuple(r[c] for c in cols)
        for r in conn.execute("SELECT * FROM trades")
    }


def test_rebuild_produces_68_trades(conn, client):
    sync(client, conn)
    rebuild(conn)
    n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert n == 68


def test_rebuild_is_idempotent_except_id_and_rebuilt_at(conn, client):
    sync(client, conn)
    rebuild(conn)
    cols = _trade_value_cols(conn)   # every populated column, introspected — not listed
    first = _snapshot(conn, cols)
    ids_first = {r["id"] for r in conn.execute("SELECT id FROM trades")}

    rebuild(conn)  # second rebuild: DELETE all + re-INSERT, never UPDATE
    second = _snapshot(conn, cols)
    ids_second = {r["id"] for r in conn.execute("SELECT id FROM trades")}

    assert first == second          # every value column identical, keyed on the tuple
    assert ids_first != ids_second  # id is AUTOINCREMENT — expected to renumber


def test_rebuild_reads_typed_columns_not_raw_json(conn, client):
    # Amendment 2: rebuild must survive MT5 adding an unknown field to raw_json. Poison
    # every raw_json with a field no dataclass knows; rebuild must not choke on it.
    sync(client, conn)
    conn.execute(
        "UPDATE deals_raw SET raw_json = json_set(raw_json, '$.some_future_field', 1)"
    )
    conn.execute(
        "UPDATE orders_raw SET raw_json = json_set(raw_json, '$.some_future_field', 1)"
    )
    conn.commit()
    rebuild(conn)  # would raise TypeError if it did Deal(**json.loads(raw_json))
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 68


def test_rebuild_applies_poller_snapshots_scanning_past_leading_zeros(conn, client):
    # DB-level version of test_poller_scans_past_leading_zeros_to_first_real_price
    # -- exercises _load_sl_snapshots' ORDER BY (position_id, observed_msc)
    # against a real discretionary trade from the fixture, not a hand-built one.
    sync(client, conn)
    rebuild(conn)
    row = conn.execute(
        "SELECT position_id, open_price FROM trades WHERE sl_source = 'unknown' LIMIT 1"
    ).fetchone()
    assert row is not None, "fixture must contain at least one discretionary trade"
    pid, open_price = row["position_id"], row["open_price"]

    # An early zero (no SL yet), then a real SL moments later. The loader must
    # preserve this order so resolution scans past the leading zero.
    conn.execute(
        "INSERT INTO sl_tp_snapshots (account_login, position_id, observed_msc, sl, tp, volume) "
        "VALUES (?, ?, 1, 0.0, 0.0, 0.1)",
        (_LOGIN, pid),
    )
    conn.execute(
        "INSERT INTO sl_tp_snapshots (account_login, position_id, observed_msc, sl, tp, volume) "
        "VALUES (?, ?, 2, ?, 0.0, 0.1)",
        (_LOGIN, pid, open_price - 5.0),
    )
    conn.commit()

    rebuild(conn)
    t = conn.execute(
        "SELECT sl_source, sl_initial FROM trades WHERE position_id = ?", (pid,)
    ).fetchone()
    assert t["sl_source"] == "poller"
    assert abs(t["sl_initial"] - (open_price - 5.0)) < 1e-9


def test_rebuild_idempotent_with_poller_snapshots_present(conn, client):
    sync(client, conn)
    rebuild(conn)
    pid = conn.execute(
        "SELECT position_id FROM trades WHERE sl_source = 'unknown' LIMIT 1"
    ).fetchone()["position_id"]
    conn.execute(
        "INSERT INTO sl_tp_snapshots (account_login, position_id, observed_msc, sl, tp, volume) "
        "VALUES (?, ?, 1, 0.0, 0.0, 0.1)",
        (_LOGIN, pid),
    )
    conn.commit()

    rebuild(conn)
    cols = _trade_value_cols(conn)
    first = _snapshot(conn, cols)
    rebuild(conn)  # sl_tp_snapshots is append-only + read-only here: must reproduce exactly
    second = _snapshot(conn, cols)
    assert first == second


def test_killer_identity_2_holds_with_poller_snapshots_present(conn, client):
    # sl_tp_snapshots feeds sl_initial/risk/r_multiple only -- none of those enter
    # the cash partition (§6 identity 2 = SUM(net_profit) + non-trade cash). This
    # proves that structurally, not just by assertion.
    sync(client, conn)
    rebuild(conn)
    pid = conn.execute(
        "SELECT position_id FROM trades WHERE sl_source = 'unknown' LIMIT 1"
    ).fetchone()["position_id"]
    conn.execute(
        "INSERT INTO sl_tp_snapshots (account_login, position_id, observed_msc, sl, tp, volume) "
        "VALUES (?, ?, 1, 0.0, 0.0, 0.1)",
        (_LOGIN, pid),
    )
    conn.commit()
    rebuild(conn)

    add_reconciliation(
        conn, _LOGIN, _GAP, effective_msc=1783745936454,
        reason="Broker archived deals; underlying deals unrecoverable.",
        evidence="correction deal 1399033630, comment 'Archived deals'",
    )
    v = verify(conn)
    assert v.passed
    assert abs(v.residual2) < _TOL
    assert abs(v.trades_net - 63.72) < _TOL   # unchanged by the poller data


# --- M5: MAE/MFE, wired via rebuild()'s post-reconstruct() _fill_excursions ---


def test_rebuild_populates_mae_mfe_with_real_candle_coverage(conn, client):
    sync(client, conn)
    rebuild(conn)
    row = conn.execute(
        "SELECT position_id, symbol, open_time_msc, close_time_msc, duration_s, "
        "open_price, direction FROM trades WHERE status = 'closed' LIMIT 1"
    ).fetchone()
    assert row is not None
    from journal.render.chart import choose_timeframe, window_for
    tf = choose_timeframe(row["duration_s"])
    from_msc, to_msc = window_for(row["open_time_msc"], row["close_time_msc"], tf)
    # One bar covering the whole window with a known, controlled range.
    conn.execute(
        "INSERT INTO candles (symbol, timeframe, time_msc, open, high, low, close, "
        "tick_volume) VALUES (?, ?, ?, ?, ?, ?, ?, 10)",
        (row["symbol"], tf, from_msc, row["open_price"], row["open_price"] + 5.0,
         row["open_price"] - 3.0, row["open_price"], ),
    )
    conn.commit()

    rebuild(conn)
    after = conn.execute(
        "SELECT mae, mfe FROM trades WHERE position_id = ?", (row["position_id"],)
    ).fetchone()
    assert after["mae"] is not None and after["mfe"] is not None
    if row["direction"] == "buy":
        assert abs(after["mae"] - 3.0) < 1e-9
        assert abs(after["mfe"] - 5.0) < 1e-9
    else:
        assert abs(after["mae"] - 5.0) < 1e-9
        assert abs(after["mfe"] - 3.0) < 1e-9


def test_rebuild_no_candles_mae_mfe_stay_null(conn, client):
    sync(client, conn)
    rebuild(conn)  # no candles seeded anywhere
    rows = conn.execute(
        "SELECT mae, mfe, mae_r, mfe_r FROM trades WHERE status = 'closed'"
    ).fetchall()
    assert rows  # sanity: real trades exist
    assert all(r["mae"] is None and r["mfe"] is None for r in rows)
    assert all(r["mae_r"] is None and r["mfe_r"] is None for r in rows)


def test_open_trade_mae_mfe_stay_null_despite_candle_coverage(conn, client):
    sync(client, conn)
    # An IN-only deal (no OUT) -> an open trade -- excursion-so-far would be
    # incomplete and misleading, so it must stay NULL regardless of how much
    # candle coverage exists (mirrors r_multiple's status=='closed' gate).
    open_t = 1_900_000_000_000
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900010,0,999001,'XAUUSDc',0,0,0,0,0.1,4000.0,0,0,0,0,?,'{}',?)",
        (_LOGIN, open_t, open_t),
    )
    conn.commit()
    for i in range(-5, 5):
        conn.execute(
            "INSERT INTO candles (symbol,timeframe,time_msc,open,high,low,close,"
            "tick_volume) VALUES ('XAUUSDc','M1',?,4000,4010,3990,4000,10)",
            (open_t + i * 60_000,),
        )
    conn.commit()

    rebuild(conn)
    row = conn.execute(
        "SELECT status, mae, mfe FROM trades WHERE position_id = 999001"
    ).fetchone()
    assert row["status"] == "open"
    assert row["mae"] is None and row["mfe"] is None


def test_excursion_scoped_per_trade_not_contaminated_across_timeframes(conn, client):
    # THE regression guard for the design bug this milestone's plan review
    # caught: excursion MUST be scoped by (symbol, THIS trade's own TF), never
    # a symbol-wide scan across every stored timeframe. Two trades share a
    # symbol and open instant but pick DIFFERENT TFs -- a 30s trade -> M1, an
    # ~8.3h trade -> M15 -- exactly the shape a hedging account produces
    # (CLAUDE.md line 26: several positions on the same symbol can be open at
    # once). An M15 bar's wide range must never leak into the 30s trade's own,
    # narrowly-scoped M1 excursion.
    sync(client, conn)
    T0 = 1_800_000_000_000

    # Trade A: 30s, buy, entry 4000 -> choose_timeframe(30) == 'M1'.
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900001,0,801,'XAUUSDc',0,0,0,0,0.1,4000.0,0,0,0,0,?,'{}',?)",
        (_LOGIN, T0, T0),
    )
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900002,0,801,'XAUUSDc',1,1,0,0,0.1,4001.0,0,0,1.0,0,?,'{}',?)",
        (_LOGIN, T0 + 30_000, T0),
    )
    # Trade B: ~8.3h, buy, entry 4000 -> choose_timeframe(30000) == 'M15'.
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900003,0,802,'XAUUSDc',0,0,0,0,0.1,4000.0,0,0,0,0,?,'{}',?)",
        (_LOGIN, T0, T0),
    )
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900004,0,802,'XAUUSDc',1,1,0,0,0.1,4005.0,0,0,5.0,0,?,'{}',?)",
        (_LOGIN, T0 + 30_000_000, T0),
    )
    conn.commit()

    # Trade A's OWN M1 candle at T0: tight range.
    conn.execute(
        "INSERT INTO candles (symbol,timeframe,time_msc,open,high,low,close,"
        "tick_volume) VALUES ('XAUUSDc','M1',?,4000,4002,3998,4001,10)", (T0,),
    )
    # Trade B's OWN M15 candle, at the SAME instant T0: an extreme, wide bar --
    # this is the row that must NEVER leak into Trade A's excursion.
    conn.execute(
        "INSERT INTO candles (symbol,timeframe,time_msc,open,high,low,close,"
        "tick_volume) VALUES ('XAUUSDc','M15',?,4000,5000,3000,4005,10)", (T0,),
    )
    conn.commit()

    rebuild(conn)
    a = conn.execute("SELECT mae, mfe FROM trades WHERE position_id = 801").fetchone()
    b = conn.execute("SELECT mae, mfe FROM trades WHERE position_id = 802").fetchone()

    # Trade A must use ONLY its own M1 bar -- small, correct values, NOT the
    # M15 bar's [3000, 5000] range.
    assert abs(a["mae"] - 2.0) < 1e-9   # 4000 - 3998
    assert abs(a["mfe"] - 2.0) < 1e-9   # 4002 - 4000
    # Trade B must use ONLY its own M15 bar -- the wide, extreme values.
    assert abs(b["mae"] - 1000.0) < 1e-9   # 4000 - 3000
    assert abs(b["mfe"] - 1000.0) < 1e-9   # 5000 - 4000


def test_mae_r_mfe_r_zero_division_guard_sl_exactly_at_entry(conn, client):
    # The SAME ZeroDivisionError shape r_multiple already guards (Trap 6 /
    # M2.1), now in _fill_excursions: SL exactly at entry gives a KNOWN zero
    # risk_distance, not an unknown one. mae/mfe must still populate (candle
    # coverage exists); mae_r/mfe_r must stay None, never crash the rebuild.
    sync(client, conn)
    T0 = 1_850_000_000_000
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900005,700,850,'XAUUSDc',0,0,0,0,0.1,4000.0,0,0,0,0,?,'{}',?)",
        (_LOGIN, T0, T0),
    )
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,900006,0,850,'XAUUSDc',1,1,0,0,0.1,4010.0,0,0,10.0,0,?,'{}',?)",
        (_LOGIN, T0 + 373_000, T0),
    )
    conn.execute(
        "INSERT INTO orders_raw (account_login,ticket,position_id,symbol,type,sl,"
        "tp,price_open,raw_json,ingested_at) VALUES "
        "(?,700,850,'XAUUSDc',0,4000.0,0,4000.0,'{}',?)",
        (_LOGIN, T0),
    )
    conn.commit()
    for i in range(-20, 20):
        price = 4000 + i * 0.3
        conn.execute(
            "INSERT INTO candles (symbol,timeframe,time_msc,open,high,low,close,"
            "tick_volume) VALUES ('XAUUSDc','M1',?,?,?,?,?,10)",
            (T0 + i * 60_000, price, price + 1.0, price - 1.0, price + 0.1),
        )
    conn.commit()

    rebuild(conn)  # must not raise ZeroDivisionError
    row = conn.execute(
        "SELECT mae, mfe, mae_r, mfe_r FROM trades WHERE position_id = 850"
    ).fetchone()
    assert row["mae"] is not None and row["mfe"] is not None
    assert row["mae_r"] is None and row["mfe_r"] is None


def test_rebuild_idempotent_with_mae_mfe_populated(conn, client):
    sync(client, conn)
    rebuild(conn)
    row = conn.execute(
        "SELECT position_id, symbol, open_time_msc, close_time_msc, duration_s "
        "FROM trades WHERE status = 'closed' LIMIT 1"
    ).fetchone()
    from journal.render.chart import choose_timeframe, window_for
    tf = choose_timeframe(row["duration_s"])
    from_msc, _ = window_for(row["open_time_msc"], row["close_time_msc"], tf)
    conn.execute(
        "INSERT INTO candles (symbol, timeframe, time_msc, open, high, low, close, "
        "tick_volume) VALUES (?, ?, ?, 4000, 4005, 3995, 4000, 10)",
        (row["symbol"], tf, from_msc),
    )
    conn.commit()

    rebuild(conn)
    cols = _trade_value_cols(conn)
    first = _snapshot(conn, cols)
    assert any(
        v[cols.index("mae")] is not None for v in first.values()
    ), "test setup should have produced at least one non-NULL mae"

    rebuild(conn)  # sl_tp_snapshots/candles are read-only here: must reproduce exactly
    second = _snapshot(conn, cols)
    assert first == second


def test_killer_balance_identity_2_holds_over_real_history(conn, client):
    # §6 identity 2 — the partition check. THE test that proves reconstruction lost
    # and double-counted nothing across the whole history.
    sync(client, conn)
    rebuild(conn)
    add_reconciliation(
        conn, _LOGIN, _GAP, effective_msc=1783745936454,
        reason="Broker archived deals; underlying deals unrecoverable.",
        evidence="correction deal 1399033630, comment 'Archived deals'",
    )
    v = verify(conn)
    assert v.trades_count == 68
    assert v.passed                       # BOTH identities
    assert abs(v.residual2) < _TOL        # identity 2 balances
    # net of the 68 trades matches MT5's own report figure (docs §6): 63.72 USC.
    assert abs(v.trades_net - 63.72) < _TOL


def test_verify_identity2_not_run_when_no_trade_deals(tmp_path, conn):
    # Amendment 1, branch A: an empty store (no trade deals, no trades) is "not run",
    # NOT a failure — there is nothing to reconstruct.
    # Seed just an account so verify has a balance to read, but no deals at all.
    conn.execute(
        "INSERT INTO accounts (login, currency, balance, first_seen_at) "
        "VALUES (?, 'USC', 0.0, 0)", (_LOGIN,),
    )
    conn.commit()
    v = verify(conn)
    assert v.id2_state == "not_run"
    assert v.trade_deals_count == 0 and v.trades_count == 0


def test_verify_identity2_fails_loud_when_trades_empty_but_deals_present(conn, client):
    # Amendment 1, branch B: trade deals exist but trades is empty -> the catastrophic
    # reconstruct()->[] case. verify must FAIL loudly, naming the unreconstructed count.
    sync(client, conn)          # 136 trade deals land in deals_raw...
    # ...but NO rebuild. trades is empty.
    v = verify(conn)
    assert v.id2_state == "fail"
    assert not v.passed
    assert v.trade_deals_count == 136 and v.trades_count == 0


# --- M6: auto-tag pass wired into rebuild() (manual-safe, §9-gated) ----------


def _seed_account_only(conn, login=_LOGIN):
    conn.execute(
        "INSERT INTO accounts (login, currency, balance, first_seen_at) "
        "VALUES (?, 'USC', 0.0, 0)", (login,),
    )
    conn.commit()


def _raw_in(conn, pid, ticket, time_msc, *, symbol="BTCUSDc", price=100.0,
            volume=0.1, login=_LOGIN):
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,?,0,?,?,0,0,0,0,?,?,0,0,0,0,?,'{}',?)",
        (login, ticket, pid, symbol, volume, price, time_msc, time_msc),
    )


def _raw_out(conn, pid, ticket, time_msc, *, symbol="BTCUSDc", price=101.0,
             volume=0.1, profit=1.0, login=_LOGIN):
    conn.execute(
        "INSERT INTO deals_raw (account_login,ticket,order_ticket,position_id,"
        "symbol,type,entry,reason,magic,volume,price,commission,swap,profit,fee,"
        "time_msc,raw_json,ingested_at) VALUES "
        "(?,?,0,?,?,1,1,0,0,?,?,0,0,?,0,?,'{}',?)",
        (login, ticket, pid, symbol, volume, price, profit, time_msc, time_msc),
    )


def _tags(conn, pid, login=_LOGIN):
    return {
        (r["tag"], r["source"])
        for r in conn.execute(
            "SELECT tag, source FROM tags WHERE account_login = ? AND position_id = ?",
            (login, pid),
        )
    }


def _dt_ms(y, mo, d, h=0, mi=0) -> int:
    from datetime import datetime, timezone
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def test_rebuild_writes_auto_tags_for_structural_facts(conn):
    _seed_account_only(conn)
    # pid 1: opened Saturday 2026-01-17 10:00 UTC, held 30s -> weekend + sub-1min.
    sat = _dt_ms(2026, 1, 17, 10, 0)
    _raw_in(conn, 1, 101, sat)
    _raw_out(conn, 1, 102, sat + 30_000)
    # pid 2: opened Wed 23:30, closed Thu 00:10 next day -> held-overnight only.
    o = _dt_ms(2026, 1, 14, 23, 30)
    _raw_in(conn, 2, 201, o)
    _raw_out(conn, 2, 202, _dt_ms(2026, 1, 15, 0, 10))
    conn.commit()

    rebuild(conn)
    assert _tags(conn, 1) == {("weekend", "auto"), ("sub-1min", "auto")}
    assert _tags(conn, 2) == {("held-overnight", "auto")}


def test_manual_tag_survives_rebuild(conn, client):
    # The headline safety test: a source='manual' tag must NOT be deleted by the
    # auto pass, and auto tags must be regenerated around it.
    sync(client, conn)
    rebuild(conn)
    pid = conn.execute("SELECT position_id FROM trades LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO tags (account_login, position_id, segment, tag, source) "
        "VALUES (?, ?, 0, 'my-note', 'manual')", (_LOGIN, pid),
    )
    conn.commit()

    rebuild(conn)
    assert ("my-note", "manual") in _tags(conn, pid)
    # auto tags were regenerated for the account (fixture has 68 closed trades).
    n_auto = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE source = 'auto'"
    ).fetchone()[0]
    assert n_auto > 0


def test_auto_tags_are_idempotent_across_rebuilds(conn, client):
    sync(client, conn)
    rebuild(conn)
    first = sorted(
        tuple(r) for r in conn.execute(
            "SELECT account_login, position_id, segment, tag, source FROM tags"
        )
    )
    rebuild(conn)
    second = sorted(
        tuple(r) for r in conn.execute(
            "SELECT account_login, position_id, segment, tag, source FROM tags"
        )
    )
    assert first == second


def test_no_outlier_tags_below_min_n(conn):
    # §9 gate: on a sub-20-trade account, big-win/big-loss must never appear even
    # though one trade is a huge outlier.
    _seed_account_only(conn)
    for i in range(1, 6):  # 5 closed trades -> below _MIN_N
        t = _dt_ms(2026, 1, 14, 10 + i)  # Wednesday, intraday
        _raw_in(conn, i, 100 + i * 2, t)
        _raw_out(conn, i, 101 + i * 2, t + 3600_000, profit=1_000_000.0 if i == 1 else 1.0)
    conn.commit()

    rebuild(conn)
    outliers = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE tag IN ('big-win', 'big-loss')"
    ).fetchone()[0]
    assert outliers == 0
