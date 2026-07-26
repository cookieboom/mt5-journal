"""M3 chart renderer — cache identity, honest sub-bar rendering, and the two
0.0-vs-NULL guards (SL/TP hlines, R display) that mirror the exact Trap-6 shape
M2.1 already paid for on `r_multiple` (a not-None gate passing for a real 0.0).

Pure DB, no MT5 client (mirrors verify/rebuild) — every test builds its own tmp
DB with hand-inserted trade + candle rows.
"""

from __future__ import annotations

import pytest

from journal.render.chart import (
    PAD_BARS,
    NoCandlesError,
    RenderOpts,
    TradeNotFoundError,
    normalize_opts,
    render_trade,
    window_for,
)
from journal.store.db import connect

_LOGIN = 0
_OPEN_MSC = 1_700_000_000_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    c.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, 'USC', ?)",
        (_LOGIN, _OPEN_MSC),
    )
    c.commit()
    yield c
    c.close()


def _insert_trade(
    conn,
    position_id,
    *,
    open_msc=_OPEN_MSC,
    duration_s=373,
    direction="buy",
    sl_initial=None,
    tp_initial=None,
    net_profit=3.0,
    r_multiple=None,
    symbol="XAUUSDc",
    symbol_base="XAUUSD",
):
    close_msc = open_msc + duration_s * 1000
    conn.execute(
        """
        INSERT INTO trades
            (account_login, position_id, segment, symbol, symbol_base, direction,
             status, open_time_msc, close_time_msc, duration_s, volume, open_price,
             close_price, sl_initial, tp_initial, net_profit, r_multiple,
             deal_count, rebuilt_at)
        VALUES (?, ?, 0, ?, ?, ?, 'closed', ?, ?, ?, 0.1, 4035.0, 4038.0, ?, ?, ?, ?,
                2, ?)
        """,
        (_LOGIN, position_id, symbol, symbol_base, direction, open_msc, close_msc,
         duration_s, sl_initial, tp_initial, net_profit, r_multiple, open_msc),
    )
    conn.commit()
    return open_msc, close_msc


def _insert_candles(conn, symbol, tf, center_msc, tf_seconds, n_before=20, n_after=20):
    for i in range(-n_before, n_after):
        t = center_msc + i * tf_seconds * 1000
        conn.execute(
            "INSERT INTO candles (symbol, timeframe, time_msc, open, high, low, "
            "close, tick_volume) VALUES (?, ?, ?, 4035.0, 4035.2, 4034.8, 4035.1, 10)",
            (symbol, tf, t),
        )
    conn.commit()


def test_render_trade_writes_a_real_png(conn, tmp_path):
    _insert_trade(conn, 555)  # 373s median trade -> M1
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)

    r = render_trade(conn, 555, cache_dir=tmp_path / "cache")

    assert r.path.exists()
    assert r.path.stat().st_size > 0
    assert r.timeframe == "M1"
    assert r.n_trade_bars == 7  # 373s // 60 + 1, matches docs §7 median
    assert r.same_bar is False
    # stable cache key, not trades.id -- now opts-keyed (rule 6: different
    # opts must be different cache files), so the default RenderOpts()
    # signature is part of the expected name.
    assert r.path.name == f"{_LOGIN}-555-seg0-{RenderOpts().signature()}.png"


def test_render_trade_sub_bar_is_rendered_honestly(conn, tmp_path):
    # 1s trade, docs §7's measured minimum -- no TF can separate entry from exit.
    _insert_trade(conn, 556, duration_s=1)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)

    r = render_trade(conn, 556, cache_dir=tmp_path / "cache")

    assert r.same_bar is True
    assert r.n_trade_bars == 1
    assert "within one" in r.title
    assert "1s" in r.title


# --------------------------------------------------- SL/TP: value, not not-None


def test_sl_null_is_not_drawn(conn, tmp_path):
    _insert_trade(conn, 557, sl_initial=None, tp_initial=None)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)
    r = render_trade(conn, 557, cache_dir=tmp_path / "cache")
    assert r.sl_drawn is False
    assert r.tp_drawn is False


