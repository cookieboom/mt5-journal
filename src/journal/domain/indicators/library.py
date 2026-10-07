"""The shipped indicator scripts in `library/*.ind`: read-only, versioned in
git. Id is `lib:<file stem>`; the name is the first line's `# comment`."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DIR = Path(__file__).with_name("library")


@dataclass(frozen=True)
class LibraryScript:
    id: str
    name: str
    source: str


@cache
def library() -> dict[str, LibraryScript]:
    out: dict[str, LibraryScript] = {}
    for path in sorted(_DIR.glob("*.ind")):
        source = path.read_text()
        first = source.splitlines()[0] if source else ""
        name = first.lstrip("#").strip() if first.startswith("#") else path.stem
        out[f"lib:{path.stem}"] = LibraryScript(f"lib:{path.stem}", name, source)
    return out
