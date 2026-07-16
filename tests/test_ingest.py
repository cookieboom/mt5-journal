"""M1 ingest + verify, all under FakeMT5Client against the REAL recorded fixtures
(tests/fixtures/*.json) — no bridge, nothing on :8001.

The fixtures are this account's sanitised history: 140 deals (login redacted to 0),
97 orders, 3 symbols. sum(deal cash)=6061.72, balance=6047.22 → a real +14.50 USC
residual caused by the broker archiving deals (Trap 16 / §6).
"""

from __future__ import annotations

import json
import time

import pytest

from journal.adapter.base import Tick
from journal.adapter.fake import FakeMT5Client
from journal.ingest.deals import add_reconciliation, sync, verify
from journal.store.db import connect

_LOGIN = 0  # fixtures are sanitised to login 0
_GAP = 14.50  # the archived-deals residual, §6
_TOL = 0.01
_BALANCE = 6047.22  # snapshot in account.json, matches the §6 report


# --- thin fakes: override one method each, so a sync can be steered per-test ----


class DropDealClient(FakeMT5Client):
    """Returns the real fixtures MINUS one deal — simulates the broker archiving a
    ticket this journal already holds (Trap 16). Everything else is unchanged."""

    def __init__(self, drop_ticket: int) -> None:
        super().__init__()
        self._drop = drop_ticket

    def history_deals_get(self, date_from=None, date_to=None):
        return [d for d in super().history_deals_get(date_from, date_to)
                if d.ticket != self._drop]


class TickClient(FakeMT5Client):
    """Forces the XAUUSDc tick: an epoch-seconds value yields a measurable offset,
    None simulates market-closed / no fresh tick. Default fixtures have no tick."""

    def __init__(self, tick_time: int | None) -> None:
        super().__init__()
        self._tick_time = tick_time

    def symbol_info_tick(self, symbol: str):
        if self._tick_time is None:
            return None
        return Tick(time=self._tick_time)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


@pytest.fixture
def client():
    return FakeMT5Client()  # default fixtures dir = tests/fixtures


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_sync_ingests_all_deals_and_orders(conn, client):
    r = sync(client, conn)
    assert _count(conn, "deals_raw") == 140
    assert _count(conn, "orders_raw") == 97
    assert r.deals_seen == 140 and r.deals_new == 140 and r.deals_existing == 0
    assert r.orders_new == 97
    assert r.account_login == _LOGIN
    # Broker returns every deal we hold → nothing archived (Trap 16 detector quiet).
    assert r.archived_tickets == []


def test_sync_is_idempotent(conn, client):
    sync(client, conn)
    r2 = sync(client, conn)  # second pull of the same history
    # No duplicates: append-only INSERT OR IGNORE (Trap 16).
    assert _count(conn, "deals_raw") == 140
    assert _count(conn, "orders_raw") == 97
    assert r2.deals_new == 0 and r2.deals_existing == 140
    assert r2.orders_new == 0 and r2.orders_existing == 97


def test_raw_json_roundtrips(conn, client):
    sync(client, conn)
    # Take a real deal from the source of truth and confirm the stored raw_json
    # deserialises back to the identical dict (raw = raw, forward-compatible).
    src = client.history_deals_get(None, None)[0]
    row = conn.execute(
        "SELECT raw_json FROM deals_raw WHERE account_login=? AND ticket=?",
        (_LOGIN, src.ticket),
    ).fetchone()
    assert json.loads(row["raw_json"]) == src.raw


def test_symbol_specs_three_symbols_distinct_tick_value(conn, client):
    sync(client, conn)
    rows = conn.execute("SELECT symbol, tick_value FROM symbol_specs").fetchall()
    assert len(rows) == 3
    values = [r["tick_value"] for r in rows]
    # XAU 0.1, BTC 0.01, EUR 1.0 — genuinely different; gold's specs do NOT transfer.
    for expected in (0.1, 0.01, 1.0):
        assert any(abs(v - expected) < 1e-9 for v in values), expected


