"""Atomic, private interview record storage."""

import contextlib
import os
import re
import tempfile
from pathlib import Path

from fugue.interview.record import InterviewRecord


class InterviewStore:
    def __init__(self, state_dir: Path) -> None:
        self.directory = state_dir / "interviews"

    def path(self, repo: str, ticket: int) -> Path:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo)
            or ticket <= 0
        ):
            raise ValueError("Invalid interview repository or ticket")
        return self.directory / f"{repo.replace('/', '__')}__{ticket}.json"

    def load(self, repo: str, ticket: int) -> InterviewRecord | None:
        target = self.path(repo, ticket)
        if not target.exists():
            return None
        record = InterviewRecord.model_validate_json(target.read_text(encoding="utf-8"))
        if record.repo != repo or record.ticket != ticket:
            raise ValueError("Interview record names another ticket")
        return record

    def save(self, record: InterviewRecord) -> Path:
        target = self.path(record.repo, record.ticket)
        self.directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(record.model_dump_json(indent=2))
            os.replace(temporary, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return target

    def finished(self, repo: str, ticket: int) -> InterviewRecord | None:
        record = self.load(repo, ticket)
        return record if record is not None and record.status == "proceeded" else None
