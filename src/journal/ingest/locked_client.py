"""Serializes every MT5Client call behind one lock.

`journal live` now runs two threads (position_loop, symbol_loop) sharing one
bridge connection. `NativeMT5Client` (Windows terminal, the `MetaTrader5`
package) has no documented thread-safety guarantee for concurrent calls;
`LiveMT5Client` (Docker/HTTP bridge) is probably safe concurrently but gets
the same treatment for one consistent story. This wraps either adapter
behind a lock so only one thread ever has a call in flight on the real
client at a time — sleep timing, DB writes, and everything else in each
loop stays unblocked; only the bridge round trip itself serializes.
"""

from __future__ import annotations

import threading
from typing import Any

from ..adapter.base import (
    Account,
    Candle,
    Deal,
    MT5Client,
    Order,
    Position,
    SymbolInfo,
    Tick,
    TradeRequest,
    TradeResult,
)


class LockedMT5Client:
    def __init__(self, inner: MT5Client, lock: threading.Lock | None = None) -> None:
        self._inner = inner
        self._lock = lock if lock is not None else threading.Lock()

    def account_info(self) -> Account | None:
        with self._lock:
            return self._inner.account_info()

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        with self._lock:
            return self._inner.symbol_info(symbol)

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        with self._lock:
            return self._inner.symbol_info_tick(symbol)

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]:
        with self._lock:
            return self._inner.symbols_get(group)

    def copy_rates_range(self, symbol: str, timeframe: str, date_from: Any,
                         date_to: Any) -> list[Candle]:
        with self._lock:
            return self._inner.copy_rates_range(symbol, timeframe, date_from, date_to)

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]:
        with self._lock:
            return self._inner.history_deals_get(date_from, date_to)

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        with self._lock:
            return self._inner.history_orders_get(date_from, date_to)

    def positions_get(self) -> list[Position]:
        with self._lock:
            return self._inner.positions_get()

    def order_check(self, request: TradeRequest) -> TradeResult:
        with self._lock:
            return self._inner.order_check(request)

    def order_send(self, request: TradeRequest) -> TradeResult:
        with self._lock:
            return self._inner.order_send(request)
