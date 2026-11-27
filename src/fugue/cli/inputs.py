"""Reading what a command is given."""

from pathlib import Path


class Inputs:
    @staticmethod
    def body(text: str, file: Path | None) -> str:
        """The text from `--body`, or the contents of `--body-file` when one is given."""
        return file.read_text(encoding="utf-8") if file else text
