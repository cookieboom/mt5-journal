"""Default DB/cache paths are anchored to the repo, not the cwd.

`journal serve` started from `frontend/` once opened a relative
`data/journal.db`, which sqlite3 silently CREATED empty — the dashboard
answered 400 "no account in the store" while the real store sat untouched."""

from pathlib import Path

from journal import cli
from journal.web.app import create_app

_REPO = Path(__file__).resolve().parents[1]


def test_cli_defaults_are_repo_anchored():
    assert Path(cli._DEFAULT_DB) == _REPO / "data" / "journal.db"


def test_web_defaults_ignore_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("JOURNAL_DB", raising=False)
    monkeypatch.delenv("JOURNAL_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    app = create_app()
    assert Path(app.state.db_path) == _REPO / "data" / "journal.db"
    assert Path(app.state.cache_dir) == _REPO / "cache"
