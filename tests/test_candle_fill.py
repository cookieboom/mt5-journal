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
