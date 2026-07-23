"""The /api JSON layer (M-frontend). Tested like tests/test_web.py: pure
functions against a seeded DB, no HTTP/httpx. What must hold: the payload is
JSON-serialisable, money keeps its currency, NULL stays null (never 0), and the
§9 gate surfaces as JSON null."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

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


def _seed_trade(conn, position_id, *, net_profit=0.0, r_multiple=None,
                 close_time_msc=None):
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, close_time_msc, volume, open_price, "
        "close_price, sl_initial, net_profit, r_multiple, magic, deal_count, rebuilt_at) "
        "VALUES (?, ?, 'XAUUSDc', 'XAUUSD', 'buy', 'closed', ?, ?, 0.1, 4000.0, "
        "4001.0, NULL, ?, ?, NULL, 2, 1)",
        (_LOGIN, position_id, _ms(9), close_time_msc or _ms(10),
         net_profit, r_multiple),
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

def _seed_spec(conn, symbol="XAUUSDc", *, trade_mode=4,
               volume_min=0.01, volume_max=100.0, volume_step=0.01):
    conn.execute(
        "INSERT INTO symbol_specs (symbol, symbol_base, fetched_at, "
        "volume_min, volume_max, volume_step, trade_mode) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (symbol, symbol[:-1], volume_min, volume_max, volume_step, trade_mode),
    )
    conn.commit()


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
