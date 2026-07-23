"""`journal report` — a first, honest read of this account's performance (M5).

Money-based stats (win rate, avg win/loss, profit factor, expectancy) have
FULL coverage: every closed trade has a `net_profit`. R-based stats do not —
`sl_initial` is recoverable for only 6/68 trades so far (docs §7's EA-only
measurement), and `mae_r`/`mfe_r` additionally need candle coverage (M3) on
top of that, so their `n` is smaller still. Every reported number carries its
`n`; any statistic computed over `n < 20` is suppressed, never silently shown
as if it were reliable (docs §9). Build the pipeline anyway (§9's own
instruction) — the R-family sections will mostly read "insufficient data" for
a long time, by design, not by bug, and grow slowly as the M4 poller covers
more trades.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..store.db import one_account_login
from .sessions import SESSION_ORDER, session_of

# docs §9: a bucket under this many trades is noise, not a statistic.
_MIN_N = 20

# Float comparison tolerance (CLAUDE.md rule 5 — never ==/>/< a raw REAL).
# Classifying win/loss/breakeven is the one place in this report where
# getting this wrong silently corrupts every downstream count.
_TOL = 1e-9


@dataclass(frozen=True)
class BucketStat:
    """One row of a behaviour breakdown (a session, or EA/discretionary). Same
    gating discipline as the top-level report (docs §9): the RAW counts (`n`,
    `n_with_r`) are always shown — they are diagnostics, "how much data exists
    yet", not averages — while every *averaged* field is pre-gated to `None`
    when its own `n` is below `_MIN_N`, so a thin bucket can never masquerade as
    a reliable statistic. On this account most buckets sit under the gate for a
    long time by design (EA is 6 trades), exactly as M5's R-family sections do.
    """
    label: str
    n: int                    # closed trades in bucket — raw count, always shown
    win_rate: float | None    # n_wins / n; None if n < _MIN_N (gated) or n == 0
    expectancy: float | None  # mean net_profit; None if n < _MIN_N or n == 0
    n_with_r: int             # raw diagnostic, always shown
    avg_r: float | None       # None unless n_with_r >= _MIN_N


@dataclass(frozen=True)
class ReportResult:
    account_login: int
    currency: str  # never format a money field without this (Trap 13)

    n_total: int
    n_closed: int
    n_wins: int
    n_losses: int
    n_breakeven: int

    win_rate: float | None       # n_wins / n_closed; None if n_closed == 0
    avg_win: float | None        # money; None if no wins
    avg_loss: float | None       # money, kept NEGATIVE; None if no losses
    profit_factor: float | None  # sum(wins) / abs(sum(losses)); None if no losses
    expectancy: float | None     # mean(net_profit) over closed trades

    n_with_r: int
    avg_r: float | None          # None unless n_with_r >= _MIN_N

    n_with_mae: int              # candle-coverage count — always shown, never
                                  # gated: a plain "how much data exists yet"
                                  # diagnostic, not an averaged statistic.
    n_with_mae_r: int
    avg_mae_r: float | None      # None unless n_with_mae_r >= _MIN_N
    n_with_mfe_r: int
    avg_mfe_r: float | None      # None unless n_with_mfe_r >= _MIN_N

    # M5.1 behaviour breakdowns. Each is the FULL set of buckets in a fixed
    # order, always present even when empty, so the rendered table shape never
    # shifts: by_session in SESSION_ORDER; by_source as (EA, Discretionary).
    by_session: tuple[BucketStat, ...]
    by_source: tuple[BucketStat, ...]

    # M8 symbol breakdown (rule 11 / trap 12): grouped by symbol_base, NOT the
    # verbatim symbol. Unlike the two above this set is DATA-DRIVEN — it is
    # exactly the distinct symbol_base among closed trades, ordered ascending, so
    # it is empty on a fresh account and grows a bucket when a new symbol trades.
    # Do not "fix" this into a fixed tuple: there is no closed set of symbols.
    by_symbol: tuple[BucketStat, ...]


def bucket_stat(label: str, rows: list[sqlite3.Row]) -> BucketStat:
    """Aggregate one bucket's closed-trade rows into a `BucketStat`, reusing the
    exact win/loss classification (`_TOL`, rule 5) and §9 gating the top-level
    report uses — so a bucket and the whole-account figure can never disagree on
    what a "win" is. win_rate and expectancy are averages, so they are gated by
    the bucket's own `n`; the `n >= _MIN_N` guard also makes the division safe
    for empty buckets (n == 0 → None, no ZeroDivisionError)."""
    n = len(rows)
    n_wins = sum(1 for r in rows if r["net_profit"] > _TOL)

    if n >= _MIN_N:
        win_rate = n_wins / n
        expectancy = sum(r["net_profit"] for r in rows) / n
    else:
        win_rate = None
        expectancy = None

    r_values = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    n_with_r = len(r_values)
    avg_r = (sum(r_values) / n_with_r) if n_with_r >= _MIN_N else None

    return BucketStat(
        label=label,
        n=n,
        win_rate=win_rate,
        expectancy=expectancy,
        n_with_r=n_with_r,
        avg_r=avg_r,
    )


def build_report(conn: sqlite3.Connection) -> ReportResult:
    """Pure DB read, no client — mirrors `verify`/`rebuild`. Resolves the
    account login internally (matches `rebuild`/`render_trade`/`sync_candles`'s
    convention; taking `login` as a parameter would be the odd one out)."""
    login = one_account_login(conn)
    ccy_row = conn.execute(
        "SELECT currency FROM accounts WHERE login = ?", (login,)
    ).fetchone()
    currency = (ccy_row[0] if ccy_row else "") or ""

    (n_total,) = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_login = ?", (login,)
    ).fetchone()

    rows = conn.execute(
        "SELECT net_profit, r_multiple, mae, mae_r, mfe_r, open_time_msc, magic, "
        "symbol_base "
        "FROM trades WHERE account_login = ? AND status = 'closed'",
        (login,),
    ).fetchall()
    n_closed = len(rows)

    # Tolerance-based classification (rule 5) — everything below is derived
    # from these three lists, so getting this comparison wrong would silently
    # corrupt win_rate, avg_win, avg_loss, profit_factor all at once.
    wins = [r["net_profit"] for r in rows if r["net_profit"] > _TOL]
    losses = [r["net_profit"] for r in rows if r["net_profit"] < -_TOL]
    n_wins, n_losses = len(wins), len(losses)
    n_breakeven = n_closed - n_wins - n_losses

    win_rate = (n_wins / n_closed) if n_closed else None
    avg_win = (sum(wins) / n_wins) if wins else None
    avg_loss = (sum(losses) / n_losses) if losses else None
    expectancy = (sum(r["net_profit"] for r in rows) / n_closed) if n_closed else None

    # profit_factor: the SAME ZeroDivisionError-guard shape r_multiple already
    # needed (Trap 6 / M2.1), and mae_r/mfe_r needed again in reconstruct.py's
    # _fill_excursions — a third occurrence of one pattern, not three separate
    # bugs. An all-wins account has losses_sum == 0.0, a KNOWN zero (no losing
    # trades occurred), not an unknown one — gate on it being TRUTHY, never
    # `is not None`, or the first all-wins account ZeroDivisionErrors the report.
    losses_sum = sum(losses)  # <= 0, or exactly 0.0 if `losses` is empty
    profit_factor = (sum(wins) / abs(losses_sum)) if losses_sum else None

    r_values = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    n_with_r = len(r_values)
    avg_r = (sum(r_values) / n_with_r) if n_with_r >= _MIN_N else None

    n_with_mae = sum(1 for r in rows if r["mae"] is not None)

    mae_r_values = [r["mae_r"] for r in rows if r["mae_r"] is not None]
    n_with_mae_r = len(mae_r_values)
    avg_mae_r = (sum(mae_r_values) / n_with_mae_r) if n_with_mae_r >= _MIN_N else None

    mfe_r_values = [r["mfe_r"] for r in rows if r["mfe_r"] is not None]
    n_with_mfe_r = len(mfe_r_values)
    avg_mfe_r = (sum(mfe_r_values) / n_with_mfe_r) if n_with_mfe_r >= _MIN_N else None

    # Session breakdown — every bucket in SESSION_ORDER, present even when empty.
    # Server clock is UTC (docs §7), so session_of reads the hour with no offset.
    session_groups: dict[str, list[sqlite3.Row]] = {label: [] for label in SESSION_ORDER}
    for r in rows:
        session_groups[session_of(r["open_time_msc"])].append(r)
    by_session = tuple(
        bucket_stat(label, session_groups[label]) for label in SESSION_ORDER
    )

    # Source breakdown — EA vs discretionary. docs §7: magic != 0 ⟺ EA. Rule 4:
    # an unknown (NULL) magic is not evidence of EA, so NULL and 0 both fall to
    # discretionary; a truthy magic is EA.
    ea_rows = [r for r in rows if r["magic"]]
    disc_rows = [r for r in rows if not r["magic"]]
    by_source = (
        bucket_stat("EA", ea_rows),
        bucket_stat("Discretionary", disc_rows),
    )

    # Symbol breakdown — grouped by symbol_base (rule 11 / trap 12), NEVER the
    # verbatim `symbol`. Unlike session/source this set is data-driven, so order
    # it deterministically (symbol_base ascending) rather than from a fixed tuple;
    # a fresh account has no closed trades and so an empty by_symbol.
    symbol_groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        symbol_groups.setdefault(r["symbol_base"], []).append(r)
    by_symbol = tuple(
        bucket_stat(sb, symbol_groups[sb]) for sb in sorted(symbol_groups)
    )

    return ReportResult(
        account_login=login,
        currency=currency,
        n_total=n_total,
        n_closed=n_closed,
        n_wins=n_wins,
        n_losses=n_losses,
        n_breakeven=n_breakeven,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        n_with_r=n_with_r,
        avg_r=avg_r,
        n_with_mae=n_with_mae,
        n_with_mae_r=n_with_mae_r,
        avg_mae_r=avg_mae_r,
        n_with_mfe_r=n_with_mfe_r,
        avg_mfe_r=avg_mfe_r,
        by_session=by_session,
        by_source=by_source,
        by_symbol=by_symbol,
    )
