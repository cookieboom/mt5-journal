"""The MT5 boundary.

Everything the rest of the codebase is allowed to know about MetaTrader 5 lives
here: our own dataclasses (never raw MT5 namedtuples) and our own enums (never
raw MT5 integer constants). See CLAUDE.md Hard rules 1 and 12.

- Rule 1: `import siliconmetatrader5` may appear ONLY in `live.py`.
- Rule 12: MT5 *values* must not escape the adapter either. Timeframes cross
  this Protocol as strings ("M15", matching `candles.timeframe`); deal enums are
  the `IntEnum`s below. `domain/` must never contain a magic `3`.

Field names on the dataclasses mirror MT5's raw names (e.g. `trade_tick_value`)
so `live.py` needs no rename map. Each dataclass carries `raw`, the full
`._asdict()` dump, so `ingest/` (M1) can write `*_raw.raw_json` faithfully and
survive MT5 adding fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------- enums
# The ONLY place these integers live. `live.py` asserts at init that each member
# equals the bridge's constant (mt5.DEAL_ENTRY_IN, ...). Values from the MQL5
# docs; see docs/mt5-deal-model.md §2.


class DealType(IntEnum):
    # Values MEASURED against the live bridge (LiveMT5Client asserts them at
    # init). NB: docs/mt5-deal-model.md §2 lists COMMISSION=6 from the MQL5 docs,
    # but this bridge reports BONUS=6, COMMISSION=7 — the bridge is authoritative.
    # Only BUY/SELL feed reconstruction (Trap 1 is a positive whitelist); the
    # rest are non-trade deals kept for the equity curve.
    BUY = 0
    SELL = 1
    BALANCE = 2
    CREDIT = 3
    CHARGE = 4
    CORRECTION = 5
    BONUS = 6
    COMMISSION = 7


class DealEntry(IntEnum):
    IN = 0
    OUT = 1
    INOUT = 2
    OUT_BY = 3


class DealReason(IntEnum):
    CLIENT = 0
    MOBILE = 1
    WEB = 2
    EXPERT = 3
    SL = 4
    TP = 5
    SO = 6


# The timeframe strings that cross the Protocol. Identical to the values allowed
# in `candles.timeframe`. `live.py` maps these to mt5.TIMEFRAME_*.
TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")


# ---------------------------------------------------------------- dataclasses
# Every field defaults so a dataclass can be built from a partial fixture dict
# without positional gymnastics. `raw` holds the untouched `._asdict()`.


@dataclass(frozen=True)
class Account:
    login: int | None = None
    currency: str | None = None
    balance: float | None = None
    margin_mode: int | None = None  # 0=netting 1=exchange 2=hedging
    leverage: int | None = None
    server: str | None = None
    company: str | None = None
    trade_mode: int | None = None  # 0=demo 1=contest 2=real
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolInfo:
    name: str | None = None
    digits: int | None = None
    point: float | None = None
    trade_tick_size: float | None = None
    trade_tick_value: float | None = None  # in ACCOUNT currency, not currency_profit (trap 14)
    trade_contract_size: float | None = None
    currency_profit: str | None = None  # symbol quote currency; NOT the unit of tick_value
    bid: float | None = None
    ask: float | None = None
    visible: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tick:
    time: int | None = None  # server epoch seconds
    time_msc: int | None = None  # server epoch milliseconds
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Deal:
    # Exactly the fields MT5 returns on this build; see docs/mt5-deal-model.md §6.
    # No sl/tp — that is the single most important fact about a deal (trap 6).
    ticket: int | None = None
    order: int | None = None
    time: int | None = None
    time_msc: int | None = None
    type: int | None = None
    entry: int | None = None
    magic: int | None = None
    position_id: int | None = None
    reason: int | None = None
    volume: float | None = None
    price: float | None = None
    commission: float | None = None
    swap: float | None = None
    profit: float | None = None
    fee: float | None = None
    symbol: str | None = None
    comment: str | None = None
    external_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Order:
    # docs/mt5-deal-model.md §2. Carries sl/tp — the source of sl_initial (trap 6).
    ticket: int | None = None
    time_setup: int | None = None
    time_setup_msc: int | None = None
    time_done: int | None = None
    time_done_msc: int | None = None
    time_expiration: int | None = None
    type: int | None = None
    type_time: int | None = None
    type_filling: int | None = None
    state: int | None = None
    magic: int | None = None
    position_id: int | None = None
    position_by_id: int | None = None
    reason: int | None = None
    volume_initial: float | None = None
    volume_current: float | None = None
    price_open: float | None = None
    sl: float | None = None
    tp: float | None = None
    price_current: float | None = None
    price_stoplimit: float | None = None
    symbol: str | None = None
    comment: str | None = None
    external_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    ticket: int | None = None
    time_msc: int | None = None
    type: int | None = None
    magic: int | None = None
    identifier: int | None = None  # == position_id
    symbol: str | None = None
    volume: float | None = None
    price_open: float | None = None
    sl: float | None = None
    tp: float | None = None
    price_current: float | None = None
    swap: float | None = None
    profit: float | None = None
    comment: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Candle:
    # Mirrors the `candles` table. `time` is the bar OPEN time, server epoch s.
    time: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    tick_volume: int | None = None
    spread: int | None = None
    real_volume: int | None = None


# ------------------------------------------------------------------ protocol


@runtime_checkable
class MT5Client(Protocol):
    """The one interface the rest of the codebase depends on.

    `adapter/live.py` implements it over the bridge; `adapter/fake.py` implements
    it over JSON fixtures. Anything outside `adapter/` must accept an `MT5Client`
    and never reach for MT5 directly.
    """

    def account_info(self) -> Account | None: ...

    def symbol_info(self, symbol: str) -> SymbolInfo | None: ...

    def symbol_info_tick(self, symbol: str) -> Tick | None: ...

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]: ...

    def copy_rates_range(
        self, symbol: str, timeframe: str, date_from: Any, date_to: Any
    ) -> list[Candle]:
        # `timeframe` is a string from TIMEFRAMES ("M15"), never an MT5 int.
        ...

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]: ...

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]: ...

    def positions_get(self) -> list[Position]: ...
