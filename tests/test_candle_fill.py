import sqlite3

from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candles_store as cs
from journal.ingest.candle_fill import fill_range

M1 = 60_000
BASE = 1_700_000_000_000

class FakeRates:
    """Minimal MT5Client stub: scripts copy_rates_range and counts calls.
    (Same local-fake pattern as tests/test_poller.py::FakePositionsClient.)"""
    def __init__(self, bars_by_range):
        self.bars_by_range = bars_by_range   # {(from_ms, to_ms): [Candle,...]}
        self.calls = []
    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        f = int(date_from.timestamp() * 1000)
        t = int(date_to.timestamp() * 1000)
        self.calls.append((symbol, timeframe, f, t))
        return self.bars_by_range.get((f, t), [])

def _c(t):
    return Candle(time_msc=t, open=1, high=2, low=0.5, close=1.5,
                  tick_volume=1, spread=1, real_volume=1)

def test_fill_fetches_gap_inserts_and_records_coverage(tmp_path):
    conn = connect(tmp_path / "t.db")
    client = FakeRates({(BASE, BASE+3*M1): [_c(BASE+M1), _c(BASE+2*M1)]})
    n = fill_range(client, conn, "XAUUSDc", "M1", BASE, BASE+3*M1)
    assert n == 2
    assert [r["time_msc"] for r in cs.read_candles(conn, "XAUUSDc", "M1", BASE, BASE+3*M1)] == [BASE+M1, BASE+2*M1]
    assert cs.read_coverage(conn, "XAUUSDc", "M1") == [(BASE, BASE+3*M1)]

def test_fill_records_coverage_for_empty_range(tmp_path):
    conn = connect(tmp_path / "t.db")
    client = FakeRates({})  # market closed: no bars
    n = fill_range(client, conn, "XAUUSDc", "M1", BASE, BASE+3*M1)
    assert n == 0
    assert cs.read_coverage(conn, "XAUUSDc", "M1") == [(BASE, BASE+3*M1)]  # remembered as fetched

def test_fill_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    client = FakeRates({(BASE, BASE+3*M1): [_c(BASE+M1)]})
    fill_range(client, conn, "XAUUSDc", "M1", BASE, BASE+3*M1)
    client.calls.clear()
    n2 = fill_range(client, conn, "XAUUSDc", "M1", BASE, BASE+3*M1)  # already covered
    assert n2 == 0
    assert client.calls == []   # no gap → no bridge call


class ConcurrentWriter:
    """copy_rates_range that, on every call, tries to write from a SEPARATE
    connection — exactly what `journal serve`'s request_candles INSERT does while
    `journal live` is draining. If fill_range holds the write transaction open
    across a bridge fetch, this second connection is locked out (WAL = one
    writer), which is the reported `database is locked` 500."""
    def __init__(self, db_path, bars_by_range):
        self.db_path = db_path
        self.bars_by_range = bars_by_range
        self.lock_errors: list[str] = []

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        f = int(date_from.timestamp() * 1000)
        t = int(date_to.timestamp() * 1000)
        w = sqlite3.connect(str(self.db_path))
        w.execute("PRAGMA busy_timeout = 200")   # fail fast so the test is quick
        try:
            w.execute(
                "INSERT INTO candle_requests (symbol, timeframe, from_msc, to_msc, "
                "status, requested_msc) VALUES ('XAUUSDc', 'M1', ?, ?, 'pending', 0)",
                (f, t),
            )
            w.commit()
        except sqlite3.OperationalError as e:      # 'database is locked'
            self.lock_errors.append(str(e))
        finally:
            w.close()
        return self.bars_by_range.get((f, t), [])


def test_fill_does_not_hold_write_lock_across_bridge_fetches(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    # Pre-cover a middle slice so the fill has TWO disjoint gaps → two bridge
    # fetches. Under the bug, the first gap's write opens a transaction that stays
    # open across the SECOND gap's fetch, locking any other writer out.
    cs.record_coverage(conn, "XAUUSDc", "M1", BASE + M1, BASE + 2 * M1)
    conn.commit()
    client = ConcurrentWriter(db, {})
    fill_range(client, conn, "XAUUSDc", "M1", BASE, BASE + 3 * M1)
    assert client.lock_errors == []   # a concurrent enqueue must never be locked out
