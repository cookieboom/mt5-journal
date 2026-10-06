"""The web app answers only to the local machine.

`journal serve` binds to loopback, but the browser is what connects — and a
DNS-rebinding page reaches 127.0.0.1 under its own hostname. So the app itself
checks `Host` on every request and `Origin` on every state-changing one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from journal.web.app import create_app

_WATCH = {"symbol": "XAUUSDc", "timeframe": "M15"}


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "j.db"), cache_dir=str(tmp_path / "cache"))
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:5173", "[::1]:8000", "LOCALHOST"])
def test_loopback_hosts_are_served(client, host):
    r = client.post("/api/watch", json=_WATCH, headers={"Host": host})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("host", ["rebind.attacker.example", "rebind.attacker.example:8000",
                                  "127.0.0.1.attacker.example", ""])
def test_foreign_host_is_refused_on_reads_and_writes(client, host):
    assert client.get("/api/account", headers={"Host": host}).status_code == 403
    assert client.post("/api/watch", json=_WATCH, headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example"])
def test_foreign_origin_cannot_write(client, origin):
    r = client.post("/api/watch", json=_WATCH, headers={"Origin": origin})
    assert r.status_code == 403


def test_foreign_origin_on_a_read_is_left_to_the_browser(client):
    # A cross-origin GET cannot read the response (no CORS headers are sent),
    # and the page itself navigates with GET — refusing it buys nothing.
    assert client.get("/api/account", headers={"Origin": "https://evil.example"}).status_code != 403


@pytest.mark.parametrize("origin", [None, "http://localhost:5173", "http://127.0.0.1:8000"])
def test_local_or_absent_origin_can_write(client, origin):
    headers = {"Origin": origin} if origin else {}
    assert client.post("/api/watch", json=_WATCH, headers=headers).status_code == 200
