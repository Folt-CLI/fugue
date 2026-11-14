"""Native stack operations; unavailable features never fall back to plain PRs."""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from fugue.github.client import GitHubError, PullRequest
from fugue.models import Ticket
from fugue.process import CommandResult, run_command

StackReason = Literal["gh_too_old", "extension_missing", "git_too_old", "feature_disabled"]


@dataclass(frozen=True)
class StackProbe:
    available: bool
    reason: StackReason | None = None


class StackClient:
    def __init__(
        self,
        cwd: Path,
        *,
        runner: Callable[..., Awaitable[CommandResult]] = run_command,
        timeout: float = 60,
    ) -> None:
        self.cwd = cwd
        self.runner = runner
        self.timeout = timeout

    async def _result(self, *argv: str) -> CommandResult:
        result = await self.runner(argv, cwd=self.cwd, timeout=self.timeout)
        if result.timed_out or result.cancelled or result.stdout_truncated:
            raise GitHubError(argv, result.stderr or "Stack command incomplete")
        return result

    async def _run(self, *args: str) -> str:
        result = await self._result("gh", "stack", *args)
        if result.returncode != 0:
            raise GitHubError(result.argv, result.stderr or f"exit code {result.returncode}")
        return result.stdout

    async def probe(self) -> StackProbe:
        checks: tuple[tuple[tuple[str, ...], tuple[int, ...], StackReason], ...] = (
            (("gh", "--version"), (2, 90, 0), "gh_too_old"),
            (("git", "--version"), (2, 36, 0), "git_too_old"),
        )
        for argv, minimum, reason in checks:
            try:
                result = await self._result(*argv)
            except FileNotFoundError:
                return StackProbe(False, reason)
            match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", result.stdout)
            if (
                result.returncode
                or not match
                or tuple(int(v or 0) for v in match.groups()) < minimum
            ):
                return StackProbe(False, reason)
        extensions = await self._result("gh", "extension", "list")
        if extensions.returncode or "github/gh-stack" not in extensions.stdout.split():
            return StackProbe(False, "extension_missing")
        enabled = await self._result("gh", "stack", "view")
        if enabled.returncode == 9:
            return StackProbe(False, "feature_disabled")
        if enabled.returncode != 0:
            raise GitHubError(enabled.argv, enabled.stderr)
        return StackProbe(True)

    async def init(self, base: str, branches: Sequence[str]) -> str:
        return await self._run("init", "--base", base, *branches)

    async def add(self, branch: str) -> str:
        return await self._run("add", branch)

    async def submit(self) -> str:
        return await self._run("submit")

    async def view(self) -> str:
        return await self._run("view")

    async def rebase(self) -> str:
        return await self._run("rebase")

    async def push(self) -> str:
        return await self._run("push")

    async def edit_pr(
        self, repo: str, branch: str, title: str, body: str, assignee: str, milestone: str
    ) -> None:
        argv = (
            "gh",
            "pr",
            "edit",
            branch,
            "--repo",
            repo,
            "--title",
            title,
            "--body",
            body,
            "--add-assignee",
            assignee,
            "--milestone",
            milestone,
            "--add-project",
            "Fugue",
        )
        result = await self._result(*argv)
        if result.returncode:
            raise GitHubError(argv, result.stderr)


class StackGitHubPort(Protocol):
    async def get_ticket(self, number: int) -> Ticket: ...
    async def find_pr(self, branch: str) -> PullRequest | None: ...


async def lower_branch(ticket: Ticket, github: StackGitHubPort) -> str | None:
    """Resolve the sole open blocker to its open PR branch."""
    if len(ticket.blocked_by) != 1:
        return None
    from fugue.engine.facts import branch_name

    lower = await github.get_ticket(ticket.blocked_by[0])
    branch = branch_name(lower)
    return branch if await github.find_pr(branch) is not None else None
