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

    def _callbacks(self) -> pygit2.RemoteCallbacks | None:
        if not self._token:
            return None
        return pygit2.RemoteCallbacks(credentials=pygit2.UserPass("x-access-token", self._token))

    def _fetch(self, repo: pygit2.Repository, base: str) -> None:
        try:
            repo.remotes["origin"].fetch(
                [f"+refs/heads/{base}:refs/remotes/origin/{base}"], callbacks=self._callbacks()
            )
        except (pygit2.GitError, KeyError) as error:
            raise RepositoryError(f"could not fetch origin/{base}: {error}") from error

    def _fetch_branch(self, repo: pygit2.Repository, branch: str) -> pygit2.Commit | None:
        """The latest commit of `origin/<branch>`, or None when origin has no such branch."""
        name = f"refs/remotes/origin/{branch}"
        try:
            repo.remotes["origin"].fetch(
                [f"+refs/heads/{branch}:{name}"], callbacks=self._callbacks()
            )
        except KeyError as error:
            raise RepositoryError(f"could not fetch origin/{branch}: {error}") from error
        except pygit2.GitError as error:
            if "couldn't find remote ref" not in str(error):
                raise RepositoryError(f"could not fetch origin/{branch}: {error}") from error
        if name not in repo.references:
            return None
        found = repo.get(repo.lookup_reference(name).target)
        if found is None:
            return None
        return found  # type: ignore[return-value]

    def _start(self, branch: str, base: str, path: Path) -> Worktree:
        repo = self._open()
        self._fetch(repo, base)
        remote = self._fetch_branch(repo, branch)
        try:
            start_point = repo.get(repo.lookup_reference(f"refs/remotes/origin/{base}").target)
        except KeyError:
            raise RepositoryError(f"origin has no branch {base!r}") from None

        existing = repo.lookup_branch(branch)
        where = self._where(repo, branch, path)
        if where is not None:
            if remote is not None:
                self._bring_up_to_date(where, branch, remote)
            return Worktree(path=where, branch=branch, base=base, new_branch=False, reused=True)

        created = existing is None
        if existing is None:
            target = repo.create_branch(branch, remote or start_point)
        else:
            target = existing
            if remote is not None:
                self._move_branch(repo, existing, remote)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            repo.add_worktree(branch.replace("/", "-"), str(path), target)
        except pygit2.GitError as error:
            if created:
                target.delete()
            raise RepositoryError(f"could not add a worktree on {branch}: {error}") from error
        return Worktree(path=path, branch=branch, base=base, new_branch=created, reused=False)

    def _where(self, repo: pygit2.Repository, branch: str, path: Path) -> Path | None:
        """The worktree the branch is already checked out in, or None when there is none."""
        if repo.lookup_branch(branch) is not None:
            elsewhere = self._checked_out(repo, branch)
            if elsewhere is not None:
                return elsewhere
        if path.exists():
            if self._is_worktree_of(path, branch):
                return path
            raise RepositoryError(f"{path} exists and is not a worktree on {branch}")
        return None

    def _bring_up_to_date(self, path: Path, branch: str, remote: pygit2.Commit) -> None:
        """Reset the worktree to `origin/<branch>`. Local changes are never thrown away: a
        worktree with uncommitted changes, or a branch with commits origin does not have, is
        refused."""
        inside = pygit2.Repository(str(path))
        if inside.status():
            raise RepositoryError(
                f"{path} has uncommitted changes: commit or discard them, then run again"
            )
        head = inside.head.target
        if head == remote.id:
            return
        if not inside.descendant_of(remote.id, head):
            raise RepositoryError(
                f"{branch} has commits that origin/{branch} does not have, or has diverged "
                f"from it: push or sort them out in {path}, then run again"
            )
        inside.reset(remote.id, pygit2.enums.ResetMode.HARD)

    def _move_branch(
        self, repo: pygit2.Repository, existing: pygit2.Branch, remote: pygit2.Commit
    ) -> None:
        """Move a local branch that has no worktree to `origin/<branch>`, when that is only
        forward."""
        head = existing.target
        if head == remote.id:
            return
        if not repo.descendant_of(remote.id, head):
            raise RepositoryError(
                f"{existing.branch_name} has commits that origin/{existing.branch_name} does not "
                f"have, or has diverged from it: sort that out first"
            )
        existing.set_target(remote.id)

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
        for name in repo.list_worktrees():
            worktree = repo.lookup_worktree(name)
            if Path(worktree.path) == path:
                with contextlib.suppress(pygit2.GitError):
                    worktree.prune(True)
        if branch is not None:
            existing = repo.lookup_branch(branch)
            if existing is not None:
                existing.delete()
