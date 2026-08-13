"""`store/health.py` — the checks behind `journal status`.

Every check here composes a detector that already ships elsewhere, so what is
pinned is NOT the detector (that has its own suite) but the three things
`status` adds: which state a given store lands in, that a failing check carries
the command that fixes it, and that a WARN never becomes a non-zero exit. The
last one is the whole design: a status command that exits 1 because nobody
backed up today is a status command that gets `|| true`'d.
"""

from __future__ import annotations

import json
import os

import pytest

from journal.adapter.base import DealType
from journal.store import health
from journal.store.db import connect

_LOGIN = 12345
_HOUR_MS = 3_600_000


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = connect(path)
    yield conn, path
    conn.close()


def _account(conn, balance=0.0):
    conn.execute(
        "INSERT INTO accounts (login, currency, balance, first_seen_at) "
        "VALUES (?, 'USC', ?, 1)",
        (_LOGIN, balance),
    )
    conn.commit()


def _deal(conn, ticket, position_id, profit=0.0, dtype=DealType.BUY, entry=0,
          ingested_at=1):
    conn.execute(
        "INSERT INTO deals_raw (account_login, ticket, position_id, symbol, type, "
        "entry, volume, price, profit, time_msc, raw_json, ingested_at) "
        "VALUES (?, ?, ?, 'XAUUSDc', ?, ?, 0.1, 4000.0, ?, 1, '{}', ?)",
        (_LOGIN, ticket, position_id, int(dtype), entry, profit, ingested_at),
    )
    conn.commit()


def _trade(conn, position_id, net=0.0):
    conn.execute(
        "INSERT INTO trades (account_login, position_id, symbol, symbol_base, "
        "direction, status, open_time_msc, volume, open_price, net_profit, "
        "deal_count, rebuilt_at) VALUES (?, ?, 'XAUUSDc', 'XAUUSD', 'buy', "
        "'closed', 1, 0.1, 4000.0, ?, 1, 1)",
        (_LOGIN, position_id, net),
    )
    conn.commit()


def _snapshot(tmp_path, age_s=0.0, name="journal-20260813T000000Z.db"):
    d = tmp_path / "backups"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(b"")
    if age_s:
        t = os.stat(p).st_mtime - age_s
        os.utime(p, (t, t))
    return p


def _by_name(checks):
    return {c.name: c for c in checks}


# ------------------------------------------------------------------ integrity


def test_integrity_passes_on_a_healthy_store(db):
    conn, path = db
    c = _by_name(health.checks(conn, path))["integrity"]
    assert c.state == "ok"


# -------------------------------------------------------------------- balance


def test_balance_warns_instead_of_raising_on_an_empty_store(db):
    """`verify` raises RuntimeError with no account row. A status command that
    dies on a fresh DB is useless exactly when a new user runs it."""
    conn, path = db
    c = _by_name(health.checks(conn, path))["balance"]
    assert c.state == "warn"
    assert c.fix == "journal sync"


def test_balance_passes_when_the_deals_reconstruct_to_the_balance(db):
    conn, path = db
    _account(conn, balance=10.0)
    _deal(conn, ticket=1, position_id=99, profit=10.0)
    _trade(conn, position_id=99, net=10.0)

    c = _by_name(health.checks(conn, path))["balance"]
    assert c.state == "ok"


def test_balance_fails_on_an_unexplained_residual(db):
    conn, path = db
    _account(conn, balance=10.0)
    _deal(conn, ticket=1, position_id=99, profit=25.0)   # 15.00 unexplained
    _trade(conn, position_id=99, net=25.0)

    c = _by_name(health.checks(conn, path))["balance"]
    assert c.state == "fail"
    assert c.fix == "journal verify"


# --------------------------------------------------------------------- trades


def test_trades_warns_when_raw_deals_were_never_reconstructed(db):
    """The gap with no detector today: `sync` pulled deals, nothing rebuilt."""
    conn, path = db
    _account(conn)
    _deal(conn, ticket=1, position_id=99)

    c = _by_name(health.checks(conn, path))["trades"]
    assert c.state == "warn"
    assert c.fix == "journal rebuild"
    assert "1" in c.detail


def test_trades_ok_once_every_position_has_a_trade_row(db):
    conn, path = db
    _account(conn)
    _deal(conn, ticket=1, position_id=99)
    _trade(conn, position_id=99)

    assert _by_name(health.checks(conn, path))["trades"].state == "ok"


