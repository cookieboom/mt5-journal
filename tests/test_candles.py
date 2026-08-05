"""M3 candle ingest — the TF ladder, the render window, and the Trap-15 tripwire.

`sync_candles` is exercised under `FakeMT5Client`, no bridge (CLAUDE.md rule 1).
`tests/fixtures/rates.json` is empty until a human runs `scripts/record_fixtures.py`
against the live bridge (see the M3 plan) — every test here builds its own
minimal trade + candle data instead of depending on that real recording.
"""

from __future__ import annotations

import json

import pytest

from journal.adapter.base import Candle
from journal.adapter.fake import FakeMT5Client
from journal.ingest.candles import sync_candles
from journal.render.chart import PAD_BARS, choose_timeframe, window_for
from journal.store import candles_store as cs
from journal.store.db import connect

_LOGIN = 0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _insert_trade(conn, position_id, open_msc, close_msc, duration_s, symbol="XAUUSDc"):
    conn.execute(
        """
        INSERT INTO accounts (login, currency, first_seen_at)
        VALUES (?, 'USC', ?)
        ON CONFLICT(login) DO NOTHING
        """,
        (_LOGIN, open_msc),
    )
    conn.execute(
        """
        INSERT INTO trades
            (account_login, position_id, segment, symbol, symbol_base, direction,
             status, open_time_msc, close_time_msc, duration_s, volume, open_price,
             close_price, net_profit, deal_count, rebuilt_at)
        VALUES (?, ?, 0, ?, ?, 'buy', 'closed', ?, ?, ?, 0.1, 4035.0, 4038.0, 3.0, 2, ?)
        """,
        (_LOGIN, position_id, symbol, symbol[:-1], open_msc, close_msc, duration_s, open_msc),
    )
    conn.commit()


def _write_rates(fx_dir, key, open_msc, n_before=20, n_after=20, tf_seconds=60):
    """Bars every `tf_seconds`, spanning n_before..n_after around `open_msc`.
    Stored as raw SECONDS per the fixture contract — FakeMT5Client applies the
    x1000 itself (Trap 15), mirroring the real bridge."""
    fx_dir.mkdir(parents=True, exist_ok=True)
    bars = []
    base_s = open_msc // 1000
    for i in range(-n_before, n_after):
        t = base_s + i * tf_seconds
        bars.append(
            {"time": t, "open": 4035.0, "high": 4035.2, "low": 4034.8,
             "close": 4035.1, "tick_volume": 10, "spread": 1, "real_volume": 0}
        )
    (fx_dir / "rates.json").write_text(json.dumps({key: bars}))


def _write_rates_multi(fx_dir, anchors, n_before=20, n_after=22, tf_seconds=60):
    """One rates.json holding several `SYMBOL:TF` keys at once.

    `anchors` maps a fixture key to the anchor ms the bars are centred on.
    `n_after=22` is deliberate: for an M1 trade of 373 s the render window ends
    at `open_msc + 1_273_000` (PAD_BARS=15), and the last bar written here opens
    at `open_msc + 1_260_000`, so `record_fetch` claims the window to its very
    end. A shorter tail leaves the window permanently unsealed under
    FakeMT5Client, which ignores the requested date range.
    """
    fx_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, anchor_msc in anchors.items():
        base_s = anchor_msc // 1000
        out[key] = [
            {"time": base_s + i * tf_seconds, "open": 4035.0, "high": 4035.2,
             "low": 4034.8, "close": 4035.1, "tick_volume": 10, "spread": 1,
             "real_volume": 0}
            for i in range(-n_before, n_after)
        ]
    (fx_dir / "rates.json").write_text(json.dumps(out))


