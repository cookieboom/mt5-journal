"""The /api JSON layer (M-frontend). Tested like tests/test_web.py: pure
functions against a seeded DB, no HTTP/httpx. What must hold: the payload is
JSON-serialisable, money keeps its currency, NULL stays null (never 0), and the
§9 gate surfaces as JSON null."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from journal.adapter.base import Candle
from journal.store import candles_store as cs
from journal.store.db import connect, now_ms
from journal.web import api

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
    return int(datetime(2026, 1, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _seed_trade(conn, position_id, *, symbol_base="XAUUSD", direction="buy",
                status="closed", net_profit=0.0, r_multiple=None,
                sl_initial=None, magic=None, close_time_msc=None,
                mae_r=None, mfe_r=None):
    symbol = symbol_base + "c"
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, duration_s, volume, "
        "open_price, close_price, sl_initial, net_profit, r_multiple, mae_r, mfe_r, "
        "magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.1, 4000.0, 4001.0, ?, ?, ?, ?, ?, ?, 2, 1)",
        (_LOGIN, position_id, symbol, symbol_base, direction, status, _ms(9),
         close_time_msc or _ms(10), 3600, sl_initial, net_profit, r_multiple,
         mae_r, mfe_r, magic),
    )
    conn.commit()


def test_to_jsonable_handles_dataclass_row_and_nesting(conn):
    _seed_account(conn)

    @dataclasses.dataclass
    class D:
        a: int
        b: float | None

    row = conn.execute("SELECT login, currency FROM accounts").fetchone()
    out = api.to_jsonable({"d": D(1, None), "rows": [row], "t": (1, 2)})
    assert out == {
        "d": {"a": 1, "b": None},
        "rows": [{"login": 0, "currency": "USC"}],
        "t": [1, 2],
    }
    json.dumps(out)  # must not raise


def test_to_jsonable_rejects_unknown_type():
    with pytest.raises(TypeError):
        api.to_jsonable(object())


def test_account_payload_shape(conn):
    _seed_account(conn)
    p = api.account_payload(conn)
    assert p == {"login": 0, "currency": "USC", "offset_s": 0}
    json.dumps(p)


def test_account_payload_raises_without_account(conn):
    with pytest.raises(RuntimeError):
        api.account_payload(conn)


def test_dashboard_payload_is_jsonable_and_honest(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=120.0, r_multiple=1.5)
    _seed_trade(conn, 2, net_profit=-80.0, r_multiple=-1.0)

    p = api.dashboard_payload(conn)
    json.dumps(p)  # must not raise

    assert set(p.keys()) == {"header", "report", "live", "equity"}
    assert p["header"]["currency"] == "USC"
    assert p["report"]["n_closed"] == 2
    # §9 gate: with only 2 R-known trades, avg_r is withheld as null (never 0).
    assert p["report"]["avg_r"] is None
    assert p["equity"]["n"] == 2
    assert isinstance(p["live"]["positions"], list)


# --- live / commands seed helpers (mirror tests/test_web.py) ---------------

def _seed_position(conn, position_id, *, symbol="XAUUSDc", direction="buy",
                   volume=0.10, open_price=4000.0, price_current=4010.0,
                   sl=None, tp=None, profit=0.0, observed_msc=None):
    observed_msc = now_ms() if observed_msc is None else observed_msc
    conn.execute(
        "INSERT INTO open_positions (account_login, position_id, symbol, symbol_base, "
        "direction, volume, open_price, price_current, sl, tp, profit, swap, magic, "
        "open_time_msc, observed_msc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
        (_LOGIN, position_id, symbol, symbol[:-1], direction, volume, open_price,
         price_current, sl, tp, profit, _ms(9), observed_msc),
    )
    conn.commit()


def _seed_command(conn, *, position_id=1, kind="close", status="pending",
                  retcode=None, error=None, sl=None, tp=None, volume=None):
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, sl, tp, "
        "volume, requested_msc, status, retcode, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_LOGIN, position_id, kind, sl, tp, volume, now_ms(), status, retcode, error),
    )
    conn.commit()


def test_live_payload_shape_and_floating(conn):
    _seed_account(conn)
    _seed_position(conn, 1, profit=120.0, volume=0.10)
    _seed_position(conn, 2, profit=-30.0, volume=0.20)
    p = api.live_payload(conn)
    json.dumps(p)
    assert p["header"]["currency"] == "USC"
    assert p["live"]["count"] == 2
    assert abs(p["live"]["total_floating"] - 90.0) < 1e-9
    assert p["live"]["empty"] is False
    # positions carry the full field set the card renders
    pos = {r["position_id"]: r for r in p["live"]["positions"]}
    assert pos[1]["direction"] == "buy"
    assert pos[1]["symbol_base"] == "XAUUSD"
    assert "price_current" in pos[1] and "sl" in pos[1] and "observed_msc" in pos[1]


def test_live_payload_empty_is_honest(conn):
    _seed_account(conn)
    p = api.live_payload(conn)
    assert p["live"]["empty"] is True
    assert p["live"]["count"] == 0
    assert p["live"]["positions"] == []


def test_live_status_offline_when_no_heartbeat(conn):
    from journal.web import api
    p = api.live_status_payload(conn, now_msc=1_700_000_100_000)
    assert p == {"live": False, "beat_msc": None, "age_ms": None}


def test_live_status_live_when_recent(conn):
    from journal.web import api
    from journal.store import live_store as ls
    ls.beat(conn, 1_700_000_100_000)
    p = api.live_status_payload(conn, stale_ms=15_000, now_msc=1_700_000_105_000)
    assert p["live"] is True and p["age_ms"] == 5_000


def test_live_status_stale_when_old(conn):
    from journal.web import api
    from journal.store import live_store as ls
    ls.beat(conn, 1_700_000_100_000)
    p = api.live_status_payload(conn, stale_ms=15_000, now_msc=1_700_000_200_000)
    assert p["live"] is False and p["age_ms"] == 100_000


def test_commands_payload_maps_retcode_name(conn):
    _seed_account(conn)
    _seed_command(conn, position_id=1, kind="close", status="done", retcode=10009)
    _seed_command(conn, position_id=2, kind="close", status="failed",
                  error="proses berhenti di tengah perintah")
    p = api.commands_payload(conn)
    json.dumps(p)
    by_pos = {c["position_id"]: c for c in p["commands"]}
    assert by_pos[1]["retcode_name"] == "DONE"      # name, not the int
    assert by_pos[2]["retcode_name"] is None         # nothing said yet
    assert by_pos[2]["error"] == "proses berhenti di tengah perintah"


# --- annotation / tag seed helpers (mirror tests/test_web.py) ---------------

def _seed_annotation(conn, position_id, *, setup=None, confidence=None,
                     emotion=None, followed_plan=None, notes=None):
    conn.execute(
        "INSERT INTO annotations (account_login, position_id, segment, setup, "
        "confidence, emotion, followed_plan, notes, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?, 1, 1)",
        (_LOGIN, position_id, setup, confidence, emotion, followed_plan, notes),
    )
    conn.commit()


def _seed_tag(conn, position_id, tag, source="manual"):
    conn.execute(
        "INSERT INTO tags (account_login, position_id, segment, tag, source) "
        "VALUES (?, ?, 0, ?, ?)",
        (_LOGIN, position_id, tag, source),
    )
    conn.commit()


def test_trades_payload_shape_filters_and_nulls(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, symbol_base="XAUUSD", net_profit=250.0, r_multiple=1.5, magic=None)
    _seed_trade(conn, 2, symbol_base="BTCUSD", net_profit=-80.0, r_multiple=None, magic=777)
    _seed_tag(conn, 1, "breakout", source="manual")
    p = api.trades_payload(conn)
    json.dumps(p)
    assert p["header"]["currency"] == "USC"
    assert {t["position_id"] for t in p["trades"]} == {1, 2}
    assert p["max_abs_net"] == 250.0
    assert "BTCUSD" in p["symbols"] and "XAUUSD" in p["symbols"]
    # rule 4: unknown R stays null, never 0
    by_pos = {t["position_id"]: t for t in p["trades"]}
    assert by_pos[2]["r_multiple"] is None
    # tags survive as [tag, source] pairs (json stringifies the int key)
    tags = json.loads(json.dumps(p))["tags"]
    assert tags["1"] == [["breakout", "manual"]]
    # source filter (ea = magic truthy) narrows to the EA trade
    ea = api.trades_payload(conn, source="ea")
    assert [t["position_id"] for t in ea["trades"]] == [2]
    assert ea["filters"]["source"] == "ea"
    # symbol filter narrows and is echoed back
    xau = api.trades_payload(conn, symbol="XAUUSD")
    assert [t["position_id"] for t in xau["trades"]] == [1]


def test_trade_detail_payload_facts_annotation_tags(conn):
    _seed_account(conn)
    _seed_trade(conn, 5, net_profit=100.0, r_multiple=None, sl_initial=None, magic=42)
    _seed_annotation(conn, 5, setup="breakout", confidence=4, followed_plan=1)
    _seed_tag(conn, 5, "auto-win", source="auto")
    p = api.trade_detail_payload(conn, 5)
    json.dumps(p)
    assert p["trade"]["position_id"] == 5
    assert p["trade"]["sl_initial"] is None      # rule 4: unknown, not 0
    assert p["trade"]["r_multiple"] is None
    assert p["is_ea"] is True                      # magic truthy
    assert p["chartable"] is True                  # closed + close_time set
    assert p["annotation"]["setup"] == "breakout"
    assert p["annotation"]["confidence"] == 4
    assert p["tags"] == [["auto-win", "auto"]]


def test_trade_detail_payload_missing_is_none(conn):
    _seed_account(conn)
    assert api.trade_detail_payload(conn, 999) is None


def test_trade_detail_payload_null_annotation(conn):
    _seed_account(conn)
    _seed_trade(conn, 7, net_profit=0.0)
    p = api.trade_detail_payload(conn, 7)
    assert p["annotation"] is None                 # no note yet → null, not {}
    assert p["tags"] == []


def test_trade_detail_payload_exposes_raw_symbol(conn):
    _seed_account(conn)
    _seed_trade(conn, 9, symbol_base="XAUUSD")
    p = api.trade_detail_payload(conn, 9)
    assert p["trade"]["symbol"] == "XAUUSDc"        # raw, suffixed — for the candle feed
    assert p["trade"]["symbol_base"] == "XAUUSD"


def test_report_payload_composes_report_and_series(conn):
    _seed_account(conn)
    _seed_trade(conn, 1, net_profit=250.0, r_multiple=1.5, mae_r=-0.4, mfe_r=2.1,
                close_time_msc=_ms(10))
    _seed_trade(conn, 2, net_profit=-80.0, r_multiple=None, close_time_msc=_ms(11))
    p = api.report_payload(conn)
    json.dumps(p)  # must not raise
    assert set(p.keys()) == {"header", "report", "series"}
    assert p["header"]["currency"] == "USC"
    assert p["report"]["n_closed"] == 2
    # §9 gate: only 2 R-known trades → avg_r withheld as null, never 0
    assert p["report"]["avg_r"] is None
    # series carries the raw per-trade chart source; nulls preserved (rule 4)
    by_pos = {s["position_id"]: s for s in p["series"]}
    assert by_pos[1]["mfe_r"] == 2.1
    assert by_pos[2]["r_multiple"] is None


def test_weekly_payload_shape_gating_and_notes(conn):
    _seed_account(conn)
    # two closed trades in ISO 2026-W03 (Jan 15 = Thu of week 3); one annotated
    _seed_trade(conn, 1, net_profit=250.0, close_time_msc=_ms(10, day=15))
    _seed_trade(conn, 2, net_profit=-80.0, close_time_msc=_ms(11, day=15))
    _seed_annotation(conn, 1, setup="breakout", confidence=4, followed_plan=1)
    _seed_tag(conn, 1, "revenge", source="manual")

    p = api.weekly_payload(conn, 2026, 3)
    json.dumps(p)  # must not raise
    assert set(p.keys()) == {"header", "result", "weeks", "start_ms"}
    r = p["result"]
    assert r["iso_year"] == 2026 and r["iso_week"] == 3
    assert r["n_closed"] == 2
    assert r["net_total"] == 170.0           # 250 + (-80); a sum, always shown
    assert r["win_rate"] is None             # §9: 2 < 20 → gated to null, not 0
    # notes surfaces the annotated/manually-tagged trade
    assert [n["position_id"] for n in r["notes"]] == [1]
    assert r["notes"][0]["setup"] == "breakout"
    # weeks nav lists (year, week) tuples as JSON arrays
    assert [2026, 3] in p["weeks"]


def test_weekly_payload_empty_week_is_honest(conn):
    _seed_account(conn)
    p = api.weekly_payload(conn, 2026, 3)
    assert p["result"]["n_closed"] == 0
    assert p["result"]["net_total"] == 0
    assert p["result"]["notes"] == []
    assert p["weeks"] == []                   # no closed trades → empty nav


# ------------------------------------------------------------- candles_payload

BASE = 1_700_000_000_000
M1 = 60_000


def _c(t):
    return Candle(time_msc=t, open=1, high=2, low=0.5, close=1.5,
                  tick_volume=3, spread=1, real_volume=3)


def test_candles_payload_serves_native_and_no_pending(tmp_path):
    conn = connect(tmp_path / "t.db")
    cs.insert_candle(conn, "XAUUSDc", "M1", _c(BASE + M1))
    cs.record_coverage(conn, "XAUUSDc", "M1", BASE, BASE + 3 * M1)
    conn.commit()
    p = api.candles_payload(conn, "XAUUSDc", "M1", BASE, BASE + 3 * M1)
    assert p["candles"] == [{"time_msc": BASE + M1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3}]
    assert p["missing"] == [] and p["pending"] is False


def test_candles_payload_enqueues_when_uncovered(tmp_path):
    conn = connect(tmp_path / "t.db")
    p = api.candles_payload(conn, "XAUUSDc", "M1", 0, 3 * M1)
    assert p["candles"] == []
    assert p["missing"] == [[0, 3 * M1]] and p["pending"] is True
    # the fill was queued, NOT executed (no bridge in web)
    n = conn.execute("SELECT count(*) FROM candle_requests WHERE status='pending'").fetchone()[0]
    assert n == 1


def test_candles_payload_rejects_unknown_timeframe(tmp_path):
    conn = connect(tmp_path / "t.db")
    with pytest.raises(ValueError):
        api.candles_payload(conn, "XAUUSDc", "M3", 0, 3 * M1)


def test_candles_payload_aggregates_correct_boundary_bucket_for_unaligned_from(tmp_path):
    # Regression (final-review Important): an unaligned from_ms must still yield a
    # CORRECT M5 bucket. 5 M1 bars form one M5 bucket [BASE, BASE+5*M1); the
    # request starts mid-bucket at BASE+2*M1. Without a bucket-aligned M1 read the
    # boundary bucket gets only its tail bars and reports a wrong open/high/low.
    # BASE shadowed locally: the module-level BASE (line 304) is NOT M5-bucket-
    # aligned (BASE % 300_000 != 0), so it can't exercise this boundary case.
    BASE = 1_700_000_100_000
    conn = connect(tmp_path / "t.db")
    opens = [10, 11, 12, 13, 14]; highs = [12, 20, 14, 13, 19]
    lows = [9, 8, 13, 10, 12];   closes = [11, 14, 13, 12, 19]
    for i in range(5):
        cs.insert_candle(conn, "XAUUSDc", "M1", Candle(
            time_msc=BASE + i*M1, open=opens[i], high=highs[i],
            low=lows[i], close=closes[i], tick_volume=1, spread=1, real_volume=1))
    cs.record_coverage(conn, "XAUUSDc", "M1", BASE, BASE + 5*M1); conn.commit()
    p = api.candles_payload(conn, "XAUUSDc", "M5", BASE + 2*M1, BASE + 5*M1)
    assert len(p["candles"]) == 1
    c = p["candles"][0]
    assert c["time_msc"] == BASE
    assert c["o"] == 10 and c["h"] == 20 and c["l"] == 8 and c["c"] == 19


def test_training_session_create_and_summary(conn):
    from journal.web import api as _api
    from journal.web import training as _tr
    created = _tr.create_session(conn, symbol="XAUUSDc", timeframe="M15",
                                 range_start_msc=1000, range_end_msc=9000,
                                 cursor_start_msc=1000)
    sid = created["session"]["id"]
    view = _api.to_jsonable(_tr.session_view(conn, sid))
    assert view["session"]["symbol"] == "XAUUSDc"
    assert view["positions"] == []
    summary = _api.to_jsonable(_tr.career_summary(conn))
    assert summary["n"] == 0 and summary["total_r"] == 0


def test_register_watch_makes_it_active(conn):
    from journal.web import api
    from journal.store import live_store as ls
    out = api.register_watch(conn, "XAUUSDc", "M5", ttl_ms=30_000, now_msc=1_700_000_000_000)
    assert out == {"ok": True}
    assert ls.active_watches(conn, 1_700_000_010_000) == [("XAUUSDc", "M5")]


def test_register_watch_rejects_bad_timeframe(conn):
    import pytest
    from journal.web import api
    with pytest.raises(ValueError):
        api.register_watch(conn, "XAUUSDc", "M7")


def test_coverage_payload_reports_covered_and_missing(conn):
    from journal.web import api
    from journal.store import candles_store as cs
    cs.record_coverage(conn, "XAUUSDc", "M5", 0, 300_000)
    conn.commit()
    p = api.coverage_payload(conn, "XAUUSDc", "M5", 0, 600_000)
    assert [0, 300_000] in p["covered"]
    assert [300_001, 600_000] in p["missing"]


def test_live_candle_payload_null_when_no_forming(conn):
    from journal.web import api
    p = api.live_candle_payload(conn, "XAUUSDc", "M5", now_msc=1_700_000_000_000)
    assert p["forming"] is None and p["live"] is False


def test_live_candle_payload_returns_forming_and_liveness(conn):
    from journal.web import api
    from journal.store import live_store as ls
    from journal.adapter.base import Candle
    ls.beat(conn, 1_700_000_000_000)
    ls.upsert_forming(conn, "XAUUSDc", "M5",
                      Candle(time_msc=1_700_000_040_000, open=1, high=2, low=0.5,
                             close=1.5, tick_volume=9, spread=2, real_volume=0),
                      1_700_000_040_000)
    p = api.live_candle_payload(conn, "XAUUSDc", "M5", now_msc=1_700_000_003_000)
    assert p["live"] is True
    assert p["forming"] == {"time_msc": 1_700_000_040_000, "o": 1, "h": 2,
                            "l": 0.5, "c": 1.5, "v": 9}


def test_training_routes_smoke(tmp_path):
    from fastapi.testclient import TestClient
    from journal.web.app import create_app
    db = tmp_path / "journal.db"
    client = TestClient(create_app(str(db)))
    r = client.post("/api/training/sessions", json={
        "symbol": "XAUUSDc", "timeframe": "M15",
        "range_start_msc": 1000, "range_end_msc": 9000, "cursor_start_msc": 1000,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    assert client.get(f"/api/training/sessions/{sid}").json()["positions"] == []
    assert client.get("/api/training/summary").json()["n"] == 0
    assert client.delete(f"/api/training/sessions/{sid}").json()["ok"] is True
    assert client.get(f"/api/training/sessions/{sid}").status_code == 404
