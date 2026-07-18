"""session_of: pure UTC-hour → trading-session classifier (M5.1).

The broker server clock IS UTC (server_utc_offset_s = 0, confirmed — docs §7),
so the session is read straight off the hour with no offset. Windows are
half-open [start, end) so every instant belongs to exactly one session and the
day tiles with no gap and no overlap. Tests come first (CLAUDE.md rule 7).
"""

from datetime import datetime, timezone

from journal.analytics.sessions import SESSION_ORDER, session_of


def _ms(hour: int, minute: int = 0, second: int = 0) -> int:
    """Epoch ms (UTC) at a fixed date and the given UTC wall-clock time. The
    date is arbitrary — session_of only looks at the hour — but building it via
    an explicitly UTC datetime is the point: it proves the classifier reads UTC,
    not local time."""
    dt = datetime(2026, 1, 15, hour, minute, second, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def test_each_windows_interior():
    assert session_of(_ms(3)) == "Asian"
    assert session_of(_ms(9)) == "London"
    assert session_of(_ms(14)) == "LDN/NY"
    assert session_of(_ms(18)) == "New York"
    assert session_of(_ms(22)) == "Late"


def test_half_open_boundaries():
    # Each boundary belongs to the LATER session ([start, end) semantics), and
    # the last second of the prior window still belongs to the earlier one.
    assert session_of(_ms(0, 0, 0)) == "Asian"       # day start
    assert session_of(_ms(6, 59, 59)) == "Asian"
    assert session_of(_ms(7, 0, 0)) == "London"
    assert session_of(_ms(11, 59, 59)) == "London"
    assert session_of(_ms(12, 0, 0)) == "LDN/NY"
    assert session_of(_ms(15, 59, 59)) == "LDN/NY"
    assert session_of(_ms(16, 0, 0)) == "New York"
    assert session_of(_ms(20, 59, 59)) == "New York"
    assert session_of(_ms(21, 0, 0)) == "Late"
    assert session_of(_ms(23, 59, 59)) == "Late"      # day end


def test_reads_utc_not_local():
    # A real timestamp from §7's history span: 2025-12-08 13:56 UTC. In UTC that
    # is the London/NY overlap. Under any negative-offset local zone it would
    # slip into London (or earlier) — this pins that we do NOT convert.
    dt = datetime(2025, 12, 8, 13, 56, tzinfo=timezone.utc)
    assert session_of(int(dt.timestamp() * 1000)) == "LDN/NY"


def test_every_hour_maps_into_session_order():
    # The day tiles completely: every UTC hour returns a label that is in the
    # canonical order, so no trade can ever fall out of the buckets.
    for hour in range(24):
        assert session_of(_ms(hour)) in SESSION_ORDER


def test_session_order_is_the_five_windows_in_time_order():
    assert SESSION_ORDER == ("Asian", "London", "LDN/NY", "New York", "Late")