class CountingClient(FakeMT5Client):
    """FakeMT5Client that records every bridge fetch, so a test can assert that
    an already-covered window costs ZERO round trips."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fetches: list[tuple[str, str]] = []

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        self.fetches.append((symbol, timeframe))
        return super().copy_rates_range(symbol, timeframe, date_from, date_to)


# --------------------------------------------------------------- choose_timeframe


@pytest.mark.parametrize(
    "duration_s,expected",
    [
        (1, "M1"),                    # min measured (docs §7)
        (373, "M1"),                  # median 6m13s
        (1218, "M1"),                 # p75 20m18s
        (3600, "M1"),                 # exactly 60 M1 bars -- the ladder's own edge
        (3601, "M5"),                 # one second past the M1 edge -> escalate
        (300 * 60, "M5"),             # M5 edge (60 M5 bars = 5h)
        (300 * 60 + 1, "M15"),
        (41100, "M15"),                # max measured, 11h25m
        (900 * 60 + 1, "H1"),
        (3600 * 60 + 1, "H4"),
        (14400 * 60 + 1, "D1"),
        (10**8, "D1"),                 # far beyond any bar -- floor is D1
    ],
)
def test_choose_timeframe_ladder(duration_s, expected):
    assert choose_timeframe(duration_s) == expected


def test_window_for_pads_symmetrically():
    open_msc, close_msc = 1_700_000_000_000, 1_700_000_373_000
    from_msc, to_msc = window_for(open_msc, close_msc, "M1")
    pad_ms = PAD_BARS * 60 * 1000
    assert from_msc == open_msc - pad_ms
    assert to_msc == close_msc + pad_ms


# ------------------------------------------------------------------- sync_candles


def test_sync_candles_writes_ms_not_seconds(conn, tmp_path):
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=555, open_msc=open_msc, close_msc=close_msc,
                   duration_s=373)

    fx = tmp_path / "fixtures"
    _write_rates(fx, "XAUUSDc:M1", open_msc)
    client = FakeMT5Client(fixtures_dir=fx)

    r = sync_candles(client, conn)
    assert r.trades_seen == 1
    assert r.trades_skipped_open == 0
    assert r.bars_new == 40
    assert r.symbols == ["XAUUSDc"]

    rows = conn.execute("SELECT time_msc FROM candles").fetchall()
    assert len(rows) == 40
    assert all(row["time_msc"] >= 10**12 for row in rows)  # ms, not seconds


def test_sync_candles_is_idempotent(conn, tmp_path):
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=555, open_msc=open_msc, close_msc=close_msc,
                   duration_s=373)
    fx = tmp_path / "fixtures"
    _write_rates(fx, "XAUUSDc:M1", open_msc)
    client = FakeMT5Client(fixtures_dir=fx)

    r1 = sync_candles(client, conn)
    r2 = sync_candles(client, conn)
    assert r1.bars_new == 40
    assert r2.bars_new == 0  # PK-deduped, nothing new the second time
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 40


def test_sync_candles_skips_open_trades(conn, tmp_path):
    conn.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, 'USC', 1)",
        (_LOGIN,),
    )
    conn.execute(
        """
        INSERT INTO trades
            (account_login, position_id, segment, symbol, symbol_base, direction,
             status, open_time_msc, volume, open_price, net_profit, deal_count,
             rebuilt_at)
        VALUES (?, 999, 0, 'XAUUSDc', 'XAUUSD', 'buy', 'open', 1700000000000, 0.1,
                4035.0, 0, 1, 1700000000000)
        """,
        (_LOGIN,),
    )
    conn.commit()
    client = FakeMT5Client(fixtures_dir=tmp_path / "empty")
    r = sync_candles(client, conn)
    assert r.trades_seen == 0
    assert r.trades_skipped_open == 1


def test_sync_candles_refetches_nothing_once_covered(conn, tmp_path):
    """The five-minute stall: sync_candles wrote coverage but never read it, so
    every position close re-fetched all 123 closed-trade windows from the bridge.
    Once a window is covered it must cost zero round trips."""
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=555, open_msc=open_msc, close_msc=close_msc,
                  duration_s=373)
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, {"XAUUSDc:M1": open_msc})
    client = CountingClient(fixtures_dir=fx)

    first = sync_candles(client, conn)
    assert first.windows_fetched == 1
    assert len(client.fetches) == 1

    client.fetches.clear()
    second = sync_candles(client, conn)
    assert client.fetches == []          # the whole point
    assert second.windows_fetched == 0
    assert second.bars_new == 0
    assert second.trades_seen == 1       # still processed, just for free


# --------------------------------------------------- Trap 15 regression tripwire


class BadTimeClient(FakeMT5Client):
    """Simulates an adapter bug that leaks SECONDS into `Candle.time_msc` (Trap
    15) -- the failure `sync_candles`'s magnitude guard exists to catch. The real
    x1000 conversion lives in live.py/fake.py and is not under test here; this
    stands in for "what if that boundary broke"."""

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        return [
            Candle(time_msc=1752624000, open=1, high=1, low=1, close=1, tick_volume=1)
        ]


def test_sync_candles_rejects_seconds_leaked_as_msc(conn, tmp_path):
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=555, open_msc=open_msc, close_msc=close_msc,
                   duration_s=373)

    with pytest.raises(ValueError, match="Trap 15"):
        sync_candles(BadTimeClient(), conn)


# --------------------------------------------------- record_coverage


def test_record_coverage_ignores_a_reversed_range(tmp_path):
    """from_ms > to_ms is nonsense; it must not enter the coverage set (mirror
    missing_ranges' lo>hi guard). A reversed call is a no-op."""
    conn = connect(tmp_path / "j.db")
    try:
        cs.record_coverage(conn, "XAUUSDc", "M5", 2000, 1000)  # reversed
        assert cs.read_coverage(conn, "XAUUSDc", "M5") == []
        # a valid range still records normally
        cs.record_coverage(conn, "XAUUSDc", "M5", 1000, 2000)
        assert cs.read_coverage(conn, "XAUUSDc", "M5") == [(1000, 2000)]
    finally:
        conn.close()


def test_sync_candles_populates_coverage(conn, tmp_path):
    """Cross-producer contract: the legacy per-trade ingest path must record
    candle_coverage too, so the store can tell 'fetched, empty' from 'never
    fetched' regardless of which producer filled it. Previously only true by
    inspection (candles.py:77 calls record_coverage)."""
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=555, open_msc=open_msc, close_msc=close_msc,
                   duration_s=373)
    fx = tmp_path / "fixtures"
    _write_rates(fx, "XAUUSDc:M1", open_msc)
    client = FakeMT5Client(fixtures_dir=fx)

    report = sync_candles(client, conn)
    assert report.trades_seen == 1
    for sym in report.symbols:
        # every symbol a window was fetched for now has coverage recorded
        has_any = any(
            cs.read_coverage(conn, sym, tf)
            for tf in ("M1", "M5", "M15", "H1", "H4", "D1")
        )
        assert has_any, f"no coverage recorded for {sym}"


