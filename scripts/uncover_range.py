#!/usr/bin/env python
"""Forget the coverage memo for a range so the normal fill path fetches it again.

One-off repair for holes sealed by the pre-2026-08-05 coverage bug: a truncated
bridge response was recorded as if it had covered the whole requested span, so
`missing_ranges` stopped offering those minutes and the bars could never arrive.
The bars themselves are untouched — `candle_coverage` is only a memo of what has
been fetched, so the worst case of forgetting too much is a few extra bridge
calls the next time a chart shows that window.

    uv run python scripts/uncover_range.py XAUUSDc M1 "2026-08-05 08:00" "2026-08-05 08:15"

Times are WIB (UTC+7), matching what the chart shows. Then open the chart on that
range (or let `journal live` run) and the gap fills itself.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from journal.store import candles_store as cs
from journal.store.db import connect

WIB = dt.timezone(dt.timedelta(hours=7))


def _wib_to_ms(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=WIB).timestamp() * 1000)


def _fmt(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, WIB).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("symbol")
    p.add_argument("timeframe")
    p.add_argument("from_wib", help='e.g. "2026-08-05 08:00"')
    p.add_argument("to_wib", help='e.g. "2026-08-05 08:15"')
    p.add_argument("--db", default="data/journal.db", type=Path)
    a = p.parse_args()

    from_ms, to_ms = _wib_to_ms(a.from_wib), _wib_to_ms(a.to_wib)
    conn = connect(a.db)
    before = cs.read_coverage(conn, a.symbol, a.timeframe)
    after = cs.forget_coverage(conn, a.symbol, a.timeframe, from_ms, to_ms)
    conn.commit()
    print(f"{a.symbol} {a.timeframe}: forgot {_fmt(from_ms)} — {_fmt(to_ms)} WIB "
          f"({len(before)} coverage range(s) → {len(after)})")
    print("Open the chart on that range (or let `journal live` run) to refetch.")


if __name__ == "__main__":
    main()