def test_trades_warns_when_a_deal_landed_after_the_last_rebuild(db):
    """The case the row-count check cannot see: the position HAS a trade row,
    but a partial close (or a re-synced OUT) arrived after it was built."""
    conn, path = db
    _account(conn)
    _deal(conn, ticket=1, position_id=99, ingested_at=1)
    _trade(conn, position_id=99)                       # rebuilt_at = 1
    _deal(conn, ticket=2, position_id=99, entry=1, ingested_at=9_999)

    c = _by_name(health.checks(conn, path))["trades"]
    assert c.state == "warn"
    assert c.fix == "journal rebuild"


def test_a_balance_deal_ingested_later_does_not_demand_a_rebuild(db):
    """Trap 1 again: a non-trade deal reconstructs to nothing, so treating it
    as staleness would leave a warning `rebuild` can never clear."""
    conn, path = db
    _account(conn)
    _deal(conn, ticket=1, position_id=99, ingested_at=1)
    _trade(conn, position_id=99)
    _deal(conn, ticket=2, position_id=0, dtype=DealType.BALANCE, ingested_at=9_999)

    assert _by_name(health.checks(conn, path))["trades"].state == "ok"


def test_trades_ignores_non_trade_deals(db):
    """A balance/credit deal has position_id 0 and reconstructs to nothing —
    counting it would leave a permanent, unfixable warning (Trap 1)."""
    conn, path = db
    _account(conn)
    _deal(conn, ticket=1, position_id=0, dtype=DealType.BALANCE)

    assert _by_name(health.checks(conn, path))["trades"].state == "ok"


# --------------------------------------------------------------------- backup


def test_backup_warns_when_none_was_ever_taken(db):
    conn, path = db
    c = _by_name(health.checks(conn, path))["backup"]
    assert c.state == "warn"
    assert c.fix == "journal backup"


def test_backup_ok_on_a_snapshot_from_today(db, tmp_path):
    conn, path = db
    _snapshot(tmp_path)
    assert _by_name(health.checks(conn, path))["backup"].state == "ok"


def test_backup_warns_once_the_newest_snapshot_is_a_day_old(db, tmp_path):
    conn, path = db
    _snapshot(tmp_path, age_s=25 * 3600)
    c = _by_name(health.checks(conn, path))["backup"]
    assert c.state == "warn"


def test_backup_ignores_hand_named_files(db, tmp_path):
    """Same rule as `backup.due`: a file a human dropped in the folder is not
    this project's record of itself, and must not suppress the warning."""
    conn, path = db
    _snapshot(tmp_path, name="before-the-migration.db")
    assert _by_name(health.checks(conn, path))["backup"].state == "warn"


# ----------------------------------------------------------------------- live


def test_live_is_not_a_warning_when_the_daemon_has_never_run(db):
    """A journal with no daemon is a normal journal — most of this project's
    life is `sync` + `rebuild` by hand."""
    conn, path = db
    c = _by_name(health.checks(conn, path))["live"]
    assert c.state == "ok"
    assert "never" in c.detail


def test_live_warns_on_a_stale_heartbeat(db):
    conn, path = db
    now_s = 1_000_000.0
    conn.execute("INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?)",
                 (int(now_s * 1000) - 5 * 60_000,))
    conn.commit()

    c = _by_name(health.checks(conn, path, now=now_s))["live"]
    assert c.state == "warn"
    assert c.fix == "journal live"


def test_live_ok_on_a_fresh_heartbeat(db):
    conn, path = db
    now_s = 1_000_000.0
    conn.execute("INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?)",
                 (int(now_s * 1000) - 3_000,))
    conn.commit()

    assert _by_name(health.checks(conn, path, now=now_s))["live"].state == "ok"


def test_a_pending_command_with_no_daemon_is_a_warning(db):
    """A queued SL change nothing will ever send is worse than no daemon: the
    human believes the order is in flight."""
    conn, path = db
    _account(conn)
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, "
        "requested_msc, status) VALUES (?, 99, 'modify_sltp', 1, 'pending')",
        (_LOGIN,),
    )
    conn.commit()

    c = _by_name(health.checks(conn, path))["live"]
    assert c.state == "warn"
    assert "pending" in c.detail


def test_a_pending_command_under_a_live_daemon_is_just_in_flight(db):
    conn, path = db
    _account(conn)
    now_s = 1_000_000.0
    conn.execute("INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?)",
                 (int(now_s * 1000) - 1_000,))
    conn.execute(
        "INSERT INTO trade_commands (account_login, position_id, kind, "
        "requested_msc, status) VALUES (?, 99, 'modify_sltp', 1, 'pending')",
        (_LOGIN,),
    )
    conn.commit()

    c = _by_name(health.checks(conn, path, now=now_s))["live"]
    assert c.state == "ok"
    assert "pending" in c.detail


