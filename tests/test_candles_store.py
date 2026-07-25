import pytest
from journal.adapter.base import Candle
from journal.store.db import connect
from journal.store import candles_store as cs

M1 = 60_000
BASE = 1_700_000_000_000   # ~2023-11, a real epoch-ms; safely above the 1e12 floor

def _conn(tmp_path):
    return connect(tmp_path / "t.db")

def _c(t, o=1.0, h=2.0, l=0.5, c=1.5, v=3):
    return Candle(time_msc=t, open=o, high=h, low=l, close=c,
                  tick_volume=v, spread=1, real_volume=v)

def test_missing_ranges_full_when_no_coverage():
    assert cs.missing_ranges([], (0, 100)) == [(0, 100)]

def test_missing_ranges_subtracts_middle():
    assert cs.missing_ranges([(20, 40)], (0, 100)) == [(0, 19), (41, 100)]

def test_missing_ranges_empty_when_fully_covered():
    assert cs.missing_ranges([(0, 100)], (10, 90)) == []

def test_insert_and_read_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    assert cs.insert_candle(conn, "XAUUSDc", "M1", _c(BASE + 2*M1)) == 1
    assert cs.insert_candle(conn, "XAUUSDc", "M1", _c(BASE + 2*M1)) == 0  # PK dedupe
    rows = cs.read_candles(conn, "XAUUSDc", "M1", BASE, BASE + 3*M1)
    assert [r["time_msc"] for r in rows] == [BASE + 2*M1]

def test_insert_rejects_seconds_leak(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        cs.insert_candle(conn, "XAUUSDc", "M1", _c(1_700_000))  # seconds, < 1e12

def test_record_coverage_merges_touching(tmp_path):
    conn = _conn(tmp_path)
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 100)
    cs.record_coverage(conn, "XAUUSDc", "M1", 101, 200)   # touches (gap 1)
    cs.record_coverage(conn, "XAUUSDc", "M1", 500, 600)   # disjoint
    conn.commit()
    assert cs.read_coverage(conn, "XAUUSDc", "M1") == [(0, 200), (500, 600)]

def test_row_to_candle(tmp_path):
    conn = _conn(tmp_path)
    cs.insert_candle(conn, "XAUUSDc", "M1", _c(BASE + 2*M1, o=1.1))
    r = cs.read_candles(conn, "XAUUSDc", "M1", BASE, BASE + 3*M1)[0]
    c = cs.row_to_candle(r)
    assert c.time_msc == BASE + 2*M1 and c.open == 1.1

def test_load_bars_returns_native_rows(tmp_path):
    from journal.store.db import connect
    from journal.store import candles_store as cs
    from journal.adapter.base import Candle
    conn = connect(tmp_path / "j.db")
    try:
        c = Candle(time_msc=1_700_000_000_000, open=1, high=2, low=0.5, close=1.5,
                   tick_volume=3, spread=0, real_volume=0)
        cs.insert_candle(conn, "XAUUSDc", "M5", c)
        conn.commit()
        bars = cs.load_bars(conn, "XAUUSDc", "M5", 1_700_000_000_000, 1_700_000_000_000)
        assert len(bars) == 1 and bars[0].close == 1.5
    finally:
        conn.close()
