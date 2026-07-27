"""Tests for storage overview and maintenance endpoints (Task 1)."""
from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from journal.store.db import connect
from journal.web.app import create_app, _CACHE_DIR


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "journal.db"
    conn = connect(p)
    # Seed account
    conn.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (1001, 'USD', 1000)"
    )
    # Seed candles
    conn.execute(
        "INSERT INTO candles (symbol, timeframe, time_msc, open, high, low, close, tick_volume) "
        "VALUES ('XAUUSDc', 'M1', 1000, 2000.0, 2005.0, 1995.0, 2002.0, 10)"
    )
    # Seed candle_coverage
    conn.execute(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) "
        "VALUES ('XAUUSDc', 'M1', 1000, 2000)"
    )
    conn.execute(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) "
        "VALUES ('EURUSD', 'M1', 1000, 2000)"
    )
    # Seed raw deals for rebuild
    conn.execute(
        "INSERT INTO deals_raw (account_login, ticket, position_id, symbol, type, entry, volume, price, time_msc, profit, commission, swap, magic, raw_json, ingested_at) "
        "VALUES (1001, 1, 101, 'XAUUSDc', 0, 0, 0.1, 2000.0, 1000, 0, -1.0, 0, 0, '{}', 1000)"
    )
    conn.execute(
        "INSERT INTO deals_raw (account_login, ticket, position_id, symbol, type, entry, volume, price, time_msc, profit, commission, swap, magic, raw_json, ingested_at) "
        "VALUES (1001, 2, 101, 'XAUUSDc', 1, 1, 0.1, 2010.0, 2000, 10.0, -1.0, 0, 0, '{}', 2000)"
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app = create_app(str(db_path))
    return TestClient(app)


def test_storage_overview(client: TestClient, db_path: Path):
    # Ensure cache dir has a dummy file
    cache_dir = Path(_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dummy_file = cache_dir / "test_dummy.png"
    dummy_file.write_bytes(b"1234567890")

    try:
        response = client.get("/api/storage/overview")
        assert response.status_code == 200, response.text
        data = response.json()
        assert "db_size_bytes" in data
        assert data["db_size_bytes"] > 0
        assert "wal_size_bytes" in data
        assert isinstance(data["wal_size_bytes"], int)
        assert data["total_m1_bars"] == 1
        assert "total_trades" in data
        assert data["cache_files_count"] >= 1
        assert data["cache_size_bytes"] >= 10
        assert data["symbols"] == ["EURUSD", "XAUUSDc"]
    finally:
        if dummy_file.exists():
            dummy_file.unlink()


def test_storage_clear_cache(client: TestClient):
    cache_dir = Path(_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    f1 = cache_dir / "temp1.png"
    f2 = cache_dir / "temp2.png"
    f1.write_bytes(b"hello")
    f2.write_bytes(b"world!")

    response = client.post("/api/storage/maintenance/clear-cache")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["cleared_files"] >= 2
    assert data["freed_bytes"] >= 11

    assert not f1.exists()
    assert not f2.exists()


def test_storage_vacuum(client: TestClient):
    response = client.post("/api/storage/maintenance/vacuum")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "ok"
    assert "db_size_after" in data
    assert data["db_size_after"] > 0


def test_storage_rebuild(client: TestClient):
    response = client.post("/api/storage/maintenance/rebuild")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "ok"
    assert data["trades_rebuilt"] == 1
