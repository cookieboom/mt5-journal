"""Rule 10, enforced: nothing tracked by git may carry a real identifier.

CLAUDE.md rule 10 — "never commit anything containing a real account login;
fixtures must be sanitised" — has been written down since M0 and has been
broken twice:

1. `tests/fixtures/deals.json` shipped a real funding reference in the deposit
   deal's `comment` (fixed in `7464753`).
2. The documents *describing* that leak then pasted the reference back in, in
   full, and stayed that way while the repository was public.

Both were found by a human reading a file for another reason. This module is
the guard, in the same shape as `test_frontend_constants.py`: a check nobody
has to remember to run.

Two layers, because they fail in different ways:

- `test_no_funding_reference_pattern` knows the *shape* of a funding reference
  and needs no database. It is what protects a clone that has no `data/`.
- `test_live_account_identifiers_absent` reads the real values out of the
  untracked `data/journal.db` and asserts they appear in no tracked file. It
  is the one with teeth — it cannot be fooled by a leak in a shape nobody
  predicted — and it is why no real identifier is written down here. It skips
  when the database is absent (a fresh clone, a worktree, CI).
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Funding references on this account look like `D-IDQRISGT-USC-9393629...`,
# `W-ALLINT-USC-INT-...`, `W-BANKIDGT-USC-...`: a direction letter, a scheme
# name, the account currency, an optional qualifier, and the transaction
# digits. Digits are required — a prefix on its own is not the leak.
FUNDING_REFERENCE = re.compile(r"\b[A-Z]-[A-Z]{4,}-[A-Z]{3}(?:-[A-Z]{2,})?-\d{6,}\b")

# Skip this file when scanning: it is the only tracked file allowed to talk
# about the shape of a reference.
SELF = Path(__file__).name

MAX_BYTES = 2_000_000


@lru_cache(maxsize=1)
def _tracked_text() -> tuple[tuple[str, str], ...]:
    """Every tracked text file as (repo-relative path, contents)."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = []
    for name in out.split("\0"):
        if not name or name.endswith(SELF):
            continue
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        blob = path.read_bytes()
        if b"\0" in blob:  # binary
            continue
        files.append((name, blob.decode("utf-8", errors="replace")))
    assert len(files) > 50, f"tracked-file scan found only {len(files)} files"
    return tuple(files)


def _hits(needle: str) -> list[str]:
    """Paths whose text contains `needle` as a standalone token.

    The boundaries matter for the login: a bare 6–9 digit number would
    otherwise match inside a hex hash in `uv.lock` and fail for no reason.
    """
    pattern = re.compile(rf"(?<![0-9A-Za-z]){re.escape(needle)}(?![0-9A-Za-z])")
    return [name for name, text in _tracked_text() if pattern.search(text)]


def test_no_funding_reference_pattern() -> None:
    """No tracked file carries anything shaped like a funding reference."""
    leaks = [
        f"{name}: {FUNDING_REFERENCE.search(text).group(0)}"  # type: ignore[union-attr]
        for name, text in _tracked_text()
        if FUNDING_REFERENCE.search(text)
    ]
    assert not leaks, "funding reference in tracked files:\n  " + "\n  ".join(leaks)


def test_fixture_account_is_sanitised() -> None:
    """The recorded account fixture keeps no identity of the real account."""
    import json

    account = json.loads((ROOT / "tests/fixtures/account.json").read_text())
    assert account["login"] == 0, "fixture login must be 0 (rule 10)"
    for field in ("company", "server", "name"):
        assert account[field] == "REDACTED", f"fixture {field} must be REDACTED"


def test_live_account_identifiers_absent() -> None:
    """Nothing tracked contains this account's login, broker, server, or any
    funding reference *as actually stored* — read from the untracked DB."""
    db = ROOT / "data" / "journal.db"
    if not db.exists():
        pytest.skip("no data/journal.db — nothing to compare against")

    # Read-only, and never `immutable=1`: `journal live` may be writing, and
    # immutable would tell SQLite to ignore the WAL it is writing into.
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:  # e.g. WAL with no readable -shm
        pytest.skip(f"cannot open {db} read-only: {exc}")
    try:
        secrets: set[str] = set()
        for row in con.execute("SELECT login, broker, server FROM accounts"):
            secrets.update(str(v) for v in row if v not in (None, "", 0))
        # Funding deals: everything that is not a trade (IN/OUT are 0/1).
        for (value,) in con.execute(
            "SELECT comment FROM deals_raw WHERE type NOT IN (0, 1) "
            "UNION SELECT external_id FROM deals_raw WHERE type NOT IN (0, 1)"
        ):
            if value and any(c.isdigit() for c in value):
                secrets.add(value)
    finally:
        con.close()

    assert secrets, "read no identifiers from the DB — the query is wrong"
    # The secret is never named, not even in the failure: pytest prints the
    # whole compared expression, so what is compared must already be redacted.
    # The paths are enough — `git grep` against the DB finds the rest.
    leaks = sorted(
        f"<{len(s)}-char identifier>: {', '.join(paths)}"
        for s in secrets
        if (paths := _hits(s))
    )
    assert not leaks, "tracked files carry live account identifiers:\n  " + "\n  ".join(
        leaks
    )
