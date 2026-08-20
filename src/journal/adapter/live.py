"""The live MT5 adapter. THE ONLY FILE ALLOWED TO IMPORT THE BRIDGE.

CLAUDE.md Hard rule 1 (import) and Hard rule 12 (values): the MT5 vocabulary —
the `siliconmetatrader5` module, its `TIMEFRAME_*` constants, its `DEAL_*`
integers — is confined to this file. Everything leaving here is one of our own
dataclasses / strings from `base.py`.
"""

from __future__ import annotations

import logging
from typing import Any

from siliconmetatrader5 import MetaTrader5  # noqa: the one permitted import

from .base import (
    Account,
    Candle,
    Deal,
    DealEntry,
    DealReason,
    DealType,
    Order,
    OrderFilling,
    OrderType,
    Position,
    SymbolInfo,
    Tick,
    TradeAction,
    TradeRequest,
    TradeResult,
    TradeRetcode,
)
from ._mt5_common import _build, _from_bridge_result, _to_bridge_request

log = logging.getLogger(__name__)


class LiveMT5Client:
    """Implements `MT5Client` over the siliconmetatrader5 bridge."""

    def __init__(
        self, host: str = "localhost", port: int = 8001, keepalive: bool = True
    ) -> None:
        self._mt5 = MetaTrader5(host=host, port=port, keepalive=keepalive)
        if not self._mt5.initialize():
            raise RuntimeError(
                f"MT5 bridge initialize() failed on {host}:{port} — is the "
                f"Docker container up? last_error={self._safe_last_error()}"
            )
        self._assert_enums_match()
        # Map our timeframe strings to the bridge constants. Built here so no
        # MT5 TIMEFRAME_* value ever leaves this file (Rule 12).
        self._tf = {
            "M1": self._mt5.TIMEFRAME_M1,
            "M5": self._mt5.TIMEFRAME_M5,
            "M15": self._mt5.TIMEFRAME_M15,
            "H1": self._mt5.TIMEFRAME_H1,
            "H4": self._mt5.TIMEFRAME_H4,
            "D1": self._mt5.TIMEFRAME_D1,
        }

    def _safe_last_error(self) -> Any:
        try:
            return self._mt5.last_error()
        except Exception:  # pragma: no cover - diagnostic only
            return "unavailable"

    def _assert_enums_match(self) -> None:
        """Our IntEnums are authoritative for the codebase; where the bridge
        exposes the matching constant, verify it agrees (docs/mt5-deal-model.md §2
        says confirm, don't hardcode). A *mismatch* on an exposed constant is a
        hard failure. A *missing* constant is unverifiable — not a failure — so it
        is logged and skipped: the bridge does not export every DEAL_* value, and
        an unexposed one must not stop init (see doc §2, Rule 12)."""
        checks = {
            DealType: "DEAL_TYPE_{}",
            DealEntry: "DEAL_ENTRY_{}",
            DealReason: "DEAL_REASON_{}",
            # M9 write side. A wrong TRADE_ACTION would send a completely
            # different operation than intended, so this check matters more here
            # than anywhere above: a mismatched DEAL_TYPE misreads history, a
            # mismatched TRADE_ACTION mistrades money.
            TradeAction: "TRADE_ACTION_{}",
            OrderType: "ORDER_TYPE_{}",
            OrderFilling: "ORDER_FILLING_{}",
            TradeRetcode: "TRADE_RETCODE_{}",
        }
        for enum_cls, tmpl in checks.items():
            for member in enum_cls:
                attr = tmpl.format(member.name)
                if not hasattr(self._mt5, attr):
                    log.warning(
                        "bridge does not expose %s; cannot verify %s.%s=%d "
                        "(unverifiable, not a failure)",
                        attr, enum_cls.__name__, member.name, member.value,
                    )
                    continue
                bridge_val = getattr(self._mt5, attr)
                if bridge_val != member.value:
                    raise RuntimeError(
                        f"enum mismatch: {enum_cls.__name__}.{member.name}="
                        f"{member.value} but bridge {attr}={bridge_val}"
                    )

    # --------------------------------------------------------------- methods

    def account_info(self) -> Account | None:
        info = self._mt5.account_info()
        return _build(Account, info._asdict()) if info is not None else None

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        # Select into Market Watch first, else out-of-watch symbols return None
        # silently (trap 12).
        self._mt5.symbol_select(symbol, True)
        info = self._mt5.symbol_info(symbol)
        return _build(SymbolInfo, info._asdict()) if info is not None else None

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        self._mt5.symbol_select(symbol, True)  # trap 12
        tick = self._mt5.symbol_info_tick(symbol)
        return _build(Tick, tick._asdict()) if tick is not None else None

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]:
        syms = self._mt5.symbols_get(group) if group else self._mt5.symbols_get()
        return [_build(SymbolInfo, s._asdict()) for s in (syms or ())]

    def copy_rates_range(
        self, symbol: str, timeframe: str, date_from: Any, date_to: Any
    ) -> list[Candle]:
        if timeframe not in self._tf:
            raise ValueError(
                f"unknown timeframe {timeframe!r}; expected one of {list(self._tf)}"
            )
        # Select into Market Watch first, else out-of-watch symbols return None/[]
        # silently (trap 12) — same reasoning as symbol_info/symbol_info_tick above.
        # Idempotent; one call per windowed fetch, not per bar. Not a proven bug on
        # this bridge (the 2026-07-17 probe tested an already-selected symbol and
        # could not settle it either way) — cheap insurance against a fresh
        # container / reset Market Watch silently drawing an empty chart.
        self._mt5.symbol_select(symbol, True)
        rows = self._mt5.copy_rates_range(
            symbol, self._tf[timeframe], date_from, date_to
        )
        out: list[Candle] = []
        for r in rows if rows is not None else ():
            # MT5 returns a numpy structured array; each row exposes named fields.
            # `time` is epoch SECONDS (rates carry no time_msc) — convert to ms at
            # the boundary so the rest of the codebase sees only ms (Trap 15).
            out.append(
                Candle(
                    time_msc=int(r["time"]) * 1000,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    tick_volume=int(r["tick_volume"]),
                    spread=int(r["spread"]),
                    real_volume=int(r["real_volume"]),
                )
            )
        return out

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]:
        deals = self._mt5.history_deals_get(date_from, date_to)
        return [_build(Deal, d._asdict()) for d in (deals or ())]

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        orders = self._mt5.history_orders_get(date_from, date_to)
        return [_build(Order, o._asdict()) for o in (orders or ())]

    def positions_get(self) -> list[Position]:
        positions = self._mt5.positions_get()
        return [_build(Position, p._asdict()) for p in (positions or ())]

    # ----------------------------------------------------------- write side (M9)

    def order_check(self, request: TradeRequest) -> TradeResult:
        """Dry run — asks the broker whether it would accept this. Sends nothing."""
        return _from_bridge_result(self._mt5.order_check(_to_bridge_request(request)))

    def order_send(self, request: TradeRequest) -> TradeResult:
        """Send for real. THIS MOVES MONEY.

        Logged before and after at INFO, unconditionally: when something goes
        wrong with a live order, the process log is the only record that exists
        outside the DB, and a request nobody logged is a request nobody can
        reconstruct. Note `symbol_select` is NOT called here — a position being
        modified or closed already exists, so its symbol is in Market Watch;
        adding a call would only add a failure mode.
        """
        payload = _to_bridge_request(request)
        log.info("order_send -> %s", payload)
        result = _from_bridge_result(self._mt5.order_send(payload))
        log.info(
            "order_send <- retcode=%s deal=%s volume=%s comment=%s",
            result.retcode, result.deal, result.volume, result.comment,
        )
        return result
