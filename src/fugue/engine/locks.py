"""Process-held ticket and repository locks with diagnostic ownership metadata."""

import asyncio
import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self

_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class LockMetadata:
    pid: int
    created_at: datetime

    @property
    def age_seconds(self) -> float:
        return max(0.0, (datetime.now(UTC) - self.created_at).total_seconds())


def format_elapsed(seconds: float) -> str:
    """Format a duration as `1h 02m 03s`, `12m 03s` or `5s`."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _parse_metadata(text: str) -> LockMetadata | None:
    try:
        data = json.loads(text)
        created_at = datetime.fromisoformat(data["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return LockMetadata(int(data["pid"]), created_at)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def read_metadata(path: Path) -> LockMetadata | None:
    """Read lock owner metadata; an empty, missing or corrupt file reads as None."""
    try:
        return _parse_metadata(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def pid_alive(pid: int) -> bool:
    """Report whether a process with this id exists on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_metadata(handle: IO[str]) -> LockMetadata:
    metadata = LockMetadata(os.getpid(), datetime.now(UTC))
    handle.seek(0)
    handle.truncate()
    json.dump({"pid": metadata.pid, "created_at": metadata.created_at.isoformat()}, handle)
    handle.flush()
    return metadata


class TicketLocked(RuntimeError):
    """Another run already owns this ticket."""

    def __init__(
        self,
        message: str,
        *,
        pid: int | None = None,
        age_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.pid = pid
        self.age_seconds = age_seconds


class TicketLock:
    """Lock one repository ticket until context exit or process termination.

    Files remain after release so contenders always lock the same inode. Old
    metadata does not imply ownership: the operating system holds the lock.
    """

    def __init__(self, state_dir: Path, repo: str, ticket_number: int) -> None:
        repository = hashlib.sha256(repo.strip().casefold().encode()).hexdigest()
        self.repo = repo
        self.ticket_number = ticket_number
        self.path = state_dir / "locks" / repository / f"{ticket_number}.lock"
        self.recovered: LockMetadata | None = None
        self._file: IO[str] | None = None

    def _conflict(self) -> TicketLocked:
        metadata = read_metadata(self.path)
        number = self.ticket_number
        if metadata is None:
            return TicketLocked(
                f"ticket #{number} is already being run by another process; lock file {self.path}"
            )
        elapsed = format_elapsed(metadata.age_seconds)
        if pid_alive(metadata.pid):
            message = (
                f"ticket #{number} is already being run by process {metadata.pid} "
                f"(running {elapsed}); lock file {self.path}"
            )
        else:
            message = (
                f"ticket #{number}: the recorded process {metadata.pid} is not running "
                f"but still holds the lock (recorded {elapsed} ago); stop any remaining "
                f"processes holding the file (for example `fuser {self.path}`) and do "
                f"not delete it; lock file {self.path}"
            )
        return TicketLocked(message, pid=metadata.pid, age_seconds=metadata.age_seconds)

    def __enter__(self) -> Self:
        if self._file is not None:
            raise RuntimeError("Ticket lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise self._conflict() from error
            handle.seek(0)
            prior = _parse_metadata(handle.read())
            if prior is not None and not pid_alive(prior.pid):
                self.recovered = prior
            _write_metadata(handle)
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
            try:
                self._file.seek(0)
                self._file.truncate()
                self._file.flush()
            finally:
                self._file.close()
                self._file = None


class RepoLock:
    """Cross-process lock around the shared Git steps in the primary checkout.

    Waiting polls without blocking so cancellation is clean. The operating
    system releases the lock when a holder dies. Not re-entrant.
    """

    def __init__(self, state_dir: Path, repo_root: Path) -> None:
        digest = hashlib.sha256(str(repo_root.resolve()).encode()).hexdigest()
        self.path = state_dir / "locks" / f"repository-{digest}.lock"
        self._file: IO[str] | None = None

    async def __aenter__(self) -> Self:
        if self._file is not None:
            raise RuntimeError("Repository lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(_POLL_SECONDS)
            _write_metadata(handle)
        except BaseException:
            handle.close()
            raise
        self._file = handle
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            handle, self._file = self._file, None
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
