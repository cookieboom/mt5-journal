"""Ingest OHLC candles for reconstructed trades into the central `candles` store.

Mirrors `ingest/deals.py`: takes an `MT5Client` by parameter, never constructs
`LiveMT5Client` (CLAUDE.md rule 1), so `sync_candles` runs under `FakeMT5Client`
with no bridge. For each CLOSED trade, fetches the render window
(`render.chart.choose_timeframe` / `window_for`) at the trade's own chosen
timeframe and appends new bars with `INSERT OR IGNORE` -- append-only, deduped on
the `candles` primary key `(symbol, timeframe, time_msc)`, safe to re-run.
Overlapping windows from nearby trades on the same symbol just collide
harmlessly on that key; central storage means a bar fetched for one trade is
free for its neighbours (schema.sql: "Dedupes across trades on the same
symbol/day").

RANJAU 1 (docs/mt5-deal-model.md Trap 15): the seconds->ms x1000 conversion
ALREADY happened at the adapter boundary (`live.py` / `fake.py` both do
`int(r["time"]) * 1000`). `candle.time_msc` is written HERE with NO arithmetic --
the magnitude check below is a TRIPWIRE against a regression (a second x1000
landing in this file), never a conversion. A second x1000 turns
1752624000000 into 1752624000000000; the render window query then matches zero
rows and the chart comes back empty with no error.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..adapter.base import Candle, MT5Client
from ..render.chart import choose_timeframe, window_for
from ..store.db import one_account_login

# A bar timestamp below this is SECONDS that leaked through (Trap 15), never a
# value to convert. See `_insert_candle`.
_MSC_FLOOR = 10**12


@dataclass(frozen=True)
class CandlesReport:
    account_login: int | None = None
    trades_seen: int = 0            # closed trades a window was fetched for
    trades_skipped_open: int = 0    # open/partially_open -- no close_time yet
    bars_seen: int = 0              # bars returned by the client, across all fetches
    bars_new: int = 0               # bars actually inserted (post PK-dedupe)
    symbols: list[str] = field(default_factory=list)


def _ms_to_dt(msc: int) -> datetime:
    return datetime.fromtimestamp(msc / 1000, tz=timezone.utc)


def sync_candles(client: MT5Client, conn: sqlite3.Connection) -> CandlesReport:
    """Fetch and store candles for every closed trade's render window. Idempotent
    and additive: re-running only inserts bars not already present. One commit."""
    login = one_account_login(conn)

    (total_trades,) = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_login = ?", (login,)
    ).fetchone()
    rows = conn.execute(
        "SELECT symbol, open_time_msc, close_time_msc, duration_s FROM trades "
        "WHERE account_login = ? AND status = 'closed'",
        (login,),
    ).fetchall()

    bars_seen = 0
    bars_new = 0
    symbols_touched: set[str] = set()

    for r in rows:
        tf = choose_timeframe(r["duration_s"])
        from_msc, to_msc = window_for(r["open_time_msc"], r["close_time_msc"], tf)
        candles = client.copy_rates_range(
            r["symbol"], tf, _ms_to_dt(from_msc), _ms_to_dt(to_msc)
        )
        symbols_touched.add(r["symbol"])
        for c in candles:
            bars_seen += 1
            bars_new += _insert_candle(conn, r["symbol"], tf, c)

    conn.commit()

    return CandlesReport(
        account_login=login,
        trades_seen=len(rows),
        trades_skipped_open=total_trades - len(rows),
        bars_seen=bars_seen,
        bars_new=bars_new,
        symbols=sorted(symbols_touched),
    )


def _insert_candle(
    conn: sqlite3.Connection, symbol: str, timeframe: str, c: Candle
) -> int:
    """Write `c.time_msc` STRAIGHT THROUGH -- see Trap 15 in the module
    docstring. The magnitude check is a regression tripwire, not a unit
    conversion: it must never fire on correct input, because the adapter
    boundary already did the x1000. Returns 1 if the row was newly inserted, 0
    if it already existed (PK dedupe)."""
    if c.time_msc is None or c.time_msc < _MSC_FLOOR:
        raise ValueError(
            f"candle time_msc={c.time_msc!r} for {symbol} {timeframe} is below "
            f"{_MSC_FLOOR} -- looks like SECONDS leaked through (Trap 15). Fix "
            "the adapter boundary (live.py/fake.py), not this module -- it must "
            "never do its own x1000."
        )
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO candles
            (symbol, timeframe, time_msc, open, high, low, close, tick_volume,
             spread, real_volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol, timeframe, c.time_msc, c.open, c.high, c.low, c.close,
            c.tick_volume, c.spread, c.real_volume,
        ),
    )
    return cur.rowcount
