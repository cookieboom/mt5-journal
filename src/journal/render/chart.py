"""Trade chart renderer — mplfinance PNG from the central candle store.

Pure DB, no MT5 client (mirrors `verify`/`rebuild`): `render_trade(conn,
position_id)` reads `trades` + `candles` and writes a PNG to `cache/`. Charts are
cache, not data (CLAUDE.md rule 6) — always reproducible from the DB, never the
other way around.

Cache identity is keyed on (account_login, position_id, segment), NEVER
`trades.id` — `trades.id` is AUTOINCREMENT and renumbers on every `rebuild`
(docs/mt5-deal-model.md §5), so a filename built from it would go silently stale.
The CLI accepts `position_id` only for the same reason: a saved `journal chart
<id>` command built on `trades.id` could silently render the WRONG trade after a
rebuild, with no error — the exact failure class this project refuses (cf. Trap 5
and why `annotations` keys on `position_id`, never `trades.id`).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")  # headless: no display backend exists or is needed here

import mplfinance as mpf  # noqa: E402 -- must follow matplotlib.use("Agg")
import pandas as pd  # noqa: E402

from ..adapter.base import TIMEFRAMES
from ..store import candles_store
from ..store.db import one_account_login

# --------------------------------------------------------------- TF ladder

# Seconds per bar. TIMEFRAMES (adapter.base, Rule 12's single source) fixes the
# order finest -> coarsest; this just attaches the seconds each one covers.
_TF_SECONDS: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400,
}
assert set(_TF_SECONDS) == set(TIMEFRAMES)

# Finest TF where the trade spans <= this many bars. Measured 2026-07-17
# (docs/mt5-deal-model.md §7): median trade is 7 M1 bars, p75 20, max 685 --
# M15 draws the MEDIAN trade as a single candle, which is not a chart. This cap
# only escalates the tail (durations past ~1h) off M1; in practice M1/M5/M15 are
# the only timeframes this account's history ever picks.
MAX_TRADE_BARS = 60

# Fixed context on each side, in bars of the CHOSEN tf. Combined with the
# <=MAX_TRADE_BARS cap on the trade itself, every window lands in
# [1 + 2*15, 60 + 2*15] = [31, 90] bars -- inside the ~20-90 readable band,
# with no proportional math needed.
PAD_BARS = 15

_TOL = 1e-9  # money/price float comparison tolerance (CLAUDE.md rule 5)

_WIB = timezone(timedelta(hours=7))  # display zone only (CLAUDE.md rule 3)

# allowed mplfinance base styles (light default first)
THEMES: frozenset[str] = frozenset({"charles", "nightclouds", "yahoo"})
PAD_MIN, PAD_MAX = 5, 120


@dataclass(frozen=True)
class RenderOpts:
    theme: str = "charles"
    pad_bars: int = PAD_BARS
    tf_override: str | None = None
    show_sltp: bool = True
    show_markers: bool = True
    show_volume: bool = False
    show_grid: bool = True

    def signature(self) -> str:
        raw = (
            f"{self.theme}|{self.pad_bars}|{self.tf_override}|"
            f"{int(self.show_sltp)}{int(self.show_markers)}"
            f"{int(self.show_volume)}{int(self.show_grid)}"
        )
        return hashlib.sha1(raw.encode()).hexdigest()[:8]


def _clamp_pad(v: object) -> int:
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return PAD_BARS
    return max(PAD_MIN, min(PAD_MAX, n))


def _b(v: object, default: bool) -> bool:
    return v if isinstance(v, bool) else default


def normalize_opts(raw: dict | None) -> RenderOpts:
    """Coerce a stored/DB blob (or None) into a valid RenderOpts. Unknown theme
    -> 'charles'; pad_bars clamped to [5,120]; tf_override must be a known
    timeframe else None; booleans keep defaults when absent/wrong-typed."""
    if not isinstance(raw, dict):
        return RenderOpts()
    theme = raw.get("theme")
    tf = raw.get("tf_override")
    return RenderOpts(
        theme=theme if theme in THEMES else "charles",
        pad_bars=_clamp_pad(raw.get("pad_bars")),
        tf_override=tf if tf in TIMEFRAMES else None,
        show_sltp=_b(raw.get("show_sltp"), True),
        show_markers=_b(raw.get("show_markers"), True),
        show_volume=_b(raw.get("show_volume"), False),
        show_grid=_b(raw.get("show_grid"), True),
    )


def choose_timeframe(duration_s: int) -> str:
    """Finest TF where the trade spans <= MAX_TRADE_BARS bars, floor D1."""
    for tf in TIMEFRAMES:
        if duration_s <= _TF_SECONDS[tf] * MAX_TRADE_BARS:
            return tf
    return TIMEFRAMES[-1]


def window_for(
    open_msc: int, close_msc: int, tf: str, pad_bars: int = PAD_BARS,
) -> tuple[int, int]:
    """+/- `pad_bars` bars of context around [open_msc, close_msc] at `tf`
    granularity. Epoch-ms, SERVER time (no zone conversion here)."""
    pad_ms = pad_bars * _TF_SECONDS[tf] * 1000
    return open_msc - pad_ms, close_msc + pad_ms


# ------------------------------------------------------------------- errors


class TradeNotFoundError(RuntimeError):
    """No `trades` row for (account_login, position_id, segment)."""


class NoCandlesError(RuntimeError):
    """The window has zero candles. One of three look-alike causes (docs §7
    'three different causes of empty chart, no error'): Trap 15 (seconds
    leaked into a `_msc` column), MaxBars truncation, or an unselected symbol.
    We raise instead of drawing a blank PNG so the failure is never silent."""


@dataclass(frozen=True)
class ChartResult:
    path: Path
    timeframe: str
    n_bars: int          # bars actually rendered (trade + padding)
    n_trade_bars: int     # bars the trade itself spans (open bar..close bar)
    same_bar: bool        # entry and exit fall on the same bar (docs §7: 11/68
                           # trades are sub-M1; no TF can separate them)
    sl_drawn: bool
    tp_drawn: bool
    title: str             # exposed so tests can assert on the rendered R/net/
                            # same-bar text without reading PNG pixels


# ------------------------------------------------------------------- lookups


def _load_trade(conn: sqlite3.Connection, login: int, position_id: int, segment: int):
    row = conn.execute(
        "SELECT * FROM trades WHERE account_login = ? AND position_id = ? "
        "AND segment = ?",
        (login, position_id, segment),
    ).fetchone()
    if row is None:
        raise TradeNotFoundError(
            f"no trade for position_id={position_id} segment={segment} "
            f"(account {login}) -- run `journal rebuild`?"
        )
    return row


def _server_offset_s(conn: sqlite3.Connection, login: int) -> int:
    """Trap 7: read the MEASURED offset, never hardcode 0. `sync` writes the
    same reading to both the 'deals' and 'orders' rows in `sync_state`, so the
    most recent non-NULL one is authoritative. Falls back to 0 only when
    nothing has EVER been measured (no sync run yet) -- a fresh-DB default,
    not an assumption made in the presence of real data."""
    row = conn.execute(
        "SELECT server_utc_offset_s FROM sync_state "
        "WHERE account_login = ? AND server_utc_offset_s IS NOT NULL "
        "ORDER BY measured_at DESC LIMIT 1",
        (login,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _currency(conn: sqlite3.Connection, login: int) -> str:
    row = conn.execute(
        "SELECT currency FROM accounts WHERE login = ?", (login,)
    ).fetchone()
    return (row[0] if row is not None else "") or ""


def _nearest_bar_index(rows: Sequence[sqlite3.Row], target_msc: int) -> int:
    """Index of the last bar whose open time_msc <= target_msc -- the bar the
    event happened within. Clamps to the first row if `target_msc` precedes
    every bar in the window (should not happen: `window_for` pads before
    `open` and after `close`; a market gap could still make this the closest
    honest answer rather than raising)."""
    idx = 0
    for i, r in enumerate(rows):
        if r["time_msc"] <= target_msc:
            idx = i
        else:
            break
    return idx


def _human_duration(duration_s: int) -> str:
    if duration_s < 60:
        return f"{duration_s}s"
    m, s = divmod(duration_s, 60)
    if m < 60:
        return f"{m}m{s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _to_wib(server_msc: int, offset_s: int) -> datetime:
    """server_msc is broker-server time (schema.sql); true UTC = server -
    offset (Trap 7). Display zone is WIB = UTC+7, chosen consciously here
    because the chart is this project's primary display surface and CLAUDE.md
    rule 3 says convert to WIB only at display time -- never silently."""
    true_utc_s = server_msc / 1000 - offset_s
    return datetime.fromtimestamp(true_utc_s, tz=timezone.utc).astimezone(_WIB)


# --------------------------------------------------------------------- core


def render_trade(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    opts: RenderOpts = RenderOpts(),
    cache_dir: str | Path = "cache",
    segment: int = 0,
) -> ChartResult:
    """Render one trade to a PNG in `cache_dir`, named by the stable cache key
    (account_login, position_id, segment, opts.signature()) -- never
    `trades.id`. Charts are cache, not data (rule 6): different `opts` must
    land in different files so no render can silently overwrite another.

    `opts.tf_override` overrides the duration-based ladder (`choose_timeframe`)
    when given. Raises `TradeNotFoundError` / `NoCandlesError` rather than ever
    writing a silently blank or wrong chart.
    """
    login = one_account_login(conn)
    trade = _load_trade(conn, login, position_id, segment)

    duration_s = trade["duration_s"]
    if duration_s is None or trade["close_time_msc"] is None:
        raise ValueError(
            f"trade position_id={position_id} has no close (status="
            f"{trade['status']!r}); only closed trades can be charted in M3"
        )

    chosen_tf = opts.tf_override or choose_timeframe(duration_s)
    if chosen_tf not in _TF_SECONDS:
        raise ValueError(f"unknown timeframe {chosen_tf!r}; expected one of {TIMEFRAMES}")

    from_msc, to_msc = window_for(
        trade["open_time_msc"], trade["close_time_msc"], chosen_tf, opts.pad_bars,
    )

    rows = candles_store.read_candles(conn, trade["symbol"], chosen_tf, from_msc, to_msc)

    if not rows:
        raise NoCandlesError(
            f"no candles for {trade['symbol']} {chosen_tf} in "
            f"[{from_msc}, {to_msc}] -- run `journal candles` first"
        )

    offset_s = _server_offset_s(conn, login)
    ccy = _currency(conn, login)

    index = pd.DatetimeIndex(
        [_to_wib(r["time_msc"], offset_s) for r in rows], name="Date"
    )
    df = pd.DataFrame(
        {
            "Open": [r["open"] for r in rows],
            "High": [r["high"] for r in rows],
            "Low": [r["low"] for r in rows],
            "Close": [r["close"] for r in rows],
            "Volume": [r["tick_volume"] or 0 for r in rows],
        },
        index=index,
    )

    open_idx = _nearest_bar_index(rows, trade["open_time_msc"])
    close_idx = _nearest_bar_index(rows, trade["close_time_msc"])
    same_bar = open_idx == close_idx
    n_bars = len(rows)
    n_trade_bars = close_idx - open_idx + 1

    is_buy = trade["direction"] == "buy"

    addplots: list = []
    if opts.show_markers:
        entry_marker = pd.Series(index=df.index, dtype=float)
        exit_marker = pd.Series(index=df.index, dtype=float)
        entry_marker.iloc[open_idx] = trade["open_price"]
        exit_marker.iloc[close_idx] = trade["close_price"]

        addplots = [
            mpf.make_addplot(
                entry_marker, type="scatter", markersize=100,
                marker="^" if is_buy else "v", color="blue",
            ),
            mpf.make_addplot(
                exit_marker, type="scatter", markersize=100,
                marker="v" if is_buy else "^", color="darkorange",
            ),
        ]

    # SL/TP hlines: guard on the VALUE being a real, non-zero price -- NOT on
    # `is not None`. `0.0 is not None` is True, and drawing an hline at price 0
    # on a XAUUSDc chart (prices ~4000) collapses the whole y-axis to 0..4000,
    # shrinking every candle to an unreadable sliver -- a junk chart with NO
    # error. This is the exact Trap 6 shape M2.1 already paid for on
    # `r_multiple` (a `risk is not None` gate passing for a real 0.0 and
    # raising ZeroDivisionError): gate on the value, not on not-None. No trade
    # has `sl_initial == 0.0` today (62 NULL + 6 non-zero, docs §7) -- M4's
    # poller will start writing confirmed-no-SL as a real 0.0, so this guard
    # must already be correct.
    sl = trade["sl_initial"]
    tp = trade["tp_initial"]
    sl_drawn = opts.show_sltp and sl is not None and abs(sl) > _TOL
    tp_drawn = opts.show_sltp and tp is not None and abs(tp) > _TOL

    hlines_prices: list[float] = []
    hlines_colors: list[str] = []
    if sl_drawn:
        hlines_prices.append(sl)
        hlines_colors.append("red")
    if tp_drawn:
        hlines_prices.append(tp)
        hlines_colors.append("green")

    # R and net_profit: NULL means unknown, 0.0 means a known value of zero
    # (CLAUDE.md rule 4 / docs Trap 6) -- `x or 'n/a'` would wrongly claim
    # "unknown" for a genuine 0.0. Money carries the currency code, never '$'
    # (Trap 13).
    r = trade["r_multiple"]
    r_text = "n/a" if r is None else f"{r:.2f}"
    net = trade["net_profit"]
    net_text = "n/a" if net is None else f"{net:.2f} {ccy}"

    title = (
        f"{trade['symbol_base']} {trade['direction'].upper()} | "
        f"{_human_duration(duration_s)} | net {net_text} | R {r_text} "
        f"[{chosen_tf}, times WIB]"
    )
    if same_bar:
        title += f"\n{duration_s}s -- entry & exit within one {chosen_tf} bar"

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{login}-{position_id}-seg{segment}-{opts.signature()}.png"

    style = mpf.make_mpf_style(
        base_mpf_style=opts.theme, rc={"axes.grid": opts.show_grid}
    )
    plot_kwargs: dict = dict(
        type="candle",
        style=style,
        addplot=addplots if addplots else None,
        title=title,
        volume=opts.show_volume,
        savefig=dict(fname=str(out_path), dpi=150),
    )
    if not addplots:
        plot_kwargs.pop("addplot")
    if hlines_prices:
        plot_kwargs["hlines"] = dict(
            hlines=hlines_prices, colors=hlines_colors, linestyle="--", linewidths=1,
        )

    mpf.plot(df, **plot_kwargs)

    return ChartResult(
        path=out_path,
        timeframe=chosen_tf,
        n_bars=n_bars,
        n_trade_bars=n_trade_bars,
        same_bar=same_bar,
        sl_drawn=sl_drawn,
        tp_drawn=tp_drawn,
        title=title,
    )