def test_balance_and_correction_deals_are_stored_not_filtered(conn, client):
    # Trap 1 filtering is M2's job; M1 stores EVERY deal raw, incl. non-trades.
    sync(client, conn)
    balance_deals = conn.execute(
        "SELECT COUNT(*) FROM deals_raw WHERE type = 2"  # DEAL_TYPE_BALANCE
    ).fetchone()[0]
    assert balance_deals == 3
    # The archival headstone (Trap 16) must be captured, comment intact.
    corr = conn.execute(
        "SELECT comment FROM deals_raw WHERE ticket = 1399033630"
    ).fetchone()
    assert corr is not None and corr["comment"] == "Archived deals"


def test_accounts_row_upserted(conn, client):
    sync(client, conn)
    row = conn.execute("SELECT * FROM accounts WHERE login = ?", (_LOGIN,)).fetchone()
    assert row is not None
    assert row["currency"] == "USC"
    assert row["margin_mode"] == 2  # HEDGING


def test_verify_fails_with_the_1450_residual(conn, client):
    sync(client, conn)
    v = verify(conn)  # pure SQL — no client
    assert not v.passed
    assert abs(v.residual - _GAP) < _TOL  # +14.50: deals hold more than balance
    assert abs(v.deals_cash - 6061.72) < _TOL
    assert abs(v.balance - _BALANCE) < _TOL


def test_verify_passes_once_the_gap_is_reconciled(conn, client):
    sync(client, conn)
    # Name the gap, exactly as the human will after seeing verify FAIL.
    add_reconciliation(
        conn,
        _LOGIN,
        _GAP,
        effective_msc=1783745936454,  # correction deal 1399033630's time
        reason="Broker archived deals; underlying deals unrecoverable.",
        evidence="correction deal 1399033630, comment 'Archived deals'",
    )
    v = verify(conn)
    assert v.passed
    assert abs(v.residual) < _TOL
    assert abs(v.reconciled - _GAP) < _TOL


def test_verify_runs_without_a_client(conn, client):
    # The Trap 16 guarantee: once sync has stored the balance snapshot, verify needs
    # nothing but the DB. No bridge, no client — proves it works on a backup / in CI.
    sync(client, conn)
    del client  # gone; verify must not reach for it
    v = verify(conn)
    assert not v.passed
    assert abs(v.residual - _GAP) < _TOL


def test_sync_snapshots_balance_and_equity(conn, client):
    sync(client, conn)
    row = conn.execute(
        "SELECT balance, equity FROM accounts WHERE login = ?", (_LOGIN,)
    ).fetchone()
    # Captured by the same sync as the deals, so verify's two halves come from time T.
    assert abs(row["balance"] - _BALANCE) < 1e-9
    assert abs(row["equity"] - _BALANCE) < 1e-9  # fixture: flat, no open positions


def test_sync_detects_archived_tickets(conn, client):
    # 1) Full sync: journal now holds all 140 deals.
    sync(client, conn)
    # 2) A later sync where the broker has dropped one ticket we still hold (Trap 16).
    dropped = 1399033630  # the 'Archived deals' correction deal — any held ticket works
    r = sync(DropDealClient(dropped), conn)
    # The deal is NOT removed locally (append-only), and the detector names it.
    assert _count(conn, "deals_raw") == 140
    assert r.archived_tickets == [dropped]


def test_offset_survives_sync_without_tick(conn):
    # Trap 6: a no-tick sync must not clobber a real measurement with NULL.
    sync(TickClient(int(time.time())), conn)  # fresh tick → offset measured (0)
    before = conn.execute(
        "SELECT server_utc_offset_s FROM sync_state WHERE stream = 'deals'"
    ).fetchone()[0]
    assert before == 0  # a measured value, not NULL

    sync(TickClient(None), conn)  # no fresh tick → _measure_offset returns None
    after = conn.execute(
        "SELECT server_utc_offset_s FROM sync_state WHERE stream = 'deals'"
    ).fetchone()[0]
    assert after == 0  # COALESCE kept the measured 0, did not overwrite with NULL
