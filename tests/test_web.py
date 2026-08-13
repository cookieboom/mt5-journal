"""M7 web dashboard — pure formatters (`web/format.py`) and the DB→context
builders (`web/views.py`). Tested without an HTTP layer (no httpx/TestClient
dependency): the builders are deliberately separated from the FastAPI routes so
they can be exercised against a seeded DB, exactly as the analytics tests call
`build_report`/`build_weekly` directly.

The disciplines under test are the ones a UI can silently violate: money always
carries its currency and NEVER reads as a bare 0 when unknown (rule 4 / Trap 13),
§9 gating (n<20) is surfaced honestly, and the builders agree with the analytics
functions they wrap (a dashboard that disagrees with `journal report` is a bug).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from journal import execute
from journal.analytics.report import build_report
from journal.store import live_store
from journal.store.db import connect, now_ms
from journal.web import format as fmt
from journal.web import views

_LOGIN = 0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _seed_account(conn, currency="USC", balance=None):
    conn.execute(
        "INSERT INTO accounts (login, currency, balance, first_seen_at) "
        "VALUES (?, ?, ?, 1)",
        (_LOGIN, currency, balance),
    )
    conn.commit()


def _ms(hour: int, day: int = 15) -> int:
    dt = datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _seed_trade(
    conn, position_id, *, status="closed", net_profit=0.0, r_multiple=None,
    symbol="XAUUSDc", direction="buy", open_time_msc=None, close_time_msc=None,
    magic=None, sl_initial=None,
):
    open_time_msc = _ms(9) if open_time_msc is None else open_time_msc
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, volume, open_price, "
        "close_price, sl_initial, net_profit, r_multiple, magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.1, 4000.0, 4001.0, ?, ?, ?, ?, 2, 1)",
        (_LOGIN, position_id, symbol, symbol[:-1], direction, status,
         open_time_msc, close_time_msc, sl_initial, net_profit, r_multiple, magic),
    )
    conn.commit()


# --------------------------------------------------------------- formatters


def test_money_carries_currency_and_never_bare_dollar():
    assert fmt.money(1250.0, "USC") == "1,250.00 USC"
    assert fmt.money(-3.75, "USC", sign=True) == "-3.75 USC"
    assert "$" not in fmt.money(9.92, "USC")


def test_money_none_is_na_not_zero():
    # rule 4: unknown must never render as a real 0.
    assert fmt.money(None, "USC") == "n/a"
    assert fmt.money(0.0, "USC") == "0.00 USC"  # a genuine zero still shows


def test_pct_rmult_num_none():
    assert fmt.pct(0.347) == "34.7%"
    assert fmt.pct(None) == "n/a"
    assert fmt.rmult(1.35) == "1.35R"
    assert fmt.rmult(None) == "n/a"
    assert fmt.num(1.41) == "1.41"
    assert fmt.num(None) == "n/a"


def test_gated_below_20_explains_itself():
    # §9: a withheld average says WHY, with its n — never a silent blank/0.
    assert fmt.gated(6, None) == "n/a (n=6, perlu ≥20)"
    assert fmt.gated(25, 1.2, unit="R") == "1.20R  (n=25)"
    assert fmt.is_gated(6, None) is True
    assert fmt.is_gated(25, 1.2) is False


def test_price_unknown_vs_zero():
    # rule 4 again, on SL/TP: NULL=unknown, 0.0=confirmed none.
    assert fmt.price(None) == "unknown"
    assert fmt.price(0.0) == "0"
    assert fmt.price(3987.5) == "3987.5"


def test_level_word_on_a_modify_is_leave_not_unknown():
    # A modify carries INTENT about a level, so a blank field is a deliberate
    # "leave it", not ignorance — it must NOT read "unknown" (the audit log did).
    assert fmt.level_word(None) == "(tetap)"     # leave unchanged
    assert fmt.level_word(0.0) == "(hapus)"      # clear it
    assert fmt.level_word(4085.0) == "4085"      # set it
    assert fmt.level_word(None) != fmt.price(None)  # the whole point of the fix


def test_wib_converts_and_handles_none():
    # 2026-01-15 02:00 UTC + WIB(UTC+7) = 09:00 WIB, offset 0.
    ms = _ms(2)
    assert fmt.wib(ms, 0) == "2026-01-15 09:00 WIB"
    assert fmt.wib(None) == "—"


def test_dur_human():
    assert fmt.dur(45) == "45s"
    assert fmt.dur(90) == "1m30s"
    assert fmt.dur(3720) == "1h02m"
    assert fmt.dur(None) == "—"


# --------------------------------------------------------------- builders


def test_dashboard_context_agrees_with_build_report(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=10.0)
    _seed_trade(conn, 2, net_profit=-4.0)
    ctx = views.dashboard_context(conn)
    direct = build_report(conn)
    # The dashboard must show exactly what `journal report` computes.
    assert ctx["report"] == direct
    assert direct.currency == "USC"
    assert direct.n_closed == 2


def test_report_context_agrees_with_build_report_and_carries_by_symbol(conn):
    # /report shows exactly what `journal report` computes, including the M8
    # per-symbol breakdown the dashboard cards summarise.
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=10.0, symbol="XAUUSDc")
    _seed_trade(conn, 2, net_profit=-4.0, symbol="BTCUSDc")
    ctx = views.report_context(conn)
    direct = build_report(conn)
    assert ctx["report"] == direct
    # data-driven buckets: exactly the symbol_base actually traded, ascending.
    assert tuple(b.label for b in ctx["report"].by_symbol) == ("BTCUSD", "XAUUSD")


def test_dashboard_and_report_read_the_same_report_object(conn):
    # The trim (deep tables → /report) must not desync the two pages: both read
    # one build_report, so the dashboard cards and the /report tables agree.
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=7.0)
    assert views.dashboard_context(conn)["report"] == views.report_context(conn)["report"]


def test_account_header(conn):
    _seed_account(conn, currency="USC")
    h = views.account_header(conn)
    assert h["login"] == _LOGIN
    assert h["currency"] == "USC"
    assert h["offset_s"] == 0  # nothing measured on a fresh DB


def test_trades_context_source_filter(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, magic=0)          # discretionary
    _seed_trade(conn, 2, magic=None)       # discretionary (NULL)
    _seed_trade(conn, 3, magic=12345)      # EA
    ea = views.trades_context(conn, source="ea")
    disc = views.trades_context(conn, source="disc")
    assert {t["position_id"] for t in ea["trades"]} == {3}
    assert {t["position_id"] for t in disc["trades"]} == {1, 2}


def test_trades_context_symbol_and_status_filter(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, symbol="XAUUSDc", status="closed")
    _seed_trade(conn, 2, symbol="BTCUSDc", status="open")
    only_btc = views.trades_context(conn, symbol="BTCUSD")
    only_open = views.trades_context(conn, status="open")
    assert {t["position_id"] for t in only_btc["trades"]} == {2}
    assert {t["position_id"] for t in only_open["trades"]} == {2}
    assert set(views.trades_context(conn)["symbols"]) == {"XAUUSD", "BTCUSD"}


def test_trade_detail_context_missing_is_none(conn):
    _seed_account(conn)
    _seed_trade(conn, 1)
    assert views.trade_detail_context(conn, 999) is None
    ctx = views.trade_detail_context(conn, 1)
    assert ctx is not None
    assert ctx["trade"]["position_id"] == 1
    assert ctx["chartable"] is False  # no close_time_msc seeded


def test_trade_detail_chartable_requires_close(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", close_time_msc=_ms(10))
    ctx = views.trade_detail_context(conn, 1)
    assert ctx["chartable"] is True


def test_weekly_context_attributes_by_close(conn):
    _seed_account(conn)
    # closed on 2026-01-15 (a Thursday) → ISO 2026-W03.
    _seed_trade(conn, 1, status="closed", close_time_msc=_ms(10, day=15), net_profit=5.0)
    ctx = views.weekly_context(conn, 2026, 3)
    assert ctx["result"].n_closed == 1
    assert ctx["result"].net_total == pytest.approx(5.0)
    assert (2026, 3) in ctx["weeks"]


# --------------------------------------------------------------- live (M9)


def _seed_spec(
    conn, symbol="XAUUSDc", *, trade_mode=4,
    volume_min=0.01, volume_max=100.0, volume_step=0.01,
    tick_size=0.001, tick_value=0.1,
):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, fetched_at, "
        "volume_min, volume_max, volume_step, trade_mode, tick_size, tick_value) "
        "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
        (symbol, symbol[:-1], volume_min, volume_max, volume_step, trade_mode,
         tick_size, tick_value),
    )
    conn.commit()


def _seed_position(
    conn, position_id, *, symbol="XAUUSDc", direction="buy", volume=0.10,
    open_price=4000.0, price_current=4010.0, sl=None, tp=None, profit=0.0,
    observed_msc=None, open_time_msc=None,
):
    observed_msc = now_ms() if observed_msc is None else observed_msc
    open_time_msc = _ms(9) if open_time_msc is None else open_time_msc
    conn.execute(
        "INSERT INTO open_positions (account_login, position_id, symbol, symbol_base, "
        "direction, volume, open_price, price_current, sl, tp, profit, swap, magic, "
        "open_time_msc, observed_msc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
        (_LOGIN, position_id, symbol, symbol[:-1], direction, volume, open_price,
         price_current, sl, tp, profit, open_time_msc, observed_msc),
    )
    conn.commit()


def _seed_command(
    conn, *, position_id=1, kind="close", status="pending",
    retcode=None, error=None, sl=None, tp=None, volume=None,
    symbol=None, direction=None, price_ref=None,
):
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, symbol, "
        "direction, price_ref, sl, tp, volume, requested_msc, status, retcode, "
        "error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_LOGIN, position_id, kind, symbol, direction, price_ref, sl, tp, volume,
         now_ms(), status, retcode, error),
    )
    conn.commit()


def test_live_context_sums_floating_and_is_fresh(conn):
    _seed_account(conn)
    _seed_position(conn, 1, profit=12.0, observed_msc=now_ms())
    _seed_position(conn, 2, profit=-5.0, observed_msc=now_ms())
    ctx = views.live_context(conn)
    assert {p["position_id"] for p in ctx["positions"]} == {1, 2}
    # total floating is the plain sum of `profit` (labelled floating by template).
    assert ctx["total_floating"] == pytest.approx(7.0)
    assert ctx["empty"] is False
    assert ctx["stale"] is False


def test_live_context_empty_states_the_ambiguity(conn):
    _seed_account(conn)
    ctx = views.live_context(conn)
    assert ctx["empty"] is True
    assert ctx["positions"] == []
    # No snapshot exists, so there is nothing to be 'stale'; the age is unknown.
    assert ctx["stale"] is False
    assert ctx["age_s"] is None


def test_live_context_stale_when_snapshot_is_old(conn):
    _seed_account(conn)
    _seed_position(conn, 1, observed_msc=now_ms() - 60_000)
    ctx = views.live_context(conn)
    assert ctx["stale"] is True
    assert ctx["age_s"] >= 60


def test_preview_command_close_returns_intent(conn):
    _seed_account(conn)
    _seed_spec(conn)
    _seed_position(conn, 123456, volume=0.10)
    p = views.preview_command(conn, _LOGIN, 123456, "close", sl=None, tp=None, volume=None)
    assert "123456" in p["intent"]
    assert p["kind"] == "close"


def test_preview_command_modify_intent_mentions_levels(conn):
    _seed_account(conn)
    _seed_spec(conn)
    _seed_position(conn, 1, direction="buy", price_current=4010.0)
    p = views.preview_command(conn, _LOGIN, 1, "modify_sltp", sl=2000.5, tp=None, volume=None)
    assert "2000.5" in p["intent"]
    assert "(tetap)" in p["intent"]  # TP left unchanged (None), rule 4


def test_preview_command_over_max_lot_raises(conn):
    _seed_account(conn)
    _seed_spec(conn)
    _seed_position(conn, 1)
    with pytest.raises(execute.CommandError):
        views.preview_command(conn, _LOGIN, 1, "add_volume", sl=None, tp=None, volume=5.0)


def test_preview_command_unknown_position_raises(conn):
    _seed_account(conn)
    with pytest.raises(execute.CommandError):
        views.preview_command(conn, _LOGIN, 999, "close", sl=None, tp=None, volume=None)


def test_enqueue_inserts_exactly_one_pending_row_no_bridge(conn):
    # The write side the route uses: exactly one 'pending' row, nothing 'sent'
    # (there is no bridge in this process to send it).
    _seed_account(conn)
    _seed_spec(conn)
    _seed_position(conn, 1, volume=0.10)
    execute.enqueue(conn, _LOGIN, "close", 1)
    n_pending = conn.execute(
        "SELECT count(*) FROM trade_commands WHERE status='pending'"
    ).fetchone()[0]
    assert n_pending == 1
    n_sent = conn.execute(
        "SELECT count(*) FROM trade_commands WHERE status='sent'"
    ).fetchone()[0]
    assert n_sent == 0


def test_opt_float_preserves_rule4_distinction():
    # "" ≠ "0": empty means leave unchanged (None); an explicit 0 means clear it.
    assert views._opt_float("") is None
    assert views._opt_float("   ") is None
    assert views._opt_float("0") == 0.0
    assert views._opt_float("0.0") == 0.0
    assert views._opt_float("2000.5") == 2000.5


def test_commands_context_maps_retcode_name_and_shows_error(conn):
    _seed_account(conn)
    _seed_command(conn, position_id=1, kind="close", status="done", retcode=10009)
    _seed_command(conn, position_id=2, kind="close", status="failed",
                  error="proses berhenti di tengah perintah")
    _seed_command(conn, position_id=3, kind="close", status="failed", retcode=99999)
    ctx = views.commands_context(conn)
    by_pos = {c["position_id"]: c for c in ctx["commands"]}
    assert by_pos[1]["retcode_name"] == "DONE"          # name, not the int 10009
    assert by_pos[2]["error"] == "proses berhenti di tengah perintah"
    assert by_pos[2]["retcode_name"] is None            # nothing said yet
    assert by_pos[3]["retcode_name"] == "retcode 99999"  # honest fallback


def test_commands_context_carries_symbol_direction_price_ref_for_open(conn):
    # An "open" has no position row yet, so its symbol/direction/side-entry
    # price live only on the command — the audit log must not drop them
    # (Finding 2: a UI reading only position_id renders "#null" for these).
    _seed_account(conn)
    _seed_command(conn, position_id=None, kind="open", status="pending",
                  symbol="XAUUSDc", direction="buy", price_ref=4035.5,
                  sl=4030.0, volume=0.1)
    ctx = views.commands_context(conn)
    row = ctx["commands"][0]
    assert row["position_id"] is None
    assert row["symbol"] == "XAUUSDc"
    assert row["direction"] == "buy"
    assert row["price_ref"] == pytest.approx(4035.5)


# --------------------------------------------------- equity / R tape (M9)


def test_equity_curve_cumulative_last_equals_sum_and_r_sums_known(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", close_time_msc=_ms(10, day=15), net_profit=5.0, r_multiple=1.2)
    _seed_trade(conn, 2, status="closed", close_time_msc=_ms(11, day=16), net_profit=-2.0)  # r unknown
    _seed_trade(conn, 3, status="closed", close_time_msc=_ms(12, day=17), net_profit=3.0, r_multiple=-0.5)
    eq = views.equity_curve(conn)
    assert eq["n"] == 3
    # monotonic in count: one point per closed trade, ordered by close time
    assert len(eq["series"]) == 3
    times = [p["close_time_msc"] for p in eq["series"]]
    assert times == sorted(times)
    # last equity == sum(net_profit) over ALL closed trades
    assert eq["equity_last"] == pytest.approx(6.0)
    assert eq["series"][-1]["equity"] == pytest.approx(6.0)
    # cumulative equity is a running sum, not per-trade
    assert [p["equity"] for p in eq["series"]] == pytest.approx([5.0, 3.0, 6.0])
    # R curve sums ONLY the known r_multiple (1.2 + -0.5), over exactly 2 trades
    assert eq["n_with_r"] == 2
    assert eq["r_last"] == pytest.approx(0.7)
    assert eq["equity_svg"]["empty"] is False and eq["r_svg"]["empty"] is False


def test_equity_curve_series_names_the_trade_it_came_from(conn):
    # The dashboard strip reads this series; without an identity every row is a
    # dead end (a time and a running total name no trade). `position_id` is the
    # detail route's key — trades.id is never a URL (chart cache identity, M3).
    _seed_account(conn)
    _seed_trade(conn, 7, status="closed", close_time_msc=_ms(10, day=15),
                net_profit=5.0, symbol="XAUUSDc")
    _seed_trade(conn, 8, status="closed", close_time_msc=_ms(11, day=16),
                net_profit=-2.0, symbol="BTCUSDc")
    eq = views.equity_curve(conn)
    assert [p["position_id"] for p in eq["series"]] == [7, 8]
    # grouped identity, not the raw broker string (rule 11)
    assert [p["symbol_base"] for p in eq["series"]] == ["XAUUSD", "BTCUSD"]


def test_equity_curve_zero_trades_is_safe(conn):
    _seed_account(conn)  # account but no trades — must not divide by zero
    eq = views.equity_curve(conn)
    assert eq["n"] == 0 and eq["series"] == []
    assert eq["equity_last"] is None and eq["r_last"] is None
    assert eq["equity_svg"]["empty"] is True and eq["r_svg"]["empty"] is True
    # a flat baseline exists so the template can still draw something safely
    assert eq["equity_svg"]["baseline_y"] > 0


def test_equity_curve_one_trade_no_crash(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", close_time_msc=_ms(10), net_profit=4.0)
    eq = views.equity_curve(conn)
    assert eq["n"] == 1
    assert eq["equity_last"] == pytest.approx(4.0)
    s = eq["equity_svg"]
    assert s["empty"] is False and s["points"]  # single-point geometry, no crash
    assert eq["r_svg"]["empty"] is True          # no r_multiple → empty R curve, still safe


def test_dashboard_context_carries_live_and_equity(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", close_time_msc=_ms(10), net_profit=8.0)
    ctx = views.dashboard_context(conn)
    assert "live" in ctx and "equity" in ctx
    assert ctx["equity"]["equity_last"] == pytest.approx(8.0)
    assert ctx["live"]["empty"] is True  # no open positions seeded


def test_is_loopback():
    from journal.cli import _is_loopback

    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("::1") is True
    assert _is_loopback("localhost") is True
    assert _is_loopback("0.0.0.0") is False
    assert _is_loopback("192.168.1.5") is False
    assert _is_loopback("example.com") is False


def test_analytics_series_context_raw_closed_trades_nulls_preserved(conn):
    _seed_account(conn)
    # a fully-known trade, an R-unknown trade, and an OPEN trade (excluded)
    _seed_trade(conn, 1, net_profit=250.0, r_multiple=1.5,
                close_time_msc=_ms(10), sl_initial=3990.0)
    _seed_trade(conn, 2, net_profit=-80.0, r_multiple=None,
                close_time_msc=_ms(11))
    _seed_trade(conn, 3, status="open", net_profit=0.0, close_time_msc=None)
    ctx = views.analytics_series_context(conn)
    series = ctx["series"]
    assert [s["position_id"] for s in series] == [1, 2]  # open one excluded, time-ordered
    assert series[0]["net_profit"] == 250.0
    assert series[1]["r_multiple"] is None               # rule 4: unknown stays null
    # mae_r/mfe_r default null when the poller/candles haven't filled them
    assert series[0]["mae_r"] is None and series[0]["mfe_r"] is None


# --------------------------------------------------- route wiring (no httpx)
from starlette.routing import Match
from journal.web.app import create_app


def _resolve(app, method, path):
    """The FIRST route to fully-match (method, path), via Starlette's own matcher.
    Lets us assert route PRECEDENCE without an HTTP client (no httpx dependency)."""
    scope = {"type": "http", "method": method, "path": path}
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "name", None)
    return None


def test_api_and_chart_routes_beat_spa_catchall():
    app = create_app(":memory:")
    assert _resolve(app, "GET", "/api/dashboard") == "api_dashboard"
    assert _resolve(app, "GET", "/api/trades/1") == "api_trade_detail"
    assert _resolve(app, "GET", "/api/weekly/2026-W28") == "api_weekly"
    assert _resolve(app, "GET", "/trades/1/chart.png") == "trade_chart"


def test_root_and_client_routes_serve_the_spa():
    app = create_app(":memory:")
    for path in ("/", "/report", "/live", "/trades", "/trades/1", "/weekly/2026-W28", "/commands"):
        assert _resolve(app, "GET", path) == "spa", path


def test_legacy_app_prefix_is_just_a_client_path_now():
    app = create_app(":memory:")
    # /app was the transition mount; after cutover it is an ordinary client path
    # served by the SPA shell, NOT a dedicated route.
    assert _resolve(app, "GET", "/app") == "spa"
    assert _resolve(app, "GET", "/app/trades") == "spa"


def test_jinja_write_routes_are_gone():
    app = create_app(":memory:")
    # The Jinja form-POST write path is retired; the JSON /api/* twins remain.
    assert _resolve(app, "POST", "/trades/1/annotate") is None
    assert _resolve(app, "POST", "/live/1/close") is None


# -------------------------------------------------- /api/candles (no httpx)
# This file deliberately has no TestClient (see module docstring): FastAPI's
# TestClient needs httpx, which isn't a project dependency (CLAUDE.md rule 8).
# `_endpoint` finds the plain route function the same way `_resolve` finds its
# name, then calls it directly with an explicit `conn` — bypassing FastAPI's
# `Depends` wiring entirely, exactly like calling a `views`/`api` builder above.

import json


def _endpoint(app, name):
    for route in app.router.routes:
        if getattr(route, "name", None) == name:
            return route.endpoint
    raise AssertionError(f"no route named {name!r}")


def test_api_candles_route_returns_200_with_missing(conn):
    app = create_app(":memory:")
    fn = _endpoint(app, "api_candles")
    resp = fn(symbol="XAUUSDc", timeframe="M1", from_ms=0, to_ms=180000, conn=conn)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["symbol"] == "XAUUSDc" and "candles" in body and body["pending"] is True


def test_api_candles_route_400_on_bad_timeframe(conn):
    app = create_app(":memory:")
    fn = _endpoint(app, "api_candles")
    resp = fn(symbol="XAUUSDc", timeframe="M3", from_ms=0, to_ms=180000, conn=conn)
    assert resp.status_code == 400
    assert _resolve(app, "POST", "/api/trades/1/annotate") == "api_annotate"


def test_api_replay_prefs_get_null_then_put_then_get(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_replay_prefs")
    put_fn = _endpoint(app, "api_put_replay_prefs")

    resp = get_fn(conn=conn)
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"prefs": None}

    blob = {"version": 1, "symbol": "BTCUSDc", "timeframe": "M15",
            "startDate": "2026-01-02", "historyBars": 500, "speed": 7}
    put = put_fn(prefs=blob, conn=conn)
    put_body = json.loads(put.body)
    assert put_body["ok"] is True and isinstance(put_body["updated_ms"], int)

    resp2 = get_fn(conn=conn)
    assert json.loads(resp2.body) == {"prefs": blob}


def test_api_trade_png_prefs_get_null_then_put_then_get(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_trade_png_prefs")
    put_fn = _endpoint(app, "api_put_trade_png_prefs")

    resp = get_fn(conn=conn)
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"prefs": None}

    blob = {"theme": "yahoo", "pad_bars": 20}
    put = put_fn(prefs=blob, conn=conn)
    put_body = json.loads(put.body)
    assert put_body["ok"] is True and isinstance(put_body["updated_ms"], int)

    resp2 = get_fn(conn=conn)
    assert json.loads(resp2.body) == {"prefs": blob}


def test_api_trade_png_prefs_route_precedes_position_id_catchall():
    app = create_app(":memory:")
    # Route-ordering guard: /api/trades/png-prefs must resolve to its own
    # handler, not be captured by /api/trades/{position_id}.
    assert _resolve(app, "GET", "/api/trades/png-prefs") == "api_get_trade_png_prefs"
    assert _resolve(app, "PUT", "/api/trades/png-prefs") == "api_put_trade_png_prefs"


# ------------------------------------------------------ /api/size (no httpx)
# Same reason as /api/candles above: no TestClient/httpx. `_size` calls the
# plain route function directly (bypassing FastAPI's Depends wiring) and
# returns the parsed JSON body, mirroring `_endpoint`/`json.loads` elsewhere
# in this file.


def _size(conn, **body):
    app = create_app(":memory:")
    fn = _endpoint(app, "api_size")
    defaults = {"symbol": None, "entry": None, "sl": None, "tp": None,
                "risk_mode": "pct", "risk_value": None}
    defaults.update(body)
    resp = fn(conn=conn, **defaults)
    assert resp.status_code == 200
    return json.loads(resp.body)


def test_size_returns_the_reference_lot(conn):
    _seed_account(conn, balance=100_000.0)   # 100000 USC = $1000
    _seed_spec(conn)
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=4045.0,
              risk_mode="usc", risk_value=50.0)
    assert d["error"] is None
    assert abs(d["volume"] - 0.10) < 1e-9
    assert abs(d["risk_usc"] - 50.0) < 1e-6
    assert d["direction"] == "buy"
    assert abs(d["distance"] - 5.0) < 1e-9
    assert abs(d["rr"] - 2.0) < 1e-9          # (4045-4035) / (4035-4030)


def test_size_percent_mode_reads_the_balance(conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    # 0.05% of 100000 USC = 50 USC -> the same 0.10 lot.
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=None,
              risk_mode="pct", risk_value=0.05)
    assert d["error"] is None
    assert abs(d["volume"] - 0.10) < 1e-9
    assert abs(d["risk_pct"] - 0.05) < 1e-6
    assert d["rr"] is None                     # no TP set


def test_size_reports_the_realised_risk_of_the_rounded_lot(conn):
    """The lot is floored to the broker's step, so the risk actually taken is
    slightly BELOW the budget. Report what will happen, not what was asked."""
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=None,
              risk_mode="usc", risk_value=68.0)     # -> 0.136 lot -> floor 0.13
    assert abs(d["volume"] - 0.13) < 1e-9
    assert abs(d["risk_usc"] - 65.0) < 1e-6         # 0.13 lot, not 68
    assert d["risk_usc"] <= 68.0 + 1e-9


def test_size_refuses_a_stop_at_the_price(conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4035.0, tp=None,
              risk_mode="usc", risk_value=50.0)
    assert d["volume"] is None
    assert d["error"]                            # human-readable, non-empty
    assert d["direction"] is None


def test_size_refuses_a_budget_too_small_for_the_distance(conn):
    """0.4 USC over a 5-point XAUUSDc stop sizes to 0.0008 lot, which floors to
    0 — below volume_min. Say so; do not clamp up to the minimum."""
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=None,
              risk_mode="usc", risk_value=0.4)
    assert d["volume"] is None
    assert "minimum" in d["error"] or "kecil" in d["error"]


def test_size_refuses_over_the_risk_ceiling(conn):
    _seed_account(conn, balance=1000.0)          # 1000 USC; 5% = 50 USC
    _seed_spec(conn)
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=None,
              risk_mode="usc", risk_value=80.0)
    assert d["volume"] is None
    assert "5" in d["error"]


def test_size_refuses_an_unknown_symbol(conn):
    _seed_account(conn, balance=100_000.0)
    d = _size(conn, symbol="GBPUSDc", entry=1.25, sl=1.24, tp=None,
              risk_mode="usc", risk_value=50.0)
    assert d["volume"] is None
    assert "sync" in d["error"]


def test_size_refuses_percent_mode_with_an_unknown_balance(conn):
    _seed_account(conn, balance=None)
    _seed_spec(conn)
    d = _size(conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=None,
              risk_mode="pct", risk_value=1.0)
    assert d["volume"] is None
    assert "sync" in d["error"] or "balance" in d["error"]


# --------------------------------------------- /api/live/open* (no httpx)
# Same reason as /api/size above: no TestClient/httpx. `_open_preview`/`_open`
# call the plain route functions directly (bypassing FastAPI's Depends wiring)
# and return (status_code, parsed body), mirroring `_endpoint` elsewhere here.


def _call(app, name, conn, **body):
    fn = _endpoint(app, name)
    defaults = {"symbol": None, "entry": None, "sl": None, "tp": None,
                "risk_mode": "pct", "risk_value": None}
    defaults.update(body)
    resp = fn(conn=conn, **defaults)
    return resp.status_code, json.loads(resp.body)


def _open_preview(conn, **body):
    app = create_app(":memory:")
    return _call(app, "api_open_preview", conn, **body)


def _open(conn, **body):
    app = create_app(":memory:")
    return _call(app, "api_open", conn, **body)


def test_open_preview_writes_nothing_and_returns_the_sized_lot(conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    status, d = _open_preview(
        conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=4045.0,
        risk_mode="usc", risk_value=50.0,
    )
    assert status == 200
    assert d["kind"] == "open"
    assert d["position_id"] is None
    assert abs(d["fields"]["volume"] - 0.10) < 1e-9
    assert "XAUUSDc" in d["intent"] and "0.1" in d["intent"]
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_open_preview_refuses_over_the_ceiling_without_writing(conn):
    _seed_account(conn, balance=1000.0)
    _seed_spec(conn)
    status, d = _open_preview(
        conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=None,
        risk_mode="usc", risk_value=80.0,
    )
    assert status == 400
    assert "5" in d["error"]
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_open_enqueues_one_row_with_a_server_computed_volume(conn):
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    live_store.beat(conn, now_ms())      # an open needs a live feed (execute.py)
    status, d = _open(
        conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=4045.0,
        risk_mode="usc", risk_value=50.0,
    )
    assert status == 200 and d["ok"] is True
    row = conn.execute("SELECT * FROM trade_commands").fetchone()
    assert row["kind"] == "open"
    assert row["direction"] == "buy"
    assert abs(row["volume"] - 0.10) < 1e-9      # derived here, never sent by the client
    assert abs(row["price_ref"] - 4035.0) < 1e-9


def test_open_refuses_a_stale_feed_at_the_http_boundary(conn):
    """The browser gate (`lib/candles.staleEntryReason`) is not the only one:
    posting straight at the endpoint with no live feed must 400 and write no row,
    because `entry` is what the server derives the lot from."""
    _seed_account(conn, balance=100_000.0)
    _seed_spec(conn)
    # deliberately no live_store.beat()
    status, d = _open(
        conn, symbol="XAUUSDc", entry=4035.0, sl=4030.0, tp=4045.0,
        risk_mode="usc", risk_value=50.0,
    )
    assert status == 400
    assert "journal live" in d["error"]
    assert conn.execute("SELECT COUNT(*) FROM trade_commands").fetchone()[0] == 0


def test_a_literal_open_never_hits_the_position_id_route():
    """Route ordering. `/api/live/open/preview` must match the open route, not
    `/api/live/{position_id}/{action}/preview` — which would swallow 'open' as
    the position_id path segment."""
    app = create_app(":memory:")
    assert _resolve(app, "POST", "/api/live/open/preview") == "api_open_preview"
    assert _resolve(app, "POST", "/api/live/open") == "api_open"


def test_risk_prefs_round_trip(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_risk_prefs")
    put_fn = _endpoint(app, "api_put_risk_prefs")

    resp = get_fn(conn=conn)
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"prefs": None}

    body = {"mode": "pct", "value": 1.0}
    put = put_fn(prefs=body, conn=conn)
    put_body = json.loads(put.body)
    assert put_body["ok"] is True and isinstance(put_body["updated_ms"], int)

    resp2 = get_fn(conn=conn)
    assert json.loads(resp2.body) == {"prefs": body}


def test_api_drawings_get_null_then_put_then_get(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_drawings")
    put_fn = _endpoint(app, "api_put_drawings")

    resp = get_fn(symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"drawings": None}

    blob = {"v": 1, "items": [
        {"id": "d1", "kind": "hline", "price": 2415.5},
        {"id": "d2", "kind": "trend",
         "a": {"timeMs": 1_700_000_000_000, "price": 2400.0},
         "b": {"timeMs": 1_700_003_600_000, "price": 2420.0}},
    ]}
    put = put_fn(body=blob, symbol="XAUUSDc", session_id=None, conn=conn)
    put_body = json.loads(put.body)
    assert put_body["ok"] is True and isinstance(put_body["updated_ms"], int)

    resp2 = get_fn(symbol="XAUUSDc", session_id=None, conn=conn)
    assert json.loads(resp2.body) == {"drawings": blob}


def test_api_drawings_normalises_the_broker_suffix(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_drawings")
    put_fn = _endpoint(app, "api_put_drawings")
    blob = {"v": 1, "items": [{"id": "d1", "kind": "hline", "price": 2415.5}]}

    put_fn(body=blob, symbol="XAUUSDc", session_id=None, conn=conn)
    # Same base symbol without the suffix reads the same drawings (rule 11).
    resp = get_fn(symbol="XAUUSD", session_id=None, conn=conn)
    assert json.loads(resp.body) == {"drawings": blob}


def test_api_drawings_replay_session_does_not_see_live_drawings(conn):
    app = create_app(":memory:")
    get_fn = _endpoint(app, "api_get_drawings")
    put_fn = _endpoint(app, "api_put_drawings")

    live = {"v": 1, "items": [{"id": "live", "kind": "hline", "price": 2415.5}]}
    put_fn(body=live, symbol="XAUUSDc", session_id=None, conn=conn)

    resp = get_fn(symbol="XAUUSDc", session_id=3, conn=conn)
    assert json.loads(resp.body) == {"drawings": None}


def test_api_drawings_put_rejects_a_non_object_body(conn):
    app = create_app(":memory:")
    put_fn = _endpoint(app, "api_put_drawings")
    resp = put_fn(body=[1, 2, 3], symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 400
    assert "error" in json.loads(resp.body)


def test_api_drawings_put_rejects_an_oversized_blob(conn):
    app = create_app(":memory:")
    put_fn = _endpoint(app, "api_put_drawings")
    huge = {"v": 1, "items": [{"id": "x" * 300_000, "kind": "hline", "price": 1.0}]}
    resp = put_fn(body=huge, symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 400


def test_api_drawings_put_size_cap_accepts_a_large_unicode_blob_within_budget(conn):
    """Regression guard for the byte-vs-char question raised in code review:
    json.dumps()'s default ensure_ascii=True escapes every non-ASCII codepoint
    to \\uXXXX, so the dumped string is pure ASCII and len() on it already
    equals its encoded UTF-8 byte length — verified directly, not assumed.
    There is no under-count to reproduce for this code path; this test just
    pins that a unicode-heavy blob comfortably under the real cap is still
    accepted (i.e. the byte-length check isn't accidentally stricter than the
    old char-length one for non-ASCII content)."""
    app = create_app(":memory:")
    put_fn = _endpoint(app, "api_put_drawings")
    label = "€" * 1_000  # non-ASCII, well under the 256KiB cap either way it's measured
    body = {"v": 1, "items": [{"id": "d1", "kind": "text",
                                "a": {"timeMs": 0, "price": 1.0}, "text": label}]}
    resp = put_fn(body=body, symbol="XAUUSDc", session_id=None, conn=conn)
    assert resp.status_code == 200