def test_sl_zero_is_not_drawn_the_m21_blind_spot(conn, tmp_path):
    # THE case this test exists for: sl_initial=0.0 is NOT None. A naive
    # `is not None` gate would draw an hline at price 0 on a ~4035 chart and
    # collapse the y-axis to an unreadable sliver -- the exact Trap 6 shape
    # M2.1 already fixed for r_multiple's ZeroDivisionError. 0.0 here simulates
    # what M4's poller will eventually write for a CONFIRMED no-SL trade.
    _insert_trade(conn, 558, sl_initial=0.0, tp_initial=0.0)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)
    r = render_trade(conn, 558, cache_dir=tmp_path / "cache")
    assert r.sl_drawn is False
    assert r.tp_drawn is False


def test_sl_real_value_is_drawn(conn, tmp_path):
    _insert_trade(conn, 559, sl_initial=4030.0, tp_initial=4040.0)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)
    r = render_trade(conn, 559, cache_dir=tmp_path / "cache")
    assert r.sl_drawn is True
    assert r.tp_drawn is True


# ------------------------------------------------------- R display: 0 != unknown


def test_r_none_shows_na(conn, tmp_path):
    _insert_trade(conn, 560, r_multiple=None)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)
    r = render_trade(conn, 560, cache_dir=tmp_path / "cache")
    assert "R n/a" in r.title


def test_r_zero_shows_0_00_not_na(conn, tmp_path):
    # A KNOWN R of exactly 0.0 (breakeven) must never read as "unknown" --
    # `0.0 or 'n/a'` is the bug shape this guards against (same family as #1/#2
    # above): NULL means unknown, 0 means a known value of zero (CLAUDE.md rule 4).
    _insert_trade(conn, 561, r_multiple=0.0)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)
    r = render_trade(conn, 561, cache_dir=tmp_path / "cache")
    assert "R 0.00" in r.title
    assert "R n/a" not in r.title


# --------------------------------------------------------------------- axis zone


def test_markers_land_on_correct_bar_regardless_of_server_offset(conn, tmp_path):
    # Trap 7: server_utc_offset_s must be READ, never hardcoded. A non-zero,
    # DST-like offset must not move WHICH bar the entry/exit markers land on --
    # trade times and candle times are both server time, so a constant shift
    # cancels out; only the axis LABEL (WIB) would change.
    conn.execute(
        "INSERT INTO sync_state (account_login, stream, last_synced_msc, "
        "server_utc_offset_s, measured_at) VALUES (?, 'deals', ?, 7200, ?)",
        (_LOGIN, _OPEN_MSC, _OPEN_MSC),
    )
    conn.commit()
    _insert_trade(conn, 562)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)

    r = render_trade(conn, 562, cache_dir=tmp_path / "cache")
    assert r.n_trade_bars == 7  # unchanged vs the offset=0 case
    assert r.same_bar is False
    assert r.path.exists()


# ------------------------------------------------------------------------ errors


def test_no_candles_raises_not_a_blank_png(conn, tmp_path):
    _insert_trade(conn, 563)
    # no candles inserted at all
    with pytest.raises(NoCandlesError, match="journal candles"):
        render_trade(conn, 563, cache_dir=tmp_path / "cache")


def test_missing_trade_raises(conn, tmp_path):
    with pytest.raises(TradeNotFoundError):
        render_trade(conn, 999999, cache_dir=tmp_path / "cache")


# ---------------------------------------------------------------- render opts


def test_normalize_opts_defaults_and_clamps():
    assert normalize_opts(None) == RenderOpts()          # all defaults
    o = normalize_opts({"theme": "bogus", "pad_bars": 999, "tf_override": "M5",
                        "show_sltp": False, "show_volume": True})
    assert o.theme == "charles"        # unknown theme falls back
    assert o.pad_bars == 120           # clamped to [5,120]
    assert o.tf_override == "M5"
    assert o.show_sltp is False and o.show_volume is True
    assert normalize_opts({"pad_bars": 1}).pad_bars == 5
    assert normalize_opts({"tf_override": "ZZ"}).tf_override is None

def test_render_opts_signature_stable_and_sensitive():
    a = RenderOpts()
    assert a.signature() == RenderOpts().signature()          # stable
    assert a.signature() != RenderOpts(pad_bars=30).signature()  # sensitive

