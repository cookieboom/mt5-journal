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
from journal.store.db import connect, now_ms
from journal.web import format as fmt
from journal.web import views

_LOGIN = 0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


def _seed_account(conn, currency="USC"):
    conn.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, ?, 1)",
        (_LOGIN, currency),
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
):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, fetched_at, "
        "volume_min, volume_max, volume_step, trade_mode) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)",
        (symbol, symbol[:-1], volume_min, volume_max, volume_step, trade_mode),
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
):
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, sl, tp, "
        "volume, requested_msc, status, retcode, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_LOGIN, position_id, kind, sl, tp, volume, now_ms(), status, retcode, error),
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


# --------------------------------------------------- template rendering


def _env():
    """The SAME Jinja env `web/app.py` configures — built here so templates can
    be rendered against a seeded DB with no HTTP layer (matches this file's
    no-TestClient discipline)."""
    from pathlib import Path

    from fastapi.templating import Jinja2Templates

    here = Path(views.__file__).resolve().parent
    t = Jinja2Templates(directory=str(here / "templates"))
    t.env.filters.update(
        money=fmt.money, pct=fmt.pct, rmult=fmt.rmult, num=fmt.num,
        gated=fmt.gated, wib=fmt.wib, dur=fmt.dur, price=fmt.price,
    )
    t.env.globals["gated"] = fmt.gated
    t.env.globals["is_gated"] = fmt.is_gated
    return t.env


def _render(name, ctx):
    html = _env().get_template(name).render(ctx)
    assert isinstance(html, str) and html.strip()  # non-empty, did not raise
    return html


def _seed_a_bit(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, status="closed", close_time_msc=_ms(10, day=15),
                net_profit=10.0, r_multiple=1.3, symbol="XAUUSDc")
    _seed_trade(conn, 2, status="closed", close_time_msc=_ms(11, day=16),
                net_profit=-4.0, symbol="BTCUSDc")


def test_all_pages_render_with_seeded_db(conn):
    _seed_a_bit(conn)
    d = views.dashboard_context(conn); d["header"] = views.account_header(conn)
    _render("dashboard.html", d)
    rp = views.report_context(conn); rp["header"] = views.account_header(conn)
    _render("report.html", rp)
    _render("trades.html", views.trades_context(conn))
    _render("trade_detail.html", views.trade_detail_context(conn, 1))
    _render("weekly.html", views.weekly_context(conn, 2026, 3))
    lv = views.live_context(conn); lv["header"] = views.account_header(conn)
    _render("live.html", lv)
    cm = views.commands_context(conn); cm["header"] = views.account_header(conn)
    _render("commands.html", cm)
    _render("error.html", {"message": "contoh pesan error"})


def test_all_pages_render_with_empty_db(conn):
    # Account but ZERO trades / positions — the explicit plan verification.
    _seed_account(conn)
    d = views.dashboard_context(conn); d["header"] = views.account_header(conn)
    _render("dashboard.html", d)
    rp = views.report_context(conn); rp["header"] = views.account_header(conn)
    _render("report.html", rp)
    _render("trades.html", views.trades_context(conn))
    lv = views.live_context(conn); lv["header"] = views.account_header(conn)
    _render("live.html", lv)
    cm = views.commands_context(conn); cm["header"] = views.account_header(conn)
    _render("commands.html", cm)
    # trade_detail has no trade to show on an empty DB — the route returns None → 404.
    assert views.trade_detail_context(conn, 1) is None


def test_report_gated_cell_explains_itself_in_html(conn):
    # A thin bucket (n<20) must render its WHY, not a bare "n/a".
    _seed_a_bit(conn)
    rp = views.report_context(conn); rp["header"] = views.account_header(conn)
    html = _render("report.html", rp)
    assert "perlu ≥20" in html
    assert "n=" in html


def test_rendered_money_carries_currency_no_bare_dollar(conn):
    _seed_a_bit(conn)
    d = views.dashboard_context(conn); d["header"] = views.account_header(conn)
    html = _render("dashboard.html", d)
    assert "USC" in html          # money figures carry the currency code
    assert "$" not in html        # never a bare dollar (Trap 13)


def test_live_strip_labels_floating_not_realized(conn):
    _seed_account(conn)
    _seed_position(conn, 1, profit=12.0, observed_msc=now_ms())
    d = views.dashboard_context(conn); d["header"] = views.account_header(conn)
    html = _render("dashboard.html", d)
    assert "floating" in html.lower()  # floating P&L is labelled, never realized
    assert "USC" in html


def test_is_loopback():
    from journal.cli import _is_loopback

    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("::1") is True
    assert _is_loopback("localhost") is True
    assert _is_loopback("0.0.0.0") is False
    assert _is_loopback("192.168.1.5") is False
    assert _is_loopback("example.com") is False