# ---------------------------------------------------------------- the contract


def test_checks_write_nothing(db):
    conn, path = db
    _account(conn)
    _deal(conn, ticket=1, position_id=99)
    before = path.read_bytes()

    health.checks(conn, path)

    assert path.read_bytes() == before


def test_every_non_ok_check_carries_a_command(db):
    conn, path = db
    for c in health.checks(conn, path):
        if c.state != "ok":
            assert c.fix, f"{c.name} is {c.state} with no way to fix it"


def test_a_corrupt_page_fails_the_check_that_hits_it_instead_of_the_command(tmp_path):
    # Measured 2026-08-13 against a deliberately corrupted 62 MB copy of the
    # live store: `journal status` died on a traceback out of `_balance` before
    # it printed a single line — on the one day this command exists for. A
    # malformed page can surface under ANY check, so the guard is at the choke
    # point, and every other check still gets to run and print.
    path = tmp_path / "t.db"
    conn = connect(path)
    _account(conn)
    for t in range(1, 300):
        _deal(conn, ticket=t, position_id=t)
    conn.commit()
    page = conn.execute("PRAGMA page_size").fetchone()[0]
    root = conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE name = 'deals_raw'").fetchone()[0]
    conn.close()

    # Scribble over the b-tree root of `deals_raw` itself: the file still opens,
    # and it is the checks that read deals which blow up. Located by rootpage,
    # not by a magic offset, so a schema change cannot quietly un-corrupt it.
    b = bytearray(path.read_bytes())
    for i in range((root - 1) * page + 16, root * page):
        b[i] ^= 0xFF
    path.write_bytes(bytes(b))

    conn = connect(path)
    try:
        results = health.checks(conn, path)
    finally:
        conn.close()

    assert [c.name for c in results] == ["integrity", "balance", "trades", "backup",
                                         "live", "frontend"]
    assert any(c.state == "fail" for c in results)
    for c in results:
        if c.state != "ok":
            assert c.fix


def test_live_warns_when_the_daemon_predates_the_newest_source_file(db, monkeypatch):
    """The bug this exists for: three features shipped in one week, each ending
    "needs a `journal live` RESTART", and nothing on the machine says the daemon
    is running last week's code."""
    conn, path = db
    now_s = 1_000_000.0
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc, started_msc) VALUES (1, ?, ?)",
        (int(now_s * 1000) - 3_000, int(now_s * 1000) - 6 * 3600 * 1000),
    )
    conn.commit()
    monkeypatch.setattr(health, "newest_source",
                        lambda: ("ingest/live.py", now_s - 3600))

    c = _by_name(health.checks(conn, path, now=now_s))["live"]
    assert c.state == "warn"
    assert "ingest/live.py" in c.detail
    assert c.fix and "live" in c.fix


def test_live_stays_ok_when_the_daemon_started_after_the_newest_edit(db, monkeypatch):
    conn, path = db
    now_s = 1_000_000.0
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc, started_msc) VALUES (1, ?, ?)",
        (int(now_s * 1000) - 3_000, int(now_s * 1000) - 60_000),
    )
    conn.commit()
    monkeypatch.setattr(health, "newest_source",
                        lambda: ("ingest/live.py", now_s - 3600))

    assert _by_name(health.checks(conn, path, now=now_s))["live"].state == "ok"


def test_live_says_nothing_about_code_age_when_the_daemon_never_recorded_a_start(db, monkeypatch):
    """A daemon from before this column existed: unknown start, no accusation."""
    conn, path = db
    now_s = 1_000_000.0
    conn.execute("INSERT INTO live_heartbeat (id, beat_msc) VALUES (1, ?)",
                 (int(now_s * 1000) - 3_000,))
    conn.commit()
    monkeypatch.setattr(health, "newest_source",
                        lambda: ("ingest/live.py", now_s - 3600))

    assert _by_name(health.checks(conn, path, now=now_s))["live"].state == "ok"


def test_newest_source_points_at_a_real_python_file():
    """No monkeypatch: the real scan must find this package's own sources."""
    name, mtime = health.newest_source()
    assert name.endswith(".py") and mtime > 0


# --------------------------------------------------- live: which code, not when