# ------------------------------------------------------------- candles_payload


def test_candles_payload_truncates_to_max_bars(tmp_path):
    """When more native bars are cached than max_bars, the payload returns the
    LAST max_bars (most recent), never the head. (The bucket-boundary
    aggregation bug is already fixed in Phase A — do not re-test it here.)"""
    from journal.web import api

    db_conn = connect(tmp_path / "j.db")
    try:
        base = 1_700_000_000_000
        step = 5 * 60_000
        n = 12
        for i in range(n):
            t = base + i * step
            cs.insert_candle(
                db_conn, "XAUUSDc", "M5",
                Candle(time_msc=t, open=1.0, high=2.0, low=0.5, close=1.5, tick_volume=10),
            )
        cs.record_coverage(db_conn, "XAUUSDc", "M5", base, base + (n - 1) * step)
        db_conn.commit()

        out = api.candles_payload(db_conn, "XAUUSDc", "M5", base, base + (n - 1) * step, max_bars=5)
        assert len(out["candles"]) == 5
        # kept the most recent 5 (tail), so first kept bar is the 8th (index 7)
        assert out["candles"][0]["time_msc"] == base + 7 * step
        assert out["candles"][-1]["time_msc"] == base + (n - 1) * step
    finally:
        db_conn.close()


def test_sync_candles_does_not_claim_coverage_past_the_bars_it_got(conn, tmp_path):
    """A render window runs to close + PAD_BARS, so a trade that closed moments
    ago is asked for bars that do not exist yet. The bridge answers with what it
    has and stops; claiming the whole window regardless sealed those minutes as
    fetched forever (real hole 2026-08-05: trade closed 21:34, sync ran 21:43,
    and 21:44-21:49 were never fetchable again while the data-health panel read
    100%). Claim only up to the last bar that actually came back."""
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=556, open_msc=open_msc, close_msc=close_msc,
                  duration_s=373)
    fx = tmp_path / "fixtures"
    _write_rates(fx, "XAUUSDc:M1", open_msc)        # bars stop 20 min after open
    sync_candles(FakeMT5Client(fixtures_dir=fx), conn)

    _, to_msc = window_for(open_msc, close_msc, "M1")
    last_bar = open_msc + 19 * 60_000               # _write_rates' final bar
    ((_, hi),) = cs.read_coverage(conn, "XAUUSDc", "M1")
    assert hi == last_bar + 60_000 - 1
    assert hi < to_msc                              # the unfetched tail stays on offer


