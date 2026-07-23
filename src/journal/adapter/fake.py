"""The fixture-backed adapter. No bridge, no MT5 — reads tests/fixtures/*.json.

This is what makes CLAUDE.md Hard rule 1 real: every test and every non-adapter
module can run against `FakeMT5Client` with nothing installed and nothing
listening on :8001.

Fixture layout (real recorded data, sanitised — see scripts/record_fixtures.py;
a missing or empty fixture degrades to None/[], which the tests also exercise):
    account.json    -> object            -> Account
    symbols.json    -> [object]          -> list[SymbolInfo]
    ticks.json      -> {symbol: object}  -> Tick, keyed by symbol
    deals.json      -> [object]          -> list[Deal]
    orders.json     -> [object]          -> list[Order]
    positions.json  -> [object]          -> list[Position]
    rates.json      -> {"SYMBOL:TF": [object]} -> Candle, keyed "XAUUSDc:M15"
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from .base import (
    Account,
    Candle,
    Deal,
    Order,
    Position,
    SymbolInfo,
    Tick,
    TradeRequest,
    TradeResult,
    TradeRetcode,
)

_DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _build(cls, raw: dict[str, Any]):
    """Same contract as live.py's _build: keep declared fields, stash `raw`."""
    known = {f.name for f in fields(cls)} - {"raw"}
    kwargs = {k: raw[k] for k in known if k in raw}
    if "raw" in {f.name for f in fields(cls)}:
        return cls(**kwargs, raw=dict(raw))
    return cls(**kwargs)  # Candle has no raw field


class FakeMT5Client:
    """Implements `MT5Client` over JSON fixtures. Missing/empty fixture -> None/[]."""

    def __init__(self, fixtures_dir: str | Path = _DEFAULT_FIXTURES) -> None:
        self._dir = Path(fixtures_dir)
        self._cache: dict[str, Any] = {}
        # M9 write side. `sent` and `checked` are the record of what a test's
        # code under test asked the broker to do; `_results` is what to answer.
        self.sent: list[TradeRequest] = []
        self.checked: list[TradeRequest] = []
        self._results: list[TradeResult | Exception] = []

    def _load(self, name: str, default: Any) -> Any:
        if name not in self._cache:
            path = self._dir / f"{name}.json"
            try:
                text = path.read_text()
                self._cache[name] = json.loads(text) if text.strip() else default
            except FileNotFoundError:
                self._cache[name] = default
        data = self._cache[name]
        return data if data is not None else default

    # --------------------------------------------------------------- methods

    def account_info(self) -> Account | None:
        obj = self._load("account", {})
        return _build(Account, obj) if obj else None

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        for s in self._load("symbols", []):
            if s.get("name") == symbol:
                return _build(SymbolInfo, s)
        return None

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        obj = self._load("ticks", {}).get(symbol)
        return _build(Tick, obj) if obj else None

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]:
        out = [_build(SymbolInfo, s) for s in self._load("symbols", [])]
        if group:
            out = [s for s in out if s.name and group in s.name]
        return out

    def copy_rates_range(
        self, symbol: str, timeframe: str, date_from: Any, date_to: Any
    ) -> list[Candle]:
        rows = self._load("rates", {}).get(f"{symbol}:{timeframe}", [])
        # Fixtures store raw MT5 `time` in SECONDS (mirroring what the bridge
        # returns); convert ×1000 at the boundary just like live.py (Trap 15).
        return [
            Candle(
                time_msc=int(r["time"]) * 1000,
                open=r.get("open"),
                high=r.get("high"),
                low=r.get("low"),
                close=r.get("close"),
                tick_volume=r.get("tick_volume"),
                spread=r.get("spread"),
                real_volume=r.get("real_volume"),
            )
            for r in rows
        ]

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]:
        return [_build(Deal, d) for d in self._load("deals", [])]

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        return [_build(Order, o) for o in self._load("orders", [])]

    def positions_get(self) -> list[Position]:
        return [_build(Position, p) for p in self._load("positions", [])]

    # ----------------------------------------------------------- write side (M9)
    # There is no fixture behind these: an order is an EVENT, not recorded state.
    # Instead the fake records what it was asked to do and replays whatever the
    # test scripted — which is what lets every later phase assert on WHAT WOULD
    # HAVE BEEN SENT with no bridge, no terminal, and nothing at risk.

    def script_results(self, *results: TradeResult | Exception) -> None:
        """Queue the outcomes `order_send`/`order_check` will return, in order.

        An `Exception` in the queue is RAISED instead of returned — the only way
        to test that `journal live` survives the container disappearing
        mid-command. Once the queue is empty the default DONE resumes, so a test
        scripts only the calls it cares about.
        """
        self._results.extend(results)

    def _next_result(self) -> TradeResult:
        if not self._results:
            # Default happy path: the broker accepted it. Deliberately carries no
            # deal/volume — a test that cares about the fill must script it, and
            # inventing plausible numbers here would let a caller "pass" while
            # reading fields the real broker might not have set.
            return TradeResult(retcode=TradeRetcode.DONE)
        nxt = self._results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def order_check(self, request: TradeRequest) -> TradeResult:
        self.checked.append(request)
        return self._next_result()

    def order_send(self, request: TradeRequest) -> TradeResult:
        # Recorded on a SEPARATE list from `checked` so a test asserting
        # "nothing was actually sent" cannot be fooled by a dry run.
        self.sent.append(request)
        return self._next_result()
