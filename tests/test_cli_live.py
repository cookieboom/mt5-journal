"""CLI-level tests for `journal live` — the two-thread wiring (Task 7).

Every loop-level property (intervals, backup, bridge resilience, stop_event) is
covered against `position_loop`/`symbol_loop` in `tests/test_live.py`. What only
exists in `cli.py` — and had no test at all before this — is the WIRING: both
loops started as threads over one shared `LockedMT5Client`, two connections, and
the "database is locked" refusal that must survive being raised inside a thread
instead of inline.

Runs under a fake client with no positions: NO test here may need a live bridge.
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from journal.adapter.fake import FakeMT5Client
from journal.cli import app
from journal.store.db import connect

runner = CliRunner()

_LOGIN = 1_000_001  # placeholder, never the real login (rule 10)


class _NoPositions(FakeMT5Client):
    """The bridge is up and flat. Enough for one cycle of each loop without
    dragging the deal/candle fixtures into a wiring test."""

    def positions_get(self):
        return []


@pytest.fixture
def db(tmp_path):
    """A store `journal sync` has already touched — `_one_account_login` exits
    friendly-style without a row here, which would pass the assertions below for
    the wrong reason."""
    path = tmp_path / "j.db"
    c = connect(path)
    c.execute(
        "INSERT INTO accounts (login, currency, first_seen_at) VALUES (?, 'USC', 1)",
        (_LOGIN,),
    )
    c.commit()
    c.close()
    return path


def _invoke(db, *extra):
    return runner.invoke(app, [
        "live", "--once", "--db", str(db),
        "--interval-positions", "0.01", "--interval-symbols", "0.01",
        "--no-trading", *extra,
    ])


def test_live_once_reports_both_loops(db, monkeypatch):
    monkeypatch.setattr("journal.adapter.select.get_client", lambda: _NoPositions())

    result = _invoke(db)

    assert result.exit_code == 0, result.output
    assert "position cycles" in result.stdout
    assert "symbol cycles" in result.stdout
    # Both loops actually RAN — a summary printed off two empty holders would
    # still contain both labels.
    assert "stopped by:    once" in result.stdout
    assert result.stdout.count("stopped by:") == 2


def test_live_shares_one_client_between_both_loops(db, monkeypatch):
    """One bridge connection, serialized behind `LockedMT5Client` — two raw
    clients would mean two bridge sessions, which is the thing this process
    exists to prevent."""
    from journal.ingest.live import LiveLoopReport, SymbolLoopReport
    from journal.ingest.locked_client import LockedMT5Client

    made: list = []
    pos: list = []
    sym: list = []

    def _one():
        made.append(_NoPositions())
        return made[-1]

    monkeypatch.setattr("journal.adapter.select.get_client", _one)
    monkeypatch.setattr(
        "journal.ingest.live.position_loop",
        lambda client, conn, login, q, **k: (pos.append((client, conn)),
                                             LiveLoopReport(cycles=1, stopped_by="once"))[1],
    )
    monkeypatch.setattr(
        "journal.ingest.live.symbol_loop",
        lambda client, conn, q, **k: (sym.append((client, conn)),
                                      SymbolLoopReport(cycles=1, stopped_by="once"))[1],
    )

    result = _invoke(db)

    assert result.exit_code == 0, result.output
    assert len(made) == 1                        # exactly one bridge connection
    (pc, cp), (sc, cs_) = pos[0], sym[0]
    assert pc is sc and isinstance(pc, LockedMT5Client)   # one lock, both loops
    assert cp is not cs_                         # but a connection each


def test_a_locked_database_exits_one_with_the_single_live_message(db, monkeypatch):
    """The refusal now has to cross a thread boundary: the loop raises inside its
    own thread, and without the re-raise on the main thread this exits 0 with a
    cheerful summary while a second `journal live` quietly eats the writes."""
    monkeypatch.setattr("journal.adapter.select.get_client", lambda: _NoPositions())

    def _locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("journal.ingest.live.position_loop", _locked)

    result = _invoke(db)

    assert result.exit_code == 1, result.output
    assert "TERKUNCI" in result.stdout
    assert "HANYA SATU" in result.stdout
