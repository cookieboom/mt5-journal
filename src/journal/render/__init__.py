"""Render: turn stored candles + a reconstructed trade into a PNG (M3).

Pure DB, no MT5 client — takes a `sqlite3.Connection` and nothing else, so it
runs against any store `journal sync`/`rebuild`/`candles` already populated
(mirrors `domain.reconstruct.rebuild` and `ingest.deals.verify`). Charts in
`cache/` are reproducible from the DB (CLAUDE.md rule 6): delete the PNG, call
`render_trade` again, get the same picture back.
"""

from .chart import (
    ChartResult,
    NoCandlesError,
    TradeNotFoundError,
    choose_timeframe,
    render_trade,
    window_for,
)

__all__ = [
    "ChartResult",
    "NoCandlesError",
    "TradeNotFoundError",
    "choose_timeframe",
    "render_trade",
    "window_for",
]
