"""The native MT5 adapter for a Windows host. THE ONLY FILE (besides
`live.py`) ALLOWED TO IMPORT AN MT5 PACKAGE — CLAUDE.md Hard rule 1 names
the whole `adapter/` directory, not a single file within it.

Connects directly to a local, already-running, already-logged-in MT5
terminal via the official `MetaTrader5` PyPI package — no Docker, no rpyc,
no host/port. Picked automatically by `adapter/select.py` when
`sys.platform == "win32"` and this package is importable; falls back to
`adapter/live.py`'s Docker bridge everywhere else.
"""

from __future__ import annotations

import logging
from typing import Any

import MetaTrader5 as mt5  # noqa: the other permitted import (see module docstring)

from ._mt5_common import _build, _from_bridge_result, _to_bridge_request
from .base import (
    Account,
    Candle,
    Deal,
    DealEntry,
    DealReason,
    DealType,
    EnumMismatch,
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

log = logging.getLogger(__name__)


class NativeMT5Client:
    """Implements `MT5Client` over a local MT5 terminal (Windows only)."""

    def __init__(self) -> None:
        if not mt5.initialize():
            raise RuntimeError(
                f"MT5 initialize() failed — is a terminal installed, running, "
                f"and logged in? last_error={self._safe_last_error()}"
            )
        self._assert_enums_match()
        self._tf = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }

    def _safe_last_error(self) -> Any:
        try:
            return mt5.last_error()
        except Exception:  # pragma: no cover - diagnostic only
            return "unavailable"

    def _assert_enums_match(self) -> None:
        """Same discipline as `LiveMT5Client._assert_enums_match` (rule 12):
        our IntEnums are authoritative; verify, never assume — same vendor
        does not guarantee the installed release exposes identical values.
        A mismatch on an exposed constant is a hard failure; a missing
        constant is logged and skipped, not a failure."""
        checks = {
            DealType: "DEAL_TYPE_{}",
            DealEntry: "DEAL_ENTRY_{}",
            DealReason: "DEAL_REASON_{}",
            TradeAction: "TRADE_ACTION_{}",
            OrderType: "ORDER_TYPE_{}",
            OrderFilling: "ORDER_FILLING_{}",
            TradeRetcode: "TRADE_RETCODE_{}",
        }
        for enum_cls, tmpl in checks.items():
            for member in enum_cls:
                attr = tmpl.format(member.name)
                if not hasattr(mt5, attr):
                    log.warning(
                        "native package does not expose %s; cannot verify "
                        "%s.%s=%d (unverifiable, not a failure)",
                        attr, enum_cls.__name__, member.name, member.value,
                    )
                    continue
                native_val = getattr(mt5, attr)
                if native_val != member.value:
                    raise EnumMismatch(
                        f"enum mismatch: {enum_cls.__name__}.{member.name}="
                        f"{member.value} but native {attr}={native_val}"
                    )

    # --------------------------------------------------------------- methods

    def account_info(self) -> Account | None:
        info = mt5.account_info()
        return _build(Account, info._asdict()) if info is not None else None

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        mt5.symbol_select(symbol, True)  # trap 12, same as live.py
        info = mt5.symbol_info(symbol)
        return _build(SymbolInfo, info._asdict()) if info is not None else None

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        return _build(Tick, tick._asdict()) if tick is not None else None

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]:
        syms = mt5.symbols_get(group=group) if group else mt5.symbols_get()
        return [_build(SymbolInfo, s._asdict()) for s in (syms or ())]

    def copy_rates_range(
        self, symbol: str, timeframe: str, date_from: Any, date_to: Any
    ) -> list[Candle]:
        if timeframe not in self._tf:
            raise ValueError(
                f"unknown timeframe {timeframe!r}; expected one of {list(self._tf)}"
            )
        mt5.symbol_select(symbol, True)
        rows = mt5.copy_rates_range(symbol, self._tf[timeframe], date_from, date_to)
        out: list[Candle] = []
        for r in rows if rows is not None else ():
            # `time` is epoch SECONDS; convert to ms at the boundary (Trap 15,
            # same as live.py — the native package returns rates in the same
            # shape as the bridge).
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
        deals = mt5.history_deals_get(date_from, date_to)
        return [_build(Deal, d._asdict()) for d in (deals or ())]

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        orders = mt5.history_orders_get(date_from, date_to)
        return [_build(Order, o._asdict()) for o in (orders or ())]

    def positions_get(self) -> list[Position]:
        positions = mt5.positions_get()
        return [_build(Position, p._asdict()) for p in (positions or ())]

    # ----------------------------------------------------------- write side (M9)

    def order_check(self, request: TradeRequest) -> TradeResult:
        """Dry run — asks the broker whether it would accept this. Sends nothing."""
        return _from_bridge_result(mt5.order_check(_to_bridge_request(request)))

    def order_send(self, request: TradeRequest) -> TradeResult:
        """Send for real. THIS MOVES MONEY. Logged before/after at INFO —
        same reasoning as `LiveMT5Client.order_send`: this is the only record
        outside the DB of what was actually sent."""
        payload = _to_bridge_request(request)
        log.info("order_send -> %s", payload)
        result = _from_bridge_result(mt5.order_send(payload))
        log.info(
            "order_send <- retcode=%s deal=%s volume=%s comment=%s",
            result.retcode, result.deal, result.volume, result.comment,
        )
        return result
