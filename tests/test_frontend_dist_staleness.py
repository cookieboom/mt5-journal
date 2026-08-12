"""`stale_dist_reason` — the guard that catches a forgotten `npm run build`.

Every case is built from mtimes on a fake `frontend/` in `tmp_path`; nothing
here touches the repo's real `frontend/dist`, which may or may not be built.
"""
from __future__ import annotations

import os

from journal.web.app import stale_dist_reason

_OLD = 1_700_000_000
_NEW = 1_700_000_100


def _touch(path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _frontend(tmp_path, *, built_at: float | None = _NEW):
    """A frontend tree whose sources are all older than the bundle."""
    root = tmp_path / "frontend"
    _touch(root / "src" / "main.tsx", _OLD)
    _touch(root / "src" / "lib" / "candles.ts", _OLD)
    _touch(root / "index.html", _OLD)
    _touch(root / "package.json", _OLD)
    _touch(root / "vite.config.ts", _OLD)
    _touch(root / "tailwind.config.js", _OLD)
    if built_at is not None:
        _touch(root / "dist" / "index.html", built_at)
    return root


def test_fresh_build_is_silent(tmp_path):
    assert stale_dist_reason(_frontend(tmp_path)) is None


def test_missing_build_is_reported(tmp_path):
    root = _frontend(tmp_path, built_at=None)
    assert stale_dist_reason(root) == "frontend/dist is missing"


def test_newer_source_names_itself(tmp_path):
    root = _frontend(tmp_path)
    _touch(root / "src" / "lib" / "candles.ts", _NEW + 10)
    reason = stale_dist_reason(root)
    assert reason is not None
    assert "1 file(s) behind" in reason
    assert "src/lib/candles.ts" in reason


def test_newest_source_is_the_one_named(tmp_path):
    root = _frontend(tmp_path)
    _touch(root / "src" / "main.tsx", _NEW + 10)
    _touch(root / "src" / "lib" / "candles.ts", _NEW + 20)
    reason = stale_dist_reason(root)
    assert "2 file(s) behind" in reason
    assert "src/lib/candles.ts" in reason


def test_build_input_at_the_root_counts(tmp_path):
    """A dependency bump or a tailwind edit changes the bundle too."""
    root = _frontend(tmp_path)
    _touch(root / "package.json", _NEW + 10)
    assert "package.json" in stale_dist_reason(root)


def test_test_files_do_not_nag(tmp_path):
    """vitest files never reach the bundle — editing one is not a rebuild."""
    root = _frontend(tmp_path)
    _touch(root / "src" / "lib" / "candles.test.ts", _NEW + 10)
    assert stale_dist_reason(root) is None
