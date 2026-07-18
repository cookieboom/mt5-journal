"""Trading-session bucketing (M5.1).

Maps a trade's open time to one of five fixed UTC trading-session windows. The
broker server clock IS UTC — `server_utc_offset_s = 0`, confirmed (docs §7 /
CLAUDE.md) — so the session hour is read directly off the epoch-ms timestamp
with no offset conversion. This is pure: no MT5, no I/O, no DB (rules 1, 12), so
its tests come first (rule 7).

Windows are half-open ``[start, end)`` in UTC hours and tile the whole day, so
every instant belongs to exactly one session and no trade can fall out of the
buckets:

    Asian    00:00–07:00
    London   07:00–12:00
    LDN/NY   12:00–16:00   (London/New York overlap)
    New York 16:00–21:00
    Late     21:00–24:00

A low count in any bucket may simply mean the symbol was not open then
(BTCUSDc trades 24/7 but XAUUSDc/EURUSDc do not — docs §7); callers must report
raw counts, never divide by a "hours available" denominator we do not have.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Canonical order — chronological across the UTC day. Report code emits every
# bucket in this order, present even when empty, so the table shape is stable.
SESSION_ORDER: tuple[str, ...] = ("Asian", "London", "LDN/NY", "New York", "Late")

# (start_hour_inclusive, label). The last window runs to 24:00; a UTC hour is
# always in [0, 23], so the final entry catches everything at or after 21:00.
_WINDOWS: tuple[tuple[int, str], ...] = (
    (0, "Asian"),
    (7, "London"),
    (12, "LDN/NY"),
    (16, "New York"),
    (21, "Late"),
)


def session_of(open_time_msc: int) -> str:
    """Trading session an epoch-ms (UTC) open time falls in.

    Uses the repo's canonical ``datetime.fromtimestamp(ms / 1000,
    tz=timezone.utc)`` idiom (candles.py / chart.py) — never the deprecated,
    naive ``utcfromtimestamp`` (rule 3: never a naive datetime).
    """
    hour = datetime.fromtimestamp(open_time_msc / 1000, tz=timezone.utc).hour
    label = _WINDOWS[0][1]
    for start, name in _WINDOWS:
        if hour >= start:
            label = name
        else:
            break
    return label
