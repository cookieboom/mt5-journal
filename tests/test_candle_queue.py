from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candle_queue as q
from journal.store import candles_store as cs
from journal.ingest.candle_fill import fulfill_request

M1 = 60_000
BASE = 1_700_000_000_000

class FakeRates:
    def __init__(self, bars): self.bars = bars
    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        return self.bars

def _c(t):
    return Candle(time_msc=t, open=1, high=2, low=0.5, close=1.5,
                  tick_volume=1, spread=1, real_volume=1)

def test_request_dedupes_identical_pending(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    b = q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    assert a == b and a > 0

def test_request_returns_zero_when_already_covered(tmp_path):
    conn = connect(tmp_path / "t.db")
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 3*M1); conn.commit()
    assert q.request_candles(conn, "XAUUSDc", "M1", M1, 2*M1) == 0

def test_claim_marks_claimed_once(tmp_path):
    conn = connect(tmp_path / "t.db")
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    r1 = q.claim_next_request(conn)
    assert r1 is not None and r1["status"] == "claimed"
    assert q.claim_next_request(conn) is None   # nothing left pending

def test_fulfill_fills_and_marks_done(tmp_path):
    conn = connect(tmp_path / "t.db")
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    req = q.claim_next_request(conn)
    bars = fulfill_request(FakeRates([_c(BASE + M1)]), conn, req)
    assert bars == 1
    row = conn.execute("SELECT status, bars_written FROM candle_requests WHERE id=?", (req["id"],)).fetchone()
    assert row["status"] == "done" and row["bars_written"] == 1

def test_requeue_orphaned_resets_claimed(tmp_path):
    conn = connect(tmp_path / "t.db")
    q.request_candles(conn, "XAUUSDc", "M1", 0, 3*M1)
    q.claim_next_request(conn)
    assert q.requeue_orphaned(conn) == 1
    assert q.claim_next_request(conn) is not None   # pending again
