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
from journal.domain.reconstruct import SymbolSpec, Trade, rebuild, reconstruct
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
