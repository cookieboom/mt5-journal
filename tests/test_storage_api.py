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


def test_storage_candles_completeness(client: TestClient, db_path: Path):
    conn = connect(db_path)
    # Add a second coverage range with a gap: [1000, 5000] and [8000, 10000]
    conn.execute(
        "UPDATE candle_coverage SET to_msc = 5000 WHERE symbol = 'XAUUSDc' AND timeframe = 'M1'"
    )
    conn.execute(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) "
        "VALUES ('XAUUSDc', 'M1', 8000, 10000)"
    )
    conn.commit()
    conn.close()

    response = client.get("/api/storage/candles/completeness?symbol=XAUUSDc&tf=M1")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["symbol"] == "XAUUSDc"
    assert data["timeframe"] == "M1"
    assert data["from_ms"] == 1000
    assert data["to_ms"] == 10000
    assert len(data["covered_ranges"]) == 2
    assert data["covered_ranges"][0] == {"from_ms": 1000, "to_ms": 5000}
    assert data["covered_ranges"][1] == {"from_ms": 8000, "to_ms": 10000}
    assert len(data["gaps"]) == 1
    assert data["gaps"][0]["from_ms"] == 5001
    assert data["gaps"][0]["to_ms"] == 7999
    assert "duration_hours" in data["gaps"][0]
    assert data["total_bars"] == 1
    assert "coverage_percent" in data


def test_storage_candles_fetch(client: TestClient, db_path: Path):
    payload = {
        "symbol": "XAUUSDc",
        "timeframe": "M1",
        "from_ms": 20000,
        "to_ms": 30000,
    }
    response = client.post("/api/storage/candles/fetch", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "queued"
    assert "request_id" in data
    assert data["request_id"] > 0

    conn = connect(db_path)
    row = conn.execute(
        "SELECT * FROM candle_requests WHERE id = ?", (data["request_id"],)
    ).fetchone()
    assert row is not None
    assert row["symbol"] == "XAUUSDc"
    assert row["from_msc"] == 20000
    assert row["to_msc"] == 30000
    conn.close()


def test_storage_candles_fill_gaps(client: TestClient, db_path: Path):
    conn = connect(db_path)
    conn.execute("DELETE FROM candle_requests")
    conn.execute(
        "UPDATE candle_coverage SET to_msc = 5000 WHERE symbol = 'XAUUSDc' AND timeframe = 'M1'"
    )
    conn.execute(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) "
        "VALUES ('XAUUSDc', 'M1', 8000, 10000)"
    )
    conn.commit()
    conn.close()

    response = client.post("/api/storage/candles/fill-gaps", json={"symbol": "XAUUSDc", "tf": "M1"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "queued"
    assert data["requests_count"] == 1

    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM candle_requests").fetchall()
    assert len(rows) == 1
    assert rows[0]["from_msc"] == 5001
    assert rows[0]["to_msc"] == 7999
    conn.close()


def test_storage_candles_prune(client: TestClient, db_path: Path):
    conn = connect(db_path)
    # Insert old candle (older than 180 days, e.g. 200 days ago)
    old_time = 1000
    # Insert newer candle (e.g. now_ms - 1 day)
    from journal.store.db import now_ms
    new_time = now_ms() - 86400 * 1000

    conn.execute(
        "INSERT INTO candles (symbol, timeframe, time_msc, open, high, low, close, tick_volume) "
        "VALUES ('XAUUSDc', 'M1', ?, 2000.0, 2005.0, 1995.0, 2002.0, 10)",
        (new_time,),
    )
    conn.execute(
        "INSERT INTO candle_coverage (symbol, timeframe, from_msc, to_msc) "
        "VALUES ('XAUUSDc', 'M1', ?, ?)",
        (new_time, new_time + 60000),
    )
    conn.commit()
    conn.close()

    response = client.post("/api/storage/candles/prune", json={"symbol": "XAUUSDc", "older_than_days": 180})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "ok"
    assert data["deleted_bars"] == 1

    conn = connect(db_path)
    remaining = conn.execute("SELECT time_msc FROM candles WHERE symbol = 'XAUUSDc'").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["time_msc"] == new_time
    conn.close()


def test_storage_candles_export_csv_and_json(client: TestClient, db_path: Path):
    # Test JSON export format (default format)
    response_json = client.get("/api/storage/candles/export?symbol=XAUUSDc&tf=M1&format=json")
    assert response_json.status_code == 200, response_json.text
    data = response_json.json()
    assert data["symbol"] == "XAUUSDc"
    assert data["timeframe"] == "M1"
    assert data["count"] == 1
    assert len(data["bars"]) == 1
    bar = data["bars"][0]
    assert bar["time_msc"] == 1000
    assert bar["open"] == 2000.0
    assert bar["high"] == 2005.0
    assert bar["low"] == 1995.0
    assert bar["close"] == 2002.0
    assert bar["tick_volume"] == 10

    # Test CSV export format
    response_csv = client.get("/api/storage/candles/export?symbol=XAUUSDc&tf=M1&format=csv")
    assert response_csv.status_code == 200, response_csv.text
    assert "text/csv" in response_csv.headers["content-type"]
    assert response_csv.headers["content-disposition"] == 'attachment; filename="XAUUSDc_M1_candles.csv"'
    csv_lines = response_csv.text.strip().splitlines()
    assert len(csv_lines) == 2
    assert csv_lines[0] == "time_msc,open,high,low,close,tick_volume"
    assert csv_lines[1] == "1000,2000.0,2005.0,1995.0,2002.0,10"

    # Test query params: timeframe alias and from_ms / to_ms filtering out of range
    response_empty = client.get("/api/storage/candles/export?symbol=XAUUSDc&timeframe=M1&from_ms=5000&to_ms=10000&format=json")
    assert response_empty.status_code == 200
    assert response_empty.json()["count"] == 0


