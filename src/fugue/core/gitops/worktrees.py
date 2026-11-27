"""Git worktrees: one checkout per ticket, made with pygit2."""

import asyncio
import contextlib
import shutil
from pathlib import Path

import pygit2
from pydantic import BaseModel, ConfigDict

from fugue.core.gitops.repository import Repository
from fugue.errors import RepositoryError


class Worktree(BaseModel):
    """A checkout of a branch in its own directory."""

    model_config = ConfigDict(frozen=True)

    path: Path
    branch: str
    base: str
    new_branch: bool
    reused: bool


class Worktrees:
    """Creates and removes the worktrees of one repository.

    `token` is used only to fetch over https; leave it out for a repository that needs none.
    """

    def __init__(self, repository: Repository, token: str | None = None) -> None:
        self._repository = repository
        self._token = token

    async def start(self, branch: str, base: str, path: Path) -> Worktree:
        """A worktree at `path` on `branch`, started from the latest `origin/<base>`.

        The branch is created when it does not exist yet. A worktree that is already there on
        that branch is reused, so a run can be resumed.
        """
        return await asyncio.to_thread(self._start, branch, base, path)

    async def remove(self, path: Path, *, branch: str | None = None, force: bool = False) -> None:
        """Remove the worktree at `path`, and delete `branch` when given.

        A worktree with uncommitted changes is refused unless `force` is set.
        """
        await asyncio.to_thread(self._remove, path, branch, force)

    # ----- blocking work, run off the event loop -----

    def _open(self) -> pygit2.Repository:
        return pygit2.Repository(str(self._repository.root))

    def _fetch(self, repo: pygit2.Repository, base: str) -> None:
        callbacks = (
            pygit2.RemoteCallbacks(credentials=pygit2.UserPass("x-access-token", self._token))
            if self._token
            else None
        )
        try:
            repo.remotes["origin"].fetch(
                [f"+refs/heads/{base}:refs/remotes/origin/{base}"], callbacks=callbacks
            )
        except (pygit2.GitError, KeyError) as error:
            raise RepositoryError(f"could not fetch origin/{base}: {error}") from error

    def _start(self, branch: str, base: str, path: Path) -> Worktree:
        repo = self._open()
        self._fetch(repo, base)
        try:
            start_point = repo.get(repo.lookup_reference(f"refs/remotes/origin/{base}").target)
        except KeyError:
            raise RepositoryError(f"origin has no branch {base!r}") from None

        existing = repo.lookup_branch(branch)
        if existing is not None:
            elsewhere = self._checked_out(repo, branch)
            if elsewhere is not None:
                return Worktree(
                    path=elsewhere, branch=branch, base=base, new_branch=False, reused=True
                )
        if path.exists():
            if self._is_worktree_of(path, branch):
                return Worktree(path=path, branch=branch, base=base, new_branch=False, reused=True)
            raise RepositoryError(f"{path} exists and is not a worktree on {branch}")

        created = existing is None
        target = existing or repo.create_branch(branch, start_point)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            repo.add_worktree(path.name, str(path), target)
        except pygit2.GitError as error:
            if created:
                target.delete()
            raise RepositoryError(f"could not add a worktree on {branch}: {error}") from error
        return Worktree(path=path, branch=branch, base=base, new_branch=created, reused=False)

    def _checked_out(self, repo: pygit2.Repository, branch: str) -> Path | None:
        """Where `branch` is already checked out, in the repository itself or a worktree."""
        root = self._repository.root
        if not repo.head_is_unborn and not repo.head_is_detached and repo.head.shorthand == branch:
            raise RepositoryError(f"{branch} is checked out in the repository itself, at {root}")
        for name in repo.list_worktrees():
            where = Path(repo.lookup_worktree(name).path)
            if where.exists() and self._is_worktree_of(where, branch):
                return where
        return None

    @staticmethod
    def _is_worktree_of(path: Path, branch: str) -> bool:
        try:
            return bool(pygit2.Repository(str(path)).head.shorthand == branch)
        except (pygit2.GitError, KeyError):
            return False

    def _remove(self, path: Path, branch: str | None, force: bool) -> None:
        repo = self._open()
        if path.exists():
            if not force and pygit2.Repository(str(path)).status():
                raise RepositoryError(f"{path} has uncommitted changes: commit or force")
            shutil.rmtree(path)
        with contextlib.suppress(KeyError, pygit2.GitError):
            repo.lookup_worktree(path.name).prune(True)
        if branch is not None:
            existing = repo.lookup_branch(branch)
            if existing is not None:
                existing.delete()
