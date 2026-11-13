"""Process-held ticket locks with diagnostic ownership metadata."""

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self


@dataclass(frozen=True)
class LockMetadata:
    pid: int
    created_at: datetime

    @property
    def age_seconds(self) -> float:
        return max(0.0, (datetime.now(UTC) - self.created_at).total_seconds())


class TicketLocked(RuntimeError):
    """Another run already owns this ticket."""


class TicketLock:
    """Lock one repository ticket until context exit or process termination.

    Files remain after release so contenders always lock the same inode. Old
    metadata does not imply ownership: the operating system holds the lock.
    """

    def __init__(self, state_dir: Path, repo: str, ticket_number: int) -> None:
        repository = hashlib.sha256(repo.strip().casefold().encode()).hexdigest()
        self.path = state_dir / "locks" / repository / f"{ticket_number}.lock"
        self._file: IO[str] | None = None

    def __enter__(self) -> Self:
        if self._file is not None:
            raise RuntimeError("Ticket lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                handle.seek(0)
                owner = handle.read().strip()
                raise TicketLocked(f"Ticket already locked at {self.path}: {owner}") from error
            metadata = LockMetadata(os.getpid(), datetime.now(UTC))
            handle.seek(0)
            handle.truncate()
            json.dump({"pid": metadata.pid, "created_at": metadata.created_at.isoformat()}, handle)
            handle.flush()
        except BaseException:
            handle.close()
            raise
        self._file = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