def test_sync_candles_caps_fetches_and_serves_newest_first(conn, tmp_path):
    """Backlog protection. With 12 uncovered windows and a cap of 5, one run may
    hit the bridge 5 times and no more — a restart after `journal live` was down
    for days must not turn a position close into a multi-minute stall. The 5 it
    picks are the most recent closes, so the trade that just closed always gets
    its candles (and therefore its MAE/MFE) in the same run."""
    from journal.ingest import candles as candles_mod

    base = 1_700_000_000_000
    anchors = {}
    for i in range(12):
        open_msc = base + i * 86_400_000          # one trade per day, no overlap
        _insert_trade(conn, position_id=600 + i, open_msc=open_msc,
                      close_msc=open_msc + 373_000, duration_s=373,
                      symbol=f"T{i:02d}c")
        anchors[f"T{i:02d}c:M1"] = open_msc
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, anchors)
    client = CountingClient(fixtures_dir=fx)

    r = sync_candles(client, conn, max_windows=5)

    assert r.windows_fetched == 5
    assert len(client.fetches) == 5
    assert r.windows_pending == 7
    # newest close first: T11..T07
    assert sorted(sym for sym, _ in client.fetches) == [f"T{i:02d}c" for i in range(7, 12)]
    assert candles_mod._MAX_FETCH_WINDOWS == 5      # the default the live loop uses


def test_sync_candles_uncapped_run_fetches_the_whole_backlog(conn, tmp_path):
    """`max_windows=None` is what `journal candles` passes to prime a backlog in
    one deliberate, foreground run — nothing asserted this before. All 12
    uncovered windows must be fetched in a single call, with none deferred."""
    base = 1_700_000_000_000
    anchors = {}
    for i in range(12):
        open_msc = base + i * 86_400_000
        _insert_trade(conn, position_id=800 + i, open_msc=open_msc,
                      close_msc=open_msc + 373_000, duration_s=373,
                      symbol=f"V{i:02d}c")
        anchors[f"V{i:02d}c:M1"] = open_msc
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, anchors)
    client = CountingClient(fixtures_dir=fx)

    r = sync_candles(client, conn, max_windows=None)

    assert r.windows_fetched == 12
    assert r.windows_pending == 0
    assert len(client.fetches) == 12


def test_sync_candles_backlog_drains_without_refetching(conn, tmp_path):
    """Repeated capped runs finish the backlog, and no window is ever fetched
    twice. 12 windows at a cap of 5 = 3 runs, 12 fetches total."""
    base = 1_700_000_000_000
    anchors = {}
    for i in range(12):
        open_msc = base + i * 86_400_000
        _insert_trade(conn, position_id=700 + i, open_msc=open_msc,
                      close_msc=open_msc + 373_000, duration_s=373,
                      symbol=f"U{i:02d}c")
        anchors[f"U{i:02d}c:M1"] = open_msc
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, anchors)
    client = CountingClient(fixtures_dir=fx)

    runs = 0
    while True:
        r = sync_candles(client, conn, max_windows=5)
        runs += 1
        if r.windows_pending == 0 and r.windows_fetched == 0:
            break
        assert runs < 10, "backlog is not draining — a window is being re-offered"

    assert runs == 4                      # 5 + 5 + 2 fetched, then one clean pass
    assert len(client.fetches) == 12      # every window fetched exactly once
    assert len(set(client.fetches)) == 12
