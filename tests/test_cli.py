"""CLI tests. `candles-coverage` is the only new command testable without the
live bridge — `candles-warm` and `candles`/`doctor` all construct `LiveMT5Client`
and are exercised manually, not in this suite."""

from __future__ import annotations

from typer.testing import CliRunner

from journal.cli import app
from journal.store.db import connect
from journal.store import candles_store as cs


def test_candles_coverage_prints_ranges(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    cs.record_coverage(conn, "XAUUSDc", "M1", 0, 180000)
    conn.commit()
    conn.close()

    res = CliRunner().invoke(app, ["candles-coverage", "--db", str(db)])

    assert res.exit_code == 0
    assert "XAUUSDc" in res.stdout and "M1" in res.stdout
