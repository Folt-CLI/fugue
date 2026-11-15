"""Read the reference texts the analyst receives as data."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AnalysisContext:
    """Texts of the glossary and the issue format; each is empty when its file is missing."""

    glossary: str
    issue_format: str


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def read_context(repo_root: Path) -> AnalysisContext:
    """Read GLOSSARY.md and docs/dev/issue-format.md from the repository root."""
    return AnalysisContext(
        glossary=_read(repo_root / "GLOSSARY.md"),
        issue_format=_read(repo_root / "docs" / "dev" / "issue-format.md"),
    )
