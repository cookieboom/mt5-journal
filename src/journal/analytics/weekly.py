"""`journal weekly` — one ISO week of this account, for a weekly review (M6.1).

This is `analytics/report.py` scoped to a single ISO week (Mon–Sun UTC). A trade
is attributed to the week its `close_time_msc` falls in — realized P&L lands the
week it closed — over the half-open interval `[Monday 00:00, next Monday 00:00)`.
Server time is UTC (`server_utc_offset_s = 0`, docs §7), so the week math needs
no offset.

Aggregate rates/averages are gated by §9's `n≥20` exactly as the account report
gates its buckets (reusing `bucket_stat`, `_MIN_N`, `_TOL` — one definition of
"a win"). A single week almost never clears the gate, so `win_rate`/`expectancy`
etc. usually read `None`; that is honest, not a bug. What a weekly review is
actually FOR — the raw count, the realized net total (a sum, always shown), and
the trades you annotated or tagged — is never gated.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..store.db import one_account_login
from .report import _MIN_N, _TOL, BucketStat, bucket_stat
from .sessions import SESSION_ORDER, session_of


def iso_week_bounds_ms(iso_year: int, iso_week: int) -> tuple[int, int]:
    """`[start, end)` epoch-ms UTC for an ISO week: Monday 00:00 to next Monday."""
    start = datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def last_complete_iso_week(now: datetime | None = None) -> tuple[int, int]:
    """The most recent ISO week entirely before `now` (default: current UTC time).
    Its Monday is one week before the Monday of `now`'s own ISO week."""
    now = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    this_monday = datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=timezone.utc)
    prev = this_monday - timedelta(days=7)
    y, w, _ = prev.isocalendar()
    return y, w


@dataclass(frozen=True)
class TradeNote:
    """One trade in the week that carries human context — an annotation or a
    manual tag. `tags` is every tag on the trade (auto + manual) for display."""
    position_id: int
    symbol_base: str
    net_profit: float
    setup: str | None
    confidence: int | None
    emotion: str | None
    followed_plan: int | None
    notes: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyResult:
    account_login: int
    currency: str  # never format a money field without it (Trap 13)
    iso_year: int
    iso_week: int
    start_msc: int
    end_msc: int

    n_closed: int
    n_wins: int
    n_losses: int
    n_breakeven: int
    net_total: float            # realized sum for the week — a sum, always shown

    win_rate: float | None      # gated: None unless n_closed >= _MIN_N
    avg_win: float | None       # gated
    avg_loss: float | None      # gated (kept negative)
    profit_factor: float | None # gated
    expectancy: float | None    # gated

    by_session: tuple[BucketStat, ...]  # SESSION_ORDER, always all present
    by_source: tuple[BucketStat, ...]   # (EA, Discretionary)
    notes: tuple[TradeNote, ...]        # trades with an annotation or manual tag


def build_weekly(conn: sqlite3.Connection, iso_year: int, iso_week: int) -> WeeklyResult:
    """Pure DB read, no client — mirrors `build_report`, scoped to one ISO week
    by `close_time_msc`. Resolves the account login internally (the codebase
    convention; never a parameter)."""
    login = one_account_login(conn)
    ccy_row = conn.execute(
        "SELECT currency FROM accounts WHERE login = ?", (login,)
    ).fetchone()
    currency = (ccy_row[0] if ccy_row else "") or ""

    start_ms, end_ms = iso_week_bounds_ms(iso_year, iso_week)
    rows = conn.execute(
        "SELECT position_id, symbol_base, net_profit, r_multiple, open_time_msc, magic "
        "FROM trades WHERE account_login = ? AND status = 'closed' "
        "AND close_time_msc >= ? AND close_time_msc < ?",
        (login, start_ms, end_ms),
    ).fetchall()
    n_closed = len(rows)

    # Same tolerance-based classification as build_report (rule 5, _TOL).
    wins = [r["net_profit"] for r in rows if r["net_profit"] > _TOL]
    losses = [r["net_profit"] for r in rows if r["net_profit"] < -_TOL]
    n_wins, n_losses = len(wins), len(losses)
    n_breakeven = n_closed - n_wins - n_losses
    net_total = sum(r["net_profit"] for r in rows)

    # Weekly rates/averages ARE gated by n (§9): a week rarely has 20 trades, so
    # a bare "100% win rate (n=2)" is exactly the misleading figure §9 forbids.
    if n_closed >= _MIN_N:
        win_rate = n_wins / n_closed
        avg_win = (sum(wins) / n_wins) if wins else None
        avg_loss = (sum(losses) / n_losses) if losses else None
        expectancy = net_total / n_closed
        # Same all-wins ZeroDivision guard shape as build_report: gate on truthy.
        losses_sum = sum(losses)
        profit_factor = (sum(wins) / abs(losses_sum)) if losses_sum else None
    else:
        win_rate = avg_win = avg_loss = expectancy = profit_factor = None

    session_groups: dict[str, list[sqlite3.Row]] = {label: [] for label in SESSION_ORDER}
    for r in rows:
        session_groups[session_of(r["open_time_msc"])].append(r)
    by_session = tuple(
        bucket_stat(label, session_groups[label]) for label in SESSION_ORDER
    )
    ea_rows = [r for r in rows if r["magic"]]
    disc_rows = [r for r in rows if not r["magic"]]
    by_source = (bucket_stat("EA", ea_rows), bucket_stat("Discretionary", disc_rows))

    notes = _week_notes(conn, login, [r["position_id"] for r in rows])

    return WeeklyResult(
        account_login=login,
        currency=currency,
        iso_year=iso_year,
        iso_week=iso_week,
        start_msc=start_ms,
        end_msc=end_ms,
        n_closed=n_closed,
        n_wins=n_wins,
        n_losses=n_losses,
        n_breakeven=n_breakeven,
        net_total=net_total,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        by_session=by_session,
        by_source=by_source,
        notes=notes,
    )


def _week_notes(
    conn: sqlite3.Connection, login: int, position_ids: list[int]
) -> tuple[TradeNote, ...]:
    """The week's trades that carry human context — an annotation OR at least one
    MANUAL tag. Auto tags alone don't qualify (they're machine facts, on nearly
    every trade); they're still listed in `tags` for context once a trade is in.
    Reads annotations through the `v_trades_annotated` view (schema §human layer)."""
    notes: list[TradeNote] = []
    for pid in position_ids:
        ann = conn.execute(
            "SELECT symbol_base, net_profit, setup, confidence, emotion, "
            "followed_plan, notes FROM v_trades_annotated "
            "WHERE account_login = ? AND position_id = ?",
            (login, pid),
        ).fetchone()
        if ann is None:
            continue
        tag_rows = conn.execute(
            "SELECT tag, source FROM tags WHERE account_login = ? AND position_id = ? "
            "ORDER BY source, tag",
            (login, pid),
        ).fetchall()
        has_annotation = ann["setup"] is not None or ann["notes"] is not None \
            or ann["confidence"] is not None or ann["emotion"] is not None \
            or ann["followed_plan"] is not None
        has_manual_tag = any(t["source"] == "manual" for t in tag_rows)
        if not (has_annotation or has_manual_tag):
            continue
        notes.append(TradeNote(
            position_id=pid,
            symbol_base=ann["symbol_base"],
            net_profit=ann["net_profit"],
            setup=ann["setup"],
            confidence=ann["confidence"],
            emotion=ann["emotion"],
            followed_plan=ann["followed_plan"],
            notes=ann["notes"],
            tags=tuple(t["tag"] for t in tag_rows),
        ))
    return tuple(notes)
