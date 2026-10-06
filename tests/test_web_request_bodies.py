"""A malformed request body is the caller's mistake: 4xx with a readable
`error`, never a 500.

Every route that used to take an untyped `Body(...)` and index it by hand is
covered here. The SPA reads `error` from any failed response (`postJson` in
`frontend/src/lib/api.ts`), so validation and HTTPException errors must carry
it too, next to FastAPI's own `detail`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from journal.web.app import create_app

# (method, path, a well-formed body)
_ROUTES = [
    ("POST", "/api/watch", {"symbol": "XAUUSDc", "timeframe": "M15"}),
    ("POST", "/api/backfill", {"symbol": "XAUUSDc", "timeframe": "M15", "from_ms": 0, "to_ms": 1}),
    ("POST", "/api/storage/candles/fetch", {"symbol": "XAUUSDc", "timeframe": "M1", "from_ms": 0, "to_ms": 1}),
    ("POST", "/api/storage/candles/fill-gaps", {"symbol": "XAUUSDc", "timeframe": "M1"}),
    ("POST", "/api/storage/candles/prune", {"symbol": "XAUUSDc", "older_than_days": 180}),
    ("POST", "/api/lab/train", {"symbol": "XAUUSDc", "timeframe": "M15"}),
    ("PUT", "/api/trades/png-prefs", {"theme": "dark"}),
    ("PUT", "/api/chart/prefs", {"theme": "dark"}),
    ("PUT", "/api/replay/prefs", {"speed": 1}),
    ("PUT", "/api/risk-prefs", {"mode": "pct"}),
    ("PUT", "/api/prefs/paper", {"account_id": 1}),
    ("PUT", "/api/drawings?symbol=XAUUSDc", {"items": []}),
]


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "j.db"), cache_dir=str(tmp_path / "cache"))
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


@pytest.mark.parametrize("method,path,_ok", _ROUTES, ids=[r[1] for r in _ROUTES])
@pytest.mark.parametrize("bad", [b"not json", b"[1, 2]", b'"a string"', b"{}"],
                         ids=["garbage", "array", "string", "empty-object"])
def test_malformed_body_is_a_4xx_with_an_error(client, method, path, _ok, bad):
    if bad == b"{}" and "prefs" in path or bad == b"{}" and "drawings" in path:
        pytest.skip("an empty object is a valid prefs/drawings blob")
    if bad == b"{}" and path.endswith("/prune"):
        pytest.skip("prune has no required field")
    r = client.request(method, path, content=bad, headers={"Content-Type": "application/json"})
    assert 400 <= r.status_code < 500, (r.status_code, r.text)
    assert isinstance(r.json().get("error"), str) and r.json()["error"]


@pytest.mark.parametrize("method,path,ok", _ROUTES, ids=[r[1] for r in _ROUTES])
def test_well_formed_body_is_not_rejected_by_validation(client, method, path, ok):
    r = client.request(method, path, json=ok)
    assert r.status_code != 422, r.text
    assert r.status_code < 500, r.text


def test_storage_routes_still_accept_the_legacy_tf_key(client):
    r = client.post("/api/storage/candles/fill-gaps", json={"symbol": "XAUUSDc", "tf": "M1"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("days", [0, -5])
def test_prune_refuses_a_cutoff_at_or_after_now(client, days):
    # older_than_days <= 0 puts the cutoff at/after now: "older than" would
    # mean every candle in the store.
    r = client.post("/api/storage/candles/prune", json={"older_than_days": days})
    assert r.status_code == 422


def test_http_errors_carry_error_next_to_detail(client):
    # The lab raises HTTPException(400); the SPA used to show just "HTTP 400".
    r = client.post("/api/lab/train", json={"symbol": "XAUUSDc", "timeframe": "M15"})
    assert r.status_code == 400
    assert r.json()["error"] == r.json()["detail"]
    # A wrong method on an /api route comes from Starlette, not a handler.
    r = client.delete("/api/watch")
    assert r.status_code == 405 and r.json()["error"]
