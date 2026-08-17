"""Pure-DB live-monitor store: heartbeat, watch registry, and the single forming
bar per (symbol, timeframe). NO bridge, NO MT5 — safe to import from web/. The
bridge-touching fetch lives in ingest/live.py, exactly like candles_store vs
candle_fill.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import Candle

_MSC_FLOOR = 10**12  # below this, time_msc is seconds leaking through (Trap 15)


def beat(conn: sqlite3.Connection, now_msc: int) -> None:
    """Overwrite the single heartbeat row. Caller need not commit — we do."""
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET beat_msc = excluded.beat_msc",
        (now_msc,),
    )
    conn.commit()


def mark_started(conn: sqlite3.Connection, now_msc: int,
                 code_fingerprint: str | None = None) -> None:
    """Record when — and which code — THIS process loaded. Once, at loop start.

    A heartbeat says the daemon is alive; it cannot say it is current. Every
    change to the live loop ships with "restart `journal live`", and nothing on
    the machine could see that the restart never happened.

    `code_fingerprint` is `health.code_fingerprint()`: the modules this process
    actually imported, hashed. The timestamp alone could only be compared
    against file mtimes, which every unrelated edit disturbs — see
    `health.changed_modules`. It stays optional so this module keeps its promise
    (pure DB, no hashing, no scanning).
    """
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc, started_msc, code_fingerprint) "
        "VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET beat_msc = excluded.beat_msc, "
        "started_msc = excluded.started_msc, "
        "code_fingerprint = excluded.code_fingerprint",
        (now_msc, now_msc, code_fingerprint),
    )
    conn.commit()


def read_heartbeat(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT beat_msc FROM live_heartbeat WHERE id = 1").fetchone()
    return None if row is None else int(row["beat_msc"])


def read_started(conn: sqlite3.Connection) -> int | None:
    """When the running daemon started, or None if it never said."""
    row = conn.execute("SELECT started_msc FROM live_heartbeat WHERE id = 1").fetchone()
    return None if row is None or row["started_msc"] is None else int(row["started_msc"])


def read_code_fingerprint(conn: sqlite3.Connection) -> str | None:
    """What the running daemon said it had loaded, or None if it never said."""
    row = conn.execute(
        "SELECT code_fingerprint FROM live_heartbeat WHERE id = 1"
    ).fetchone()
    return None if row is None else row["code_fingerprint"]


def upsert_watch(conn: sqlite3.Connection, symbol: str, timeframe: str,
                 now_msc: int, ttl_ms: int) -> None:
    conn.execute(
        "INSERT INTO live_watches (symbol, timeframe, expires_msc, requested_msc) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(symbol, timeframe) DO UPDATE SET "
        "expires_msc = excluded.expires_msc, requested_msc = excluded.requested_msc",
        (symbol, timeframe, now_msc + ttl_ms, now_msc),
    )
    conn.commit()


def active_watches(conn: sqlite3.Connection, now_msc: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT symbol, timeframe FROM live_watches WHERE expires_msc > ? "
        "ORDER BY symbol, timeframe",
        (now_msc,),
    ).fetchall()
    return [(r["symbol"], r["timeframe"]) for r in rows]


def prune_expired(conn: sqlite3.Connection, now_msc: int) -> int:
    cur = conn.execute("DELETE FROM live_watches WHERE expires_msc <= ?", (now_msc,))
    conn.commit()
    return cur.rowcount


def upsert_forming(conn: sqlite3.Connection, symbol: str, timeframe: str,
                   c: Candle, now_msc: int) -> None:
    if c.time_msc is None or c.time_msc < _MSC_FLOOR:
        raise ValueError(
            f"forming candle time_msc={c.time_msc!r} for {symbol} {timeframe} is "
            f"below {_MSC_FLOOR} — seconds leaked through (Trap 15). Fix the adapter "
            "boundary; never ×1000 here."
        )
    conn.execute(
        "INSERT INTO live_candles "
        "(symbol, timeframe, time_msc, open, high, low, close, tick_volume, spread, real_volume, updated_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol, timeframe) DO UPDATE SET "
        "time_msc=excluded.time_msc, open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, tick_volume=excluded.tick_volume, "
        "spread=excluded.spread, real_volume=excluded.real_volume, updated_msc=excluded.updated_msc",
        (symbol, timeframe, c.time_msc, c.open, c.high, c.low, c.close,
         c.tick_volume, c.spread, c.real_volume, now_msc),
    )
    conn.commit()


def touch_forming(conn: sqlite3.Connection, symbol: str, timeframe: str,
                  now_msc: int) -> bool:
    """Stamp the existing forming row as refreshed WITHOUT changing its prices.

    A bucket with no ticks in it yields no new bar, but the bridge still
    answered for this symbol — and that is what `updated_msc` is read as
    downstream: evidence the feed is being served, not evidence the price moved.
    Returns False when there is no row to stamp.
    """
    cur = conn.execute(
        "UPDATE live_candles SET updated_msc = ? WHERE symbol = ? AND timeframe = ?",
        (now_msc, symbol, timeframe),
    )
    conn.commit()
    return cur.rowcount > 0


def newest_forming(
    conn: sqlite3.Connection, symbol: str, now_msc: int
) -> tuple[int, float] | None:
    """`(updated_msc, close)` of the freshest forming bar for `symbol`, counting
    only timeframes with a LIVE watch. None when nothing is being watched.

    A `live_candles` row outlives its watch — nothing prunes it — so an old
    `updated_msc` on an expired watch means "no one asked `serve_watches` to
    refresh this", not "the feed froze". Joining on an unexpired watch is what
    separates the two; without it, every chart closed an hour ago would read as
    a dead feed.

    The `close` rides along because the one caller that wants the stamp also
    wants the price it belongs to, and reading them from two queries could pair
    a timestamp with a different bar's price.
    """
    row = conn.execute(
        "SELECT c.updated_msc, c.close FROM live_candles c "
        "JOIN live_watches w ON w.symbol = c.symbol AND w.timeframe = c.timeframe "
        "WHERE c.symbol = ? AND w.expires_msc > ? "
        "ORDER BY c.updated_msc DESC LIMIT 1",
        (symbol, now_msc),
    ).fetchone()
    return None if row is None else (int(row["updated_msc"]), float(row["close"]))


def forming_updated_msc(conn: sqlite3.Connection, symbol: str,
                        timeframe: str) -> int | None:
    """When `serve_watches` last refreshed this exact forming row, or None.

    `newest_forming` above answers the same question for the OPEN guard, where
    "actively watched" has to be proven and the timeframe is whatever the chart
    happened to pick. Here the caller already names the timeframe and is itself
    the watcher — `useLiveForming` re-upserts the watch every 12 s — so the join
    would only be able to hide a lapsed watch, and a lapsed watch means the
    prices on screen have genuinely stopped moving. Reading the row plainly is
    both simpler and the safer direction.
    """
    r = conn.execute(
        "SELECT updated_msc FROM live_candles WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    return None if r is None else int(r["updated_msc"])


def read_forming(conn: sqlite3.Connection, symbol: str, timeframe: str) -> Candle | None:
    r = conn.execute(
        "SELECT time_msc, open, high, low, close, tick_volume, spread, real_volume "
        "FROM live_candles WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    if r is None:
        return None
    return Candle(time_msc=r["time_msc"], open=r["open"], high=r["high"], low=r["low"],
                  close=r["close"], tick_volume=r["tick_volume"], spread=r["spread"],
                  real_volume=r["real_volume"])


def upsert_quote(conn: sqlite3.Connection, symbol: str, *, bid: float, ask: float,
                 tick_msc: int, now_msc: int) -> None:
    """Overwrite the single latest-tick row for `symbol`. A latest-value cache,
    like the forming bar — never an append log. `tick_msc` is the broker's tick
    time; `now_msc` is true UTC and is what staleness is judged against."""
    conn.execute(
        "INSERT INTO live_quotes (symbol, bid, ask, tick_msc, updated_msc) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET bid = excluded.bid, "
        "ask = excluded.ask, tick_msc = excluded.tick_msc, "
        "updated_msc = excluded.updated_msc",
        (symbol, bid, ask, tick_msc, now_msc),
    )
    conn.commit()


def read_quote(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    """The latest stored tick for `symbol`, or None if none was ever stored.
    None means unknown — the caller refuses, it does not substitute a price."""
    return conn.execute(
        "SELECT * FROM live_quotes WHERE symbol = ?", (symbol,)
    ).fetchone()
