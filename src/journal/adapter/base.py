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
    # COMPLETE set as PROBED from the live bridge on 2026-07-16 (15 constants;
    # see scripts/probe_enums.py — the bridge, not the MQL5 docs, is authoritative:
    # the docs once listed COMMISSION=6, the bridge reports BONUS=6, COMMISSION=7).
    # Must contain EVERY member the bridge exposes or DealType(deal.type) raises
    # ValueError the first time an unlisted type (e.g. INTEREST, a *_CANCELED)
    # appears in history. Only BUY/SELL feed reconstruction (Trap 1 is a positive
    # whitelist); the rest are non-trade deals kept for the equity curve.
    BUY = 0
    SELL = 1
    BALANCE = 2
    CREDIT = 3
    CHARGE = 4
    CORRECTION = 5
    BONUS = 6
    COMMISSION = 7
    COMMISSION_DAILY = 8
    COMMISSION_MONTHLY = 9
    COMMISSION_AGENT_DAILY = 10
    COMMISSION_AGENT_MONTHLY = 11
    INTEREST = 12
    BUY_CANCELED = 13
    SELL_CANCELED = 14


class DealEntry(IntEnum):
    # Complete and verified against the bridge (2026-07-16): exactly these four.
    IN = 0
    OUT = 1
    INOUT = 2
    OUT_BY = 3


class DealReason(IntEnum):
    # COMPLETE set as PROBED from the live bridge on 2026-07-16 (10 constants).
    # `reason` on the last OUT deal is the discipline metric (SL/TP/manual).
    CLIENT = 0
    MOBILE = 1
    WEB = 2
    EXPERT = 3
    SL = 4
    TP = 5
    SO = 6
    ROLLOVER = 7
    VMARGIN = 8
    SPLIT = 9


# ------------------------------------------------------------ trade enums (M9)
# The write side of the boundary. Same discipline as the deal enums above: these
# integers live ONLY here, `live.py` asserts them against the bridge at init, and
# `domain/` must never contain a magic 6. Values probed from
# siliconmetatrader5 v1.2.3 __init__.py (line numbers noted per enum).


class TradeAction(IntEnum):
    # __init__.py:142-147. Only the two M9 needs are listed: this journal closes
    # and modifies EXISTING positions and adds to them. It does not place pending
    # orders (PENDING/MODIFY/REMOVE) — there is no feature that wants them, and an
    # unused action here is a foot-gun with no upside.
    DEAL = 1   # market order: used for close, partial close, and add-volume
    SLTP = 6   # modify SL/TP of an open position


class OrderType(IntEnum):
    # __init__.py:68-69. Market types only, for the same reason as above.
    BUY = 0
    SELL = 1


class OrderFilling(IntEnum):
    # __init__.py:89-92. These are the values that go INTO a request's
    # `type_filling`. They are NOT the values in a symbol's `filling_mode`
    # bitmask — see `filling_for` below, which exists solely because those two
    # vocabularies use the same words for different numbers.
    FOK = 0
    IOC = 1
    RETURN = 2
    BOC = 3


class TradeRetcode(IntEnum):
    # COMPLETE set as exposed by the bridge (__init__.py:222-258). Completeness is
    # not tidiness: `TradeRetcode(result.retcode)` on an unlisted code raises
    # ValueError, and it would raise at the one moment we least want an exception —
    # immediately after an order HAS ALREADY REACHED THE BROKER, while trying to
    # record what happened to it. Same reasoning as DealType's comment.
    # (10037 is absent from the bridge and from MQL5; the gap is real, not a typo.)
    REQUOTE = 10004
    REJECT = 10006
    CANCEL = 10007
    PLACED = 10008
    DONE = 10009
    DONE_PARTIAL = 10010
    ERROR = 10011
    TIMEOUT = 10012
    INVALID = 10013
    INVALID_VOLUME = 10014
    INVALID_PRICE = 10015
    INVALID_STOPS = 10016
    TRADE_DISABLED = 10017
    MARKET_CLOSED = 10018
    NO_MONEY = 10019
    PRICE_CHANGED = 10020
    PRICE_OFF = 10021
    INVALID_EXPIRATION = 10022
    ORDER_CHANGED = 10023
    TOO_MANY_REQUESTS = 10024
    NO_CHANGES = 10025
    SERVER_DISABLES_AT = 10026
    CLIENT_DISABLES_AT = 10027
    LOCKED = 10028
    FROZEN = 10029
    INVALID_FILL = 10030
    CONNECTION = 10031
    ONLY_REAL = 10032
    LIMIT_ORDERS = 10033
    LIMIT_VOLUME = 10034
    INVALID_ORDER = 10035
    POSITION_CLOSED = 10036
    INVALID_CLOSE_VOLUME = 10038
    CLOSE_ORDER_EXIST = 10039
    LIMIT_POSITIONS = 10040
    REJECT_CANCEL = 10041
    LONG_ONLY = 10042
    SHORT_ONLY = 10043
    CLOSE_ONLY = 10044
    FIFO_CLOSE = 10045


# The three retcodes that mean the broker DID something. Everything else means it
# did not. DONE_PARTIAL belongs here: a partial fill changed the account, and
# treating it as failure would leave the journal believing a position it actually
# moved is untouched. The caller records the ACTUAL filled volume separately —
# never assume it equals what was requested.
_SUCCESS_RETCODES = frozenset(
    {TradeRetcode.DONE, TradeRetcode.PLACED, TradeRetcode.DONE_PARTIAL}
)


