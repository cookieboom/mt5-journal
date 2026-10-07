"""/api/indicators/* through the real app."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from journal.adapter.base import Candle
from journal.store import candles_store as cs
from journal.store import training_store as ts
from journal.store.db import connect
from journal.web.app import create_app

M5 = 300_000
T0 = 1_767_225_600_000


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "j.db"
    conn = connect(path)
    for i in range(20):
        cs.insert_candle(conn, "XAUUSDc", "M5", Candle(
            time_msc=T0 + i * M5, open=100 + i, high=101 + i, low=99 + i, close=100 + i,
            tick_volume=1, spread=1, real_volume=0))
    cs.record_coverage(conn, "XAUUSDc", "M5", T0, T0 + 19 * M5)
    conn.commit()
    yield path, conn
    conn.close()


@pytest.fixture
def client(db, tmp_path):
    app = create_app(str(db[0]), cache_dir=str(tmp_path / "cache"))
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def test_compute_library_script(client):
    r = client.post("/api/indicators/compute", json={
        "script": "lib:sma", "inputs": {"length": 2}, "symbol": "XAUUSDc",
        "timeframe": "M5", "from_ms": T0 + 10 * M5, "to_ms": T0 + 11 * M5})
    assert r.status_code == 200
    assert r.json()["plots"][0]["values"] == [109.5, 110.5]


def test_compute_script_error_is_400_with_position(client):
    r = client.post("/api/indicators/compute", json={
        "source": "plot(sma(close, 0))", "symbol": "XAUUSDc", "timeframe": "M5",
        "from_ms": T0, "to_ms": T0 + M5})
    assert r.status_code == 400
    assert r.json()["script_error"]["line"] == 1
    assert "window" in r.json()["error"]


def test_compute_in_replay_never_passes_the_cursor(client, db):
    sid = ts.create_session(db[1], symbol="XAUUSDc", symbol_base="XAUUSD", timeframe="M5",
                            range_start_msc=T0, range_end_msc=T0 + 19 * M5,
                            cursor_msc=T0 + 5 * M5)
    r = client.post("/api/indicators/compute", json={
        "source": "plot(close)", "symbol": "XAUUSDc", "timeframe": "M5",
        "from_ms": T0, "to_ms": T0 + 19 * M5, "session_id": sid, "include_forming": True})
    assert r.json()["times"][-1] == T0 + 5 * M5


def test_unknown_session_is_400(client):
    r = client.post("/api/indicators/compute", json={
        "source": "plot(close)", "symbol": "XAUUSDc", "timeframe": "M5",
        "from_ms": T0, "to_ms": T0, "session_id": 42})
    assert r.status_code == 400 and "session" in r.json()["error"]


def test_validate(client):
    assert client.post("/api/indicators/validate", json={"source": "plot(close)"}).json()["ok"]
    bad = client.post("/api/indicators/validate", json={"source": "import os"}).json()
    assert bad["ok"] is False and bad["error"]["line"] == 1


def test_script_crud(client):
    made = client.post("/api/indicators/scripts", json={"name": "A", "source": "plot(close)"})
    sid = made.json()["id"]
    assert client.put(f"/api/indicators/scripts/{sid}",
                      json={"name": "B", "source": "plot(open)"}).status_code == 200
    names = {s["id"]: s["name"] for s in client.get("/api/indicators/scripts").json()["scripts"]}
    assert names[sid] == "B" and "lib:ema" in names
    assert client.put(f"/api/indicators/scripts/{sid}",
                      json={"name": "B", "source": "plot("}).status_code == 400
    assert client.put("/api/indicators/scripts/lib:ema",
                      json={"name": "B", "source": "plot(open)"}).status_code == 400
    assert client.delete(f"/api/indicators/scripts/{sid}").status_code == 200


def test_layout_roundtrip_and_cap(client):
    assert client.get("/api/indicators/layout").json() == {"layout": None}
    assert client.put("/api/indicators/layout", json={"version": 1, "items": []}).status_code == 200
    assert client.get("/api/indicators/layout").json()["layout"]["version"] == 1
    big = {"items": ["x" * 70_000]}
    assert client.put("/api/indicators/layout", json=big).status_code == 400


def test_tf_lower_than_the_chart_is_400(client):
    r = client.post("/api/indicators/compute", json={
        "source": 'plot(tf("M1", close))', "symbol": "XAUUSDc", "timeframe": "M5",
        "from_ms": T0, "to_ms": T0 + M5})
    assert r.status_code == 400 and "higher timeframes" in r.json()["error"]
