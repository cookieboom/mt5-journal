"""The fixture-backed adapter. No bridge, no MT5 — reads tests/fixtures/*.json.

This is what makes CLAUDE.md Hard rule 1 real: every test and every non-adapter
module can run against `FakeMT5Client` with nothing installed and nothing
listening on :8001.

Fixture layout (all valid empty placeholders for M0; populated in later
milestones):
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
        return [_build(Candle, r) for r in rows]

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]:
        return [_build(Deal, d) for d in self._load("deals", [])]

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        return [_build(Order, o) for o in self._load("orders", [])]

    def positions_get(self) -> list[Position]:
        return [_build(Position, p) for p in self._load("positions", [])]
