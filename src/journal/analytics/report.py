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

# docs §9: a bucket under this many trades is noise, not a statistic.
_MIN_N = 20

# Float comparison tolerance (CLAUDE.md rule 5 — never ==/>/< a raw REAL).
# Classifying win/loss/breakeven is the one place in this report where
# getting this wrong silently corrupts every downstream count.
_TOL = 1e-9


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
        "SELECT net_profit, r_multiple, mae, mae_r, mfe_r FROM trades "
        "WHERE account_login = ? AND status = 'closed'",
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
    )
