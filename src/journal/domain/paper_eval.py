"""Pure paper-trading evaluator — the single source of truth for a virtual
account's fills, SL/TP resolution, margin, and stop-out. No DB, no bridge, no
MT5 (CLAUDE.md rules 1, 7, 12): plain dataclasses in, events out, fixture-
testable with nothing running.

Money is USC (account currency); R is unit-free. Every unknown propagates to
`None` and is NEVER coerced to 0 (rule 4) — a coerced margin here would liquidate
an account over a missing spec.

Fill model: a market order fills at the CURRENT quote — a buy at the ask, a sell
at the bid — so a fresh position starts down by the spread, as it really does. A
pending order also fills at the current quote and not at its requested level: tick
data is discrete, and handing out a better price than was observed is a fabricated
gift. SL/TP fill AT the level (slippage across a tick gap is not modelled, the same
choice `replay_eval` makes). When one tick reaches both levels, the STOP fills
first — pessimistic, because tick granularity cannot reveal the true order and an
honest simulator never flatters.
"""
from __future__ import annotations

from dataclasses import dataclass

from .replay_eval import net_profit_usc

_TOL = 1e-9


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    time_msc: int


@dataclass
class Specs:
    tick_size: float
    tick_value: float          # per lot per tick, in ACCOUNT currency (USC)
    contract_size: float
    currency_profit: str       # the QUOTE currency, not the unit of tick_value


@dataclass
class PaperPos:
    id: int
    symbol: str
    direction: str             # "buy" | "sell"
    order_kind: str            # "market" | "limit" | "stop"
    request_price: float | None
    volume: float
    sl: float                  # 0.0 = none set (rule 4)
    tp: float                  # 0.0 = none set (rule 4)
    status: str                # pending | open | closed | cancelled | expired
    entry_price: float | None
    entry_msc: int | None
    expires_msc: int | None    # None = good till cancelled


@dataclass
class Event:
    position_id: int
    kind: str                  # "fill" | "exit" | "expire"
    price: float | None        # None for "expire"
    time_msc: int
    reason: str | None         # exit: "tp"|"sl"|"stopout"; otherwise None


@dataclass
class AccountState:
    equity: float | None
    margin: float | None
    free_margin: float | None
    margin_level: float | None
    floating: float | None


def entry_side(direction: str, quote: Quote) -> float:
    """The price you PAY to open: a buy lifts the ask, a sell hits the bid."""
    return quote.ask if direction == "buy" else quote.bid


def exit_side(direction: str, quote: Quote) -> float:
    """The price you GET to close: a buy exits into the bid, a sell into the ask."""
    return quote.bid if direction == "buy" else quote.ask


def usc_per_quote_unit(specs: Specs) -> float | None:
    """Account-currency units per one unit of quoted price, PER LOT, derived from
    the symbol's own specs rather than typed in. For XAUUSDc, 0.1 USC per 0.001
    USD is 100 USC per USD — the same 100 a literal would have hardcoded, except
    this one self-corrects per symbol and refuses when the specs are malformed.
    """
    if specs.tick_size is None or specs.tick_value is None:
        return None
    if specs.tick_size <= _TOL or specs.tick_value <= _TOL:
        return None
    return specs.tick_value / specs.tick_size


def margin_usc(volume: float | None, price: float | None, specs: Specs,
               leverage: int | None) -> float | None:
    """Margin required, in USC. `volume * price * tick_value / tick_size / leverage`.

    Valid only while the QUOTE currency is USD and the account currency is USC —
    the caller checks the account, this checks the symbol. Anything else is
    `None`: unknown, never a coerced 0 (rule 4, Trap 14).
    """
    if volume is None or price is None or leverage is None:
        return None
    if specs.currency_profit != "USD":
        return None
    if leverage <= 0 or volume <= _TOL or price <= _TOL:
        return None
    per_unit = usc_per_quote_unit(specs)
    if per_unit is None:
        return None
    return volume * price * per_unit / leverage


def floating_usc(pos: PaperPos, quote: Quote, specs: Specs) -> float | None:
    """Unrealised P&L in USC, marked at the side the position would exit on.
    `None` while the position has no entry price — an unfilled order has no P&L,
    and 0 would read as breakeven."""
    if pos.entry_price is None or pos.status != "open":
        return None
    return net_profit_usc(pos.direction, pos.entry_price, exit_side(pos.direction, quote),
                          pos.volume, specs.tick_size, specs.tick_value)


def account_state(positions: list[PaperPos], quotes: dict[str, Quote],
                  specs_by_symbol: dict[str, Specs], balance: float,
                  leverage: int) -> AccountState:
    """Equity, margin and margin level across EVERY open position, on every
    symbol — an account is cross-symbol and its margin is too.

    One missing quote or spec makes the whole account state unknown rather than
    partial. A margin level computed from some of the positions is not a smaller
    truth, it is a wrong number, and this one decides liquidation.
    """
    floating = 0.0
    margin = 0.0
    for p in positions:
        if p.status != "open":
            continue
        quote = quotes.get(p.symbol)
        specs = specs_by_symbol.get(p.symbol)
        if quote is None or specs is None:
            return AccountState(None, None, None, None, None)
        f = floating_usc(p, quote, specs)
        m = margin_usc(p.volume, p.entry_price, specs, leverage)
        if f is None or m is None:
            return AccountState(None, None, None, None, None)
        floating += f
        margin += m

    equity = balance + floating
    level = None if margin <= _TOL else equity / margin * 100.0
    return AccountState(equity=equity, margin=margin,
                        free_margin=equity - margin, margin_level=level,
                        floating=floating)
