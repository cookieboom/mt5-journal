"""Render a `WeeklyResult` to Markdown (M6.1).

Pure string builder — no DB, no file I/O (the CLI writes the returned text to
`cache/`). Money is never printed with `$`; every figure carries the account
currency (Trap 13). Gated values (a rate/average over a week of <20 trades)
render as `n/a` with the count beside them, never a bare number pretending to
be reliable (§9) — the same honesty `journal report` uses.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..analytics.weekly import BucketStat, WeeklyResult


def _date(msc: int) -> str:
    return datetime.fromtimestamp(msc / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _money(x: float | None, ccy: str = "", *, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    s = f"{x:+.2f}" if sign else f"{x:.2f}"
    return f"{s} {ccy}".strip()


def _gated(n: int, avg: float | None, ccy: str = "", *, sign: bool = False) -> str:
    """`n/a (n=X, need ≥20)` when withheld, else the value with its n — mirrors
    the CLI report's `_gated`, so weekly and account reports read the same."""
    if avg is None:
        return f"n/a (n={n}, need ≥20)"
    return f"{_money(avg, ccy, sign=sign)}  (n={n})"


def _bucket_rows(buckets: tuple[BucketStat, ...], ccy: str) -> list[str]:
    out = []
    for b in buckets:
        wr = "n/a" if b.win_rate is None else f"{b.win_rate * 100:.1f}%"
        exp = _money(b.expectancy, ccy, sign=True)
        out.append(f"| {b.label} | {b.n} | {wr} | {exp} |")
    return out


def render_weekly_md(result: WeeklyResult) -> str:
    r = result
    ccy = r.currency
    # end_msc is the exclusive next-Monday 00:00; the week's last day is -1 day.
    last_day = _date(r.end_msc - 86_400_000)
    L: list[str] = []

    L.append(f"# Week {r.iso_year}-W{r.iso_week:02d}")
    L.append("")
    L.append(f"_{_date(r.start_msc)} → {last_day} (UTC) · account {r.account_login} ({ccy})_")
    L.append("")

    L.append("## Summary")
    L.append("")
    L.append(f"- **{r.n_closed}** trades closed — {r.n_wins} win, {r.n_losses} loss, "
             f"{r.n_breakeven} breakeven")
    L.append(f"- **Realized:** {_money(r.net_total, ccy, sign=True)}")
    L.append("")

    L.append("## Money (gated per §9: needs n≥20)")
    L.append("")
    L.append("| stat | value |")
    L.append("|---|---|")
    wr = "n/a" if r.win_rate is None else f"{r.win_rate * 100:.1f}%"
    L.append(f"| win rate | {wr if r.win_rate is not None else f'n/a (n={r.n_closed}, need ≥20)'} |")
    L.append(f"| avg win | {_money(r.avg_win, ccy)} |")
    L.append(f"| avg loss | {_money(r.avg_loss, ccy, sign=True)} |")
    L.append(f"| profit factor | {_money(r.profit_factor)} |")
    L.append(f"| expectancy | {_money(r.expectancy, ccy, sign=True)} |")
    L.append("")

    L.append("## By session (UTC)")
    L.append("")
    L.append("| session | n | win | exp |")
    L.append("|---|---|---|---|")
    L.extend(_bucket_rows(r.by_session, ccy))
    L.append("")

    L.append("## By source")
    L.append("")
    L.append("| source | n | win | exp |")
    L.append("|---|---|---|---|")
    L.extend(_bucket_rows(r.by_source, ccy))
    L.append("")

    L.append("## Annotated trades")
    L.append("")
    if not r.notes:
        L.append("_None annotated or manually tagged this week._")
    else:
        for note in r.notes:
            bits = []
            if note.setup:
                bits.append(f"setup: {note.setup}")
            if note.confidence is not None:
                bits.append(f"confidence: {note.confidence}/5")
            if note.emotion:
                bits.append(f"emotion: {note.emotion}")
            if note.followed_plan is not None:
                bits.append("followed plan" if note.followed_plan else "broke plan")
            meta = " · ".join(bits)
            L.append(f"### #{note.position_id} — {note.symbol_base} "
                     f"({_money(note.net_profit, ccy, sign=True)})")
            if meta:
                L.append(f"_{meta}_")
            if note.tags:
                L.append(f"tags: {', '.join(note.tags)}")
            if note.notes:
                L.append("")
                L.append(f"> {note.notes}")
            L.append("")

    return "\n".join(L).rstrip() + "\n"
