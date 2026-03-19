"""Repository scanning and import-header extraction for multi-file optimization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .languages import LanguageSpec

MAX_HEADER_LINES = 50


def scan_repo(root: Path, lang: "LanguageSpec") -> list[Path]:
    """Return all source files under root matching lang.extensions."""
    files: list[Path] = []
    for ext in lang.extensions:
        files.extend(root.rglob(f"*{ext}"))
    return sorted(set(files))


def read_import_header(path: Path, lines: int = MAX_HEADER_LINES) -> str:
    """Return first `lines` lines of a source file as a string."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:lines])


def build_repo_context(
    files: list[Path], root: Path, lines: int = MAX_HEADER_LINES
) -> dict[str, str]:
    """Return {relative_path_str: header_text} for every file."""
    result: dict[str, str] = {}
    for f in files:
        try:
            rel = str(f.relative_to(root))
        except ValueError:
            rel = str(f)
        result[rel] = read_import_header(f, lines)
    return result