def is_success(retcode: int | TradeRetcode | None) -> bool:
    """Did the broker act on this request?

    Takes a plain int too, because callers read `result.retcode` straight off the
    wire. An unrecognised code is False, never an exception: this is called
    immediately after an order was sent, where raising would destroy the record
    of what just happened. Unknown is not a green light (rule 4's spirit).
    """
    if retcode is None:
        return False
    return retcode in _SUCCESS_RETCODES


# A symbol's `filling_mode` is a BITMASK using the SYMBOL_FILLING_* values, which
# are NOT the ORDER_FILLING_* values above. MQL5 defines SYMBOL_FILLING_FOK = 1
# and SYMBOL_FILLING_IOC = 2, while ORDER_FILLING_FOK = 0 and IOC = 1. The bridge
# does not expose the SYMBOL_FILLING_* constants (verified: no such attribute), so
# unlike every other constant in this file these two cannot be asserted against it
# — they are documented values, and this comment is the warning that goes with
# them. All three symbols on this account report filling_mode = 3, i.e. FOK|IOC.
_SYMBOL_FILLING_FOK = 1
_SYMBOL_FILLING_IOC = 2


def filling_for(filling_mode: int | None) -> "OrderFilling | None":
    """Pick a request `type_filling` from a symbol's `filling_mode` bitmask.

    Returns None when the mask is unknown (NULL — an un-refetched spec) or empty,
    so the caller decides rather than this function silently guessing FOK. Passing
    a bitmask straight through as a filling type is how you earn
    TRADE_RETCODE_INVALID_FILL, which is exactly the mistake this exists to make
    impossible.
    """
    if not filling_mode:
        return None
    if filling_mode & _SYMBOL_FILLING_FOK:
        return OrderFilling.FOK
    if filling_mode & _SYMBOL_FILLING_IOC:
        return OrderFilling.IOC
    return OrderFilling.RETURN


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
    equity: float | None = None  # snapshot; balance + floating P&L of open positions
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
    # M9: the order-validation group. Without these, checking a lot size or an
    # SL distance from stored data is impossible. All default to None because an
    # absent field is UNKNOWN, not zero (rule 4) — `domain/commands.py` refuses a
    # command whose spec is unknown rather than assuming a permissive default.
    volume_min: float | None = None
    volume_max: float | None = None
    volume_step: float | None = None
    trade_stops_level: int | None = None   # min SL/TP distance from price, in POINTS
    trade_freeze_level: int | None = None  # distance within which modification is frozen
    trade_mode: int | None = None          # 0=disabled 1=long-only 2=short-only 3=close-only 4=full
    filling_mode: int | None = None        # SYMBOL_FILLING_* BITMASK — see `filling_for`
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
    # Mirrors the `candles` table. `time_msc` is the bar OPEN time in epoch
    # MILLISECONDS, server time. MT5's `copy_rates_*` returns `time` in SECONDS
    # and has no `time_msc`; the ×1000 conversion happens at the adapter boundary
    # (live.py / fake.py) so everything above obeys Hard rule 3 — see
    # docs/mt5-deal-model.md Trap 15. No other time-shaped field exists here.
    time_msc: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    tick_volume: int | None = None
    spread: int | None = None
    real_volume: int | None = None


@dataclass(frozen=True)
class TradeRequest:
    """What we want the broker to do, in OUR vocabulary (M9).

    Frozen: an intent must not be mutated between the moment it is validated and
    the moment it is sent. `live.py` is the only place this becomes the bridge's
    dict of MT5 integers.

    `sl` / `tp` follow rule 4 and the distinction is load-bearing:
        None = leave this level untouched
        0.0  = clear this level
    A default of 0.0 would silently wipe a live stop-loss on every modify that
    only meant to set a take-profit.

    On this HEDGING account (margin_mode=2) a close or a partial close MUST
    carry `position_id` — without it the broker opens a second, opposite
    position instead of closing the one you meant.
    """

    action: TradeAction | None = None
    position_id: int | None = None
    symbol: str | None = None
    order_type: OrderType | None = None
    volume: float | None = None
    price: float | None = None
    sl: float | None = None
    tp: float | None = None
    deviation: int | None = None   # max slippage in points
    filling: OrderFilling | None = None
    magic: int | None = None
    comment: str | None = None


@dataclass(frozen=True)
class TradeResult:
    """What the broker said (M9). Every field defaults to None: before the broker
    answers, all of it is unknown, and unknown is not 0 (rule 4).

    `volume`/`price` are what was ACTUALLY filled, which on a DONE_PARTIAL is not
    what was requested. Never copy the request's volume in here.
    """

    retcode: int | None = None
    deal: int | None = None
    order: int | None = None
    volume: float | None = None
    price: float | None = None
    comment: str | None = None      # the BROKER's comment, not ours
    request_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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

    # --------------------------------------------------------- write side (M9)
    # The only two methods on this Protocol that can change the account. Until
    # M9 the entire interface was read-only.

    def order_check(self, request: TradeRequest) -> TradeResult:
        """Dry run: ask the broker whether it WOULD accept this. Sends nothing."""
        ...

    def order_send(self, request: TradeRequest) -> TradeResult:
        """Send it for real. This moves money. Callers must record the result
        before doing anything else, and must NEVER auto-retry a request that may
        have reached the broker."""
        ...
