"""FakeMT5Client must satisfy the MT5Client Protocol and return the declared
types against the (empty) placeholder fixtures — with no bridge running."""

from datetime import datetime

from journal.adapter.base import (
    Account,
    Candle,
    Deal,
    MT5Client,
    Order,
    Position,
    SymbolInfo,
    Tick,
)
from journal.adapter.fake import FakeMT5Client


def test_fake_conforms_to_protocol():
    # @runtime_checkable Protocol: verifies the 8 methods are all present.
    assert isinstance(FakeMT5Client(), MT5Client)


def test_list_methods_return_lists_on_empty_fixtures(tmp_path):
    # Hermetic: point at an empty dir so this asserts the missing/empty-fixture
    # contract regardless of what real data lives in tests/fixtures/ (populated
    # by scripts/record_fixtures.py for M1+).
    c = FakeMT5Client(fixtures_dir=tmp_path)
    when = datetime(2000, 1, 1)
    assert c.symbols_get() == []
    assert c.history_deals_get(when, when) == []
    assert c.history_orders_get(when, when) == []
    assert c.positions_get() == []
    assert c.copy_rates_range("XAUUSDc", "M15", when, when) == []


def test_scalar_methods_return_none_on_empty_fixtures(tmp_path):
    c = FakeMT5Client(fixtures_dir=tmp_path)  # empty dir; see test above
    assert c.account_info() is None
    assert c.symbol_info("XAUUSDc") is None
    assert c.symbol_info_tick("XAUUSDc") is None


def test_timeframe_is_a_string_not_an_int():
    # Rule 12: the Protocol takes a string timeframe. A populated rates fixture is
    # keyed "SYMBOL:TIMEFRAME"; here we just confirm the string call path works.
    c = FakeMT5Client()
    assert c.copy_rates_range("XAUUSDc", "M15", None, None) == []


def test_builds_declared_types_from_fixtures(tmp_path):
    # Drop minimal fixtures and confirm each maps to our dataclass with `raw` kept.
    fx = tmp_path / "fixtures"
    fx.mkdir()
    (fx / "account.json").write_text('{"login": 0, "currency": "USC", "balance": 6047.22}')
    (fx / "symbols.json").write_text(
        '[{"name": "XAUUSDc", "trade_tick_value": 0.1, "currency_profit": "USD"}]'
    )
    (fx / "ticks.json").write_text('{"XAUUSDc": {"time": 1, "bid": 4035.0}}')
    (fx / "deals.json").write_text('[{"ticket": 1, "entry": 0, "position_id": 555}]')
    (fx / "orders.json").write_text('[{"ticket": 1, "sl": 4030.0}]')
    (fx / "positions.json").write_text('[{"ticket": 1, "identifier": 555}]')
    (fx / "rates.json").write_text('{"XAUUSDc:M15": [{"time": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]}')

    c = FakeMT5Client(fixtures_dir=fx)

    acct = c.account_info()
    assert isinstance(acct, Account)
    assert acct.currency == "USC"
    assert acct.raw["balance"] == 6047.22  # full dict preserved for forward-compat

    (sym,) = c.symbols_get()
    assert isinstance(sym, SymbolInfo)
    # trap 14: currency_profit is metadata; tick_value's unit is account currency.
    assert sym.currency_profit == "USD"

    assert isinstance(c.symbol_info("XAUUSDc"), SymbolInfo)
    assert isinstance(c.symbol_info_tick("XAUUSDc"), Tick)

    (deal,) = c.history_deals_get(None, None)
    assert isinstance(deal, Deal) and deal.position_id == 555

    (order,) = c.history_orders_get(None, None)
    assert isinstance(order, Order) and order.sl == 4030.0

    (pos,) = c.positions_get()
    assert isinstance(pos, Position) and pos.identifier == 555

    (candle,) = c.copy_rates_range("XAUUSDc", "M15", None, None)
    assert isinstance(candle, Candle) and candle.high == 2


def test_candle_time_is_milliseconds(tmp_path):
    # Trap 15: copy_rates_* returns `time` in SECONDS; the adapter must surface
    # it as epoch MILLISECONDS so it lands correctly in candles.time_msc. A bar
    # timestamp below 10**12 is seconds that leaked through the boundary.
    fx = tmp_path / "fixtures"
    fx.mkdir()
    (fx / "rates.json").write_text(
        '{"XAUUSDc:M15": [{"time": 1752624000, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]}'
    )
    c = FakeMT5Client(fixtures_dir=fx)

    (candle,) = c.copy_rates_range("XAUUSDc", "M15", None, None)
    assert candle.time_msc == 1752624000000
    assert candle.time_msc >= 10**12  # below this = seconds leaked (Trap 15)
