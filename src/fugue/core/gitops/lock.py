"""A lock on a branch, so two runs never work the same M's worktree at once."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pygit2
from pydantic import BaseModel, ConfigDict

from fugue.core.gitops.repository import Repository
from fugue.errors import RepositoryError


class LockHolder(BaseModel):
    """Who holds a lock: the process and the run, and since when."""

    model_config = ConfigDict(frozen=True)

    pid: int
    run_id: str
    since: datetime

    @property
    def alive(self) -> bool:
        """Whether the process that took the lock is still running."""
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # it exists, and is someone else's
            return True
        return True


class BranchLock:
    """One lock file per branch, in Git's own folder so it never shows up as a change.

    lock = BranchLock(repository, "feature/12-title")
    held = lock.holder()          # None, or who has it
    lock.take("run-id")           # RepositoryError if someone else holds it
    lock.release()                # only removes a lock this process took
    """

    def __init__(self, repository: Repository, branch: str) -> None:
        git_dir = Path(pygit2.Repository(str(repository.root)).path)
        self._path = git_dir / "fugue" / "locks" / f"{branch.replace('/', '-')}.lock"
        self._branch = branch

    def holder(self) -> LockHolder | None:
        """Who holds the lock, or None when nobody does."""
        try:
            return LockHolder.model_validate_json(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:  # a lock file nobody can read holds nothing
            return None

    def take(self, run_id: str, *, over: LockHolder | None = None) -> None:
        """Take the lock. `over` is a holder the person agreed to take it from (a dead run)."""
        current = self.holder()
        if current is not None and current != over and current.pid != os.getpid():
            raise RepositoryError(
                f"{self._branch} is being worked by run {current.run_id} (process {current.pid})"
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        mine = LockHolder(pid=os.getpid(), run_id=run_id, since=datetime.now(UTC))
        self._path.write_text(mine.model_dump_json(), encoding="utf-8")

    def release(self) -> None:
        """Give the lock up, if this process holds it."""
        current = self.holder()
        if current is not None and current.pid == os.getpid():
            self._path.unlink(missing_ok=True)
