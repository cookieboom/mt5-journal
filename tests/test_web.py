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

from journal.analytics.report import build_report
from journal.store.db import connect
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