def _beating(conn, now_s, *, fingerprint):
    conn.execute(
        "INSERT INTO live_heartbeat (id, beat_msc, started_msc, code_fingerprint) "
        "VALUES (1, ?, ?, ?)",
        (int(now_s * 1000) - 3_000, int(now_s * 1000) - 6 * 3600 * 1000, fingerprint),
    )
    conn.commit()


def test_code_fingerprint_lists_this_package_only():
    fp = json.loads(health.code_fingerprint())
    assert "store/health.py" in fp
    assert all(k.endswith(".py") and not k.startswith("/") for k in fp)
    assert all(len(v) == 12 for v in fp.values())


def test_changed_modules_is_empty_against_an_untouched_tree():
    assert health.changed_modules(health.code_fingerprint()) == []


def test_changed_modules_names_a_file_whose_content_moved():
    fp = json.loads(health.code_fingerprint())
    fp["store/health.py"] = "0" * 12
    assert health.changed_modules(json.dumps(fp)) == ["store/health.py"]


def test_changed_modules_counts_a_vanished_file_as_changed():
    fp = json.loads(health.code_fingerprint())
    fp["store/no_such_module.py"] = "0" * 12
    assert "store/no_such_module.py" in health.changed_modules(json.dumps(fp))


def test_live_warns_when_a_module_the_daemon_loaded_changed_on_disk(db, monkeypatch):
    """The signal that replaces the mtime scan: WHICH file the daemon loaded and
    whether its bytes still match, not whether some unrelated file is newer."""
    conn, path = db
    now_s = 1_000_000.0
    fp = json.loads(health.code_fingerprint())
    fp["ingest/live.py"] = "0" * 12
    _beating(conn, now_s, fingerprint=json.dumps(fp))

    c = _by_name(health.checks(conn, path, now=now_s))["live"]
    assert c.state == "warn"
    assert "ingest/live.py" in c.detail and "OLD code" in c.detail
    assert c.fix and "live" in c.fix


def test_live_ignores_a_newer_file_the_daemon_never_loaded(db, monkeypatch):
    """The false positive this replaces: editing any `.py` under the package —
    a web view, an analytics module — accused a daemon that never imports it."""
    conn, path = db
    now_s = 1_000_000.0
    _beating(conn, now_s, fingerprint=health.code_fingerprint())
    monkeypatch.setattr(health, "newest_source",
                        lambda: ("web/views.py", now_s - 60))

    assert _by_name(health.checks(conn, path, now=now_s))["live"].state == "ok"


def test_live_falls_back_to_mtimes_when_the_daemon_left_no_fingerprint(db, monkeypatch):
    """A daemon started before this column existed still gets the old, coarser
    answer — silence would read as "current"."""
    conn, path = db
    now_s = 1_000_000.0
    _beating(conn, now_s, fingerprint=None)
    monkeypatch.setattr(health, "newest_source",
                        lambda: ("ingest/live.py", now_s - 3600))

    c = _by_name(health.checks(conn, path, now=now_s))["live"]
    assert c.state == "warn" and "ingest/live.py" in c.detail


# ------------------------------------------------------------------ frontend


def test_frontend_warns_when_the_bundle_is_behind_the_source(db, monkeypatch):
    """The other half of "am I running old code": `journal serve` mounts
    `frontend/dist` from disk and only says this at startup, in a terminal the
    human scrolled past days ago."""
    conn, path = db
    monkeypatch.setattr("journal.web.app.stale_dist_reason",
                        lambda: "frontend/dist is 2 file(s) behind the source "
                                "(newest: src/pages/Chart.tsx)")

    c = _by_name(health.checks(conn, path))["frontend"]
    assert c.state == "warn"
    assert "src/pages/Chart.tsx" in c.detail
    assert c.fix and "build" in c.fix


def test_frontend_ok_when_the_bundle_is_current(db, monkeypatch):
    conn, path = db
    monkeypatch.setattr("journal.web.app.stale_dist_reason", lambda: None)

    assert _by_name(health.checks(conn, path))["frontend"].state == "ok"


def test_a_stale_bundle_never_fails_the_exit_code(db, monkeypatch):
    """Undone, not wrong (§ the three states) — an unbuilt bundle serves an old
    page, it does not make a single number in this store untrue."""
    conn, path = db
    monkeypatch.setattr("journal.web.app.stale_dist_reason",
                        lambda: "frontend/dist is missing")

    assert not any(c.state == "fail" for c in health.checks(conn, path))
