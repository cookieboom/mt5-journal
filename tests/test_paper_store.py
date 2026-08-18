"""Pure DB access for the paper tables. No bridge, no MT5. The invariant worth a
test here is the partial-close SPLIT: the closed slice becomes its own complete
row so no statistic ever needs to understand a half-realised position."""
from __future__ import annotations

import pytest

from journal.store import paper_store as ps
from journal.store.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "journal.db")
    yield c
    c.close()


@pytest.fixture
def account(conn):
    return ps.create_account(conn, name="Scalping XAU", initial_balance=1_000_000.0,
                             leverage=500, stopout_pct=20.0)


def _pos(conn, account, **kw):
    base = dict(account_id=account, symbol="XAUUSDc", symbol_base="XAUUSD",
                direction="buy", order_kind="market", request_price=None,
                volume=0.10, sl=0.0, tp=0.0, status="open", entry_price=4030.0,
                entry_msc=1_000, expires_msc=None)
    base.update(kw)
    return ps.insert_position(conn, **base)


def test_an_account_starts_with_its_balance_equal_to_what_was_funded(conn, account):
    row = ps.get_account(conn, account)
    assert row["balance"] == pytest.approx(1_000_000.0)
    assert row["initial_balance"] == pytest.approx(1_000_000.0)
    assert row["status"] == "active"


def test_two_accounts_cannot_share_a_name(conn, account):
    with pytest.raises(ValueError, match="sudah dipakai"):
        ps.create_account(conn, name="Scalping XAU", initial_balance=1.0,
                          leverage=500, stopout_pct=20.0)


def test_archiving_keeps_the_row_and_stamps_when(conn, account):
    ps.archive_account(conn, account)
    row = ps.get_account(conn, account)
    assert row["status"] == "archived"
    assert row["archived_at_msc"] is not None


def test_listing_filters_by_status(conn, account):
    other = ps.create_account(conn, name="Swing", initial_balance=1.0,
                              leverage=100, stopout_pct=50.0)
    ps.archive_account(conn, other)
    assert [r["id"] for r in ps.list_accounts(conn, status="active")] == [account]


def test_balance_moves_by_a_signed_delta(conn, account):
    ps.add_balance(conn, account, -250.5)
    assert ps.get_account(conn, account)["balance"] == pytest.approx(999_749.5)


def test_symbols_needing_a_quote_are_the_open_and_pending_ones_only(conn, account):
    _pos(conn, account, symbol="XAUUSDc", status="open")
    _pos(conn, account, symbol="BTCUSDc", status="pending", entry_price=None,
         order_kind="limit", request_price=50_000.0)
    closed = _pos(conn, account, symbol="EURUSDc", status="open")
    ps.mark_close(conn, closed, exit_msc=2_000, exit_price=4031.0,
                  exit_reason="manual", net_profit=10.0, r_multiple=None,
                  mae=None, mfe=None, mae_r=None, mfe_r=None)
    assert sorted(ps.open_or_pending_symbols(conn)) == ["BTCUSDc", "XAUUSDc"]


def test_marking_a_fill_writes_the_initial_stop_once(conn, account):
    pid = _pos(conn, account, status="pending", entry_price=None, entry_msc=None,
               sl=4025.0)
    ps.mark_fill(conn, pid, entry_msc=1_500, entry_price=4030.5, sl_initial=4025.0)
    row = ps.get_position(conn, pid)
    assert row["status"] == "open"
    assert row["entry_price"] == pytest.approx(4030.5)
    assert row["sl_initial"] == pytest.approx(4025.0)

    # Moving the stop later must not rewrite the initial one — R depends on it.
    ps.set_sltp(conn, pid, sl=4029.0, tp=0.0)
    row = ps.get_position(conn, pid)
    assert row["sl"] == pytest.approx(4029.0)
    assert row["sl_initial"] == pytest.approx(4025.0)


def test_a_partial_close_splits_into_a_closed_child_and_a_smaller_parent(conn, account):
    parent = _pos(conn, account, volume=0.10)
    child = ps.split_for_partial(conn, parent, 0.04)

    parent_row = ps.get_position(conn, parent)
    child_row = ps.get_position(conn, child)
    assert parent_row["volume"] == pytest.approx(0.06)
    assert parent_row["status"] == "open"
    assert child_row["volume"] == pytest.approx(0.04)
    assert child_row["parent_id"] == parent
    # The child is a COMPLETE trade record: it carries the parent's entry.
    assert child_row["entry_price"] == pytest.approx(4030.0)
    assert child_row["entry_msc"] == 1_000
    assert child_row["status"] == "open"       # the caller then closes it


def test_splitting_more_than_is_held_is_refused(conn, account):
    parent = _pos(conn, account, volume=0.10)
    with pytest.raises(ValueError, match="lebih besar"):
        ps.split_for_partial(conn, parent, 0.10)


def test_deleting_an_account_takes_its_positions_with_it(conn, account):
    _pos(conn, account)
    conn.execute("DELETE FROM paper_accounts WHERE id = ?", (account,))
    conn.commit()
    assert ps.list_positions(conn, account) == []