def test_window_for_pad_bars_widens_window():
    narrow = window_for(1_000_000, 2_000_000, "M1", pad_bars=5)
    wide = window_for(1_000_000, 2_000_000, "M1", pad_bars=30)
    assert wide[0] < narrow[0] and wide[1] > narrow[1]
    # default keeps PAD_BARS behavior
    assert window_for(1_000_000, 2_000_000, "M1") == window_for(
        1_000_000, 2_000_000, "M1", pad_bars=PAD_BARS)


# ------------------------------------------------------------ render_trade(opts=)


def test_render_trade_opts_toggle_sltp_and_cache_key(conn, tmp_path):
    _insert_trade(conn, 570, sl_initial=4030.0, tp_initial=4040.0)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)

    on = render_trade(
        conn, 570, opts=RenderOpts(show_sltp=True), cache_dir=tmp_path / "cache"
    )
    off = render_trade(
        conn, 570, opts=RenderOpts(show_sltp=False), cache_dir=tmp_path / "cache"
    )

    assert on.sl_drawn is True and on.tp_drawn is True
    assert off.sl_drawn is False and off.tp_drawn is False
    assert on.path != off.path            # opts change -> different cache file
    assert on.path.exists() and off.path.exists()


def test_render_trade_pad_bars_changes_bar_count(conn, tmp_path):
    _insert_trade(conn, 571)
    _insert_candles(conn, "XAUUSDc", "M1", _OPEN_MSC, 60)

    narrow = render_trade(
        conn, 571, opts=RenderOpts(pad_bars=5), cache_dir=tmp_path / "cache"
    )
    wide = render_trade(
        conn, 571, opts=RenderOpts(pad_bars=15), cache_dir=tmp_path / "cache"
    )

    assert wide.n_bars >= narrow.n_bars   # more padding -> at least as many bars


# -------------------------------------------------------------------- integration


def test_render_real_fixture_trade_if_recorded(tmp_path):
    """Once a human has run `scripts/record_fixtures.py` (M3's live-touching
    step), this renders a REAL XAUUSDc trade through the full sync -> rebuild ->
    sync_candles -> render_trade pipeline under FakeMT5Client. Skipped while
    rates.json is still the M0 empty placeholder.

    `record_fixtures.py` records candles for exactly ONE representative trade per
    symbol (median-closest duration, not the shortest) -- FakeMT5Client's rates
    fixture is a single flat "SYMBOL:TF" -> bars list with no per-request window
    filtering, so only that ONE trade's own window lines up with the stored bars.
    Rather than duplicate the "median-closest" selection rule here (and go stale
    the moment that rule changes), this tries every closed XAUUSDc trade and
    accepts whichever one the recorded candles actually cover -- proving the real
    render path end-to-end without assuming which trade was chosen upstream.
    """
    from journal.adapter.fake import FakeMT5Client
    from journal.domain.reconstruct import rebuild
    from journal.ingest.candles import sync_candles
    from journal.ingest.deals import sync

    client = FakeMT5Client()  # default fixtures dir = tests/fixtures
    if client.copy_rates_range("XAUUSDc", "M1", None, None) == []:
        pytest.skip("tests/fixtures/rates.json not yet recorded (human step)")

    conn = connect(tmp_path / "journal.db")
    try:
        sync(client, conn)
        rebuild(conn)
        sync_candles(client, conn)

        rows = conn.execute(
            "SELECT position_id FROM trades WHERE symbol = 'XAUUSDc' "
            "AND status = 'closed' ORDER BY duration_s"
        ).fetchall()
        assert rows, "no closed XAUUSDc trade in the recorded fixtures"

        result = None
        errors = []
        for row in rows:
            try:
                result = render_trade(conn, row["position_id"], cache_dir=tmp_path / "cache")
                break
            except NoCandlesError as e:
                errors.append(str(e))

        assert result is not None, (
            "no XAUUSDc trade's render window matched the recorded candles: "
            + "; ".join(errors)
        )
        assert result.path.exists() and result.path.stat().st_size > 0
        print(
            f"\nintegration render: position_id={row['position_id']} "
            f"tf={result.timeframe} n_bars={result.n_bars} "
            f"n_trade_bars={result.n_trade_bars}"
        )
    finally:
        conn.close()
