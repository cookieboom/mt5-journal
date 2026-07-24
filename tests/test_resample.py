# tests/test_resample.py
import pytest
from journal.adapter.base import Candle
from journal.domain.resample import bucket_start, resample_m1

M1 = 60_000

def _c(t, o, h, l, c, v=1):
    return Candle(time_msc=t, open=o, high=h, low=l, close=c,
                  tick_volume=v, spread=0, real_volume=v)

def test_bucket_start_aligns_to_server_time_utc():
    # 1970-01-01 00:00 UTC is epoch 0, so D1 buckets align to UTC midnight.
    assert bucket_start(0, "D1") == 0
    assert bucket_start(86_400_000 + 5, "D1") == 86_400_000
    assert bucket_start(300_500, "M5") == 300_000  # 5-min bucket

def test_bucket_start_rejects_unknown_timeframe():
    with pytest.raises(ValueError):
        bucket_start(0, "M3")

def test_resample_m1_to_m5_ohlc():
    bars = [_c(0, 10, 12, 9, 11), _c(M1, 11, 15, 8, 14),
            _c(2*M1, 14, 14, 13, 13), _c(3*M1, 13, 13, 10, 12),
            _c(4*M1, 12, 20, 12, 19)]
    out = resample_m1(bars, "M5")
    assert len(out) == 1
    b = out[0]
    assert b.time_msc == 0
    assert (b.open, b.high, b.low, b.close) == (10, 20, 8, 19)
    assert b.tick_volume == 5

def test_resample_m1_splits_across_buckets():
    bars = [_c(0, 1, 1, 1, 1), _c(5*M1, 2, 2, 2, 2)]
    out = resample_m1(bars, "M5")
    assert [b.time_msc for b in out] == [0, 300_000]

def test_resample_guard_omits_partially_covered_bucket():
    # Only the first 3 of 5 M1 bars in the 0..5m bucket are covered → omit it.
    bars = [_c(0, 1, 1, 1, 1), _c(M1, 1, 1, 1, 1), _c(2*M1, 1, 1, 1, 1)]
    covered = [(0, 2*M1)]  # covers opens 0,60000,120000 — not 180000/240000
    out = resample_m1(bars, "M5", covered=covered)
    assert out == []

def test_resample_guard_emits_fully_covered_bucket():
    bars = [_c(i*M1, 1, 1, 1, 1) for i in range(5)]
    covered = [(0, 4*M1)]  # covers every M1 open in the 0..5m bucket
    out = resample_m1(bars, "M5", covered=covered)
    assert len(out) == 1 and out[0].time_msc == 0

def test_resample_preserves_genuine_zero_volume():
    # Hard Rule 4: a computed sum of 0 is a KNOWN zero, not unknown/None.
    # This account's symbols routinely report real_volume=0.
    bars = [Candle(time_msc=i*M1, open=1, high=1, low=1, close=1,
                   tick_volume=0, spread=0, real_volume=0) for i in range(5)]
    out = resample_m1(bars, "M5")
    assert len(out) == 1
    assert out[0].tick_volume == 0
    assert out[0].real_volume == 0
