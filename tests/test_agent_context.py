"""Tests for the analyst's reference texts."""

from pathlib import Path

from fugue.analysis.context import AnalysisContext, read_context


def test_reads_both_files(tmp_path: Path) -> None:
    (tmp_path / "docs" / "dev").mkdir(parents=True)
    (tmp_path / "GLOSSARY.md").write_text("glossary text", encoding="utf-8")
    (tmp_path / "docs" / "dev" / "issue-format.md").write_text("format text", encoding="utf-8")
    assert read_context(tmp_path) == AnalysisContext("glossary text", "format text")


def test_missing_files_are_empty(tmp_path: Path) -> None:
    assert read_context(tmp_path) == AnalysisContext("", "")


def test_one_missing_file_keeps_the_other(tmp_path: Path) -> None:
    (tmp_path / "GLOSSARY.md").write_text("glossary text", encoding="utf-8")
    assert read_context(tmp_path) == AnalysisContext("glossary text", "")


def test_unreadable_file_is_empty(tmp_path: Path) -> None:
    (tmp_path / "GLOSSARY.md").write_bytes(b"\xff\xfe\x00bad")
    assert read_context(tmp_path).glossary == ""
