"""Native stack operations; unavailable features never fall back to plain PRs."""

import json
import re
import tempfile
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

    async def probe(self, repo: str | None = None) -> StackProbe:
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
        if enabled.returncode not in (0, 2):
            raise GitHubError(enabled.argv, enabled.stderr)
        # Local tracking can be absent even when repository support is enabled.
        if repo is None:
            resolved = await self._result(
                "gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
            )
            if resolved.returncode != 0:
                raise GitHubError(resolved.argv, resolved.stderr)
            repo = resolved.stdout.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Stack probe requires an explicit owner/repository")
        support = await self._result("gh", "api", f"repos/{repo}/stacks?per_page=1", "--include")
        status = re.search(r"^HTTP/\S+ (\d{3})\b", support.stdout)
        if status is not None and status[1] == "404":
            return StackProbe(False, "feature_disabled")
        if support.returncode != 0 or status is None or status[1] != "200":
            raise GitHubError(support.argv, support.stderr or "Cannot establish stack availability")
        return StackProbe(True)

    async def init(self, base: str, branches: Sequence[str]) -> str:
        return await self._run("init", "--base", base, *branches)

    async def add(self, branch: str) -> str:
        return await self._run("add", branch)

    async def link(self, stack_number_or_branches: str | Sequence[str], branch: str) -> str:
        existing = (
            [stack_number_or_branches]
            if isinstance(stack_number_or_branches, str)
            else stack_number_or_branches
        )
        return await self._run("link", *existing, branch)

    async def _tracking_path(self) -> Path:
        result = await self._result(
            "git", "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        if result.returncode:
            raise GitHubError(result.argv, result.stderr)
        return Path(result.stdout.strip()) / "gh-stack"

    async def find_stack(self, branch: str) -> str | list[str] | None:
        """Find a tracked layer in shared state, including from a new worktree."""
        path = await self._tracking_path()
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        if not isinstance(state, dict) or not isinstance(state.get("stacks"), list):
            raise ValueError("Invalid tracked stack state")
        matches: list[str | list[str]] = []
        for stack in state["stacks"]:
            if not isinstance(stack, dict) or not isinstance(stack.get("branches"), list):
                raise ValueError("Invalid tracked stack branches")
            branches: list[str] = []
            for layer in stack["branches"]:
                if not isinstance(layer, dict) or not isinstance(layer.get("branch"), str):
                    raise ValueError("Invalid tracked stack branch")
                branches.append(layer["branch"])
            if branch in branches:
                number = stack.get("number")
                matches.append(str(number) if type(number) is int and number > 0 else branches)
        if len(matches) > 1:
            raise ValueError(f"Branch {branch!r} belongs to multiple tracked stacks")
        return matches[0] if matches else None

    async def track(self, lower: str, branch: str) -> None:
        """Track a linked layer locally; caller holds the repository lock."""
        if await self.find_stack(lower) is None:
            raise ValueError(f"Lower branch {lower!r} has no tracked stack")
        path = await self._tracking_path()
        state = json.loads(path.read_text())
        stack = next(
            stack
            for stack in state["stacks"]
            if any(layer["branch"] == lower for layer in stack["branches"])
        )
        branches = [layer["branch"] for layer in stack["branches"]]
        if branch in branches:
            if branches.index(branch) != branches.index(lower) + 1:
                raise ValueError("Linked branch is not directly above the lower branch")
            return
        if await self.find_stack(branch) is not None:
            raise ValueError(f"Branch {branch!r} belongs to another tracked stack")
        if branches[-1] != lower:
            raise ValueError("The lower branch is not the top tracked layer")
        heads: list[str] = []
        for name in (lower, branch):
            result = await self._result("git", "rev-parse", "--verify", f"refs/heads/{name}")
            if result.returncode or not result.stdout.strip():
                raise GitHubError(
                    result.argv, result.stderr or "Cannot resolve tracked branch head"
                )
            heads.append(result.stdout.strip())
        stack["branches"].append({"branch": branch, "head": heads[1], "base": heads[0]})
        with tempfile.TemporaryDirectory(dir=path.parent, prefix=".gh-stack-") as directory:
            temporary = Path(directory) / "state"
            temporary.write_text(json.dumps(state, indent=2) + "\n")
            temporary.replace(path)

    async def submit(self) -> str:
        return await self._run("submit")

    async def view(self) -> str | None:
        """Return tracked stack state, or None for the native not-in-stack exit code."""
        result = await self._result("gh", "stack", "view")
        if result.returncode == 2:
            return None
        if result.returncode != 0:
            raise GitHubError(result.argv, result.stderr or f"exit code {result.returncode}")
        return result.stdout

    async def merge(self, pr_number: int) -> str:
        """Merge the entire native stack atomically using merge commits."""
        if pr_number <= 0:
            raise ValueError("A positive pull request number is required")
        return await self._run("merge", str(pr_number), "--merge", "--yes")

    async def tracked_branches(self, branch: str) -> list[str]:
        """Read every locally tracked layer before authorizing a whole-stack merge."""
        state = json.loads((await self._tracking_path()).read_text())
        matches = [
            [layer["branch"] for layer in stack["branches"]]
            for stack in state["stacks"]
            if any(layer["branch"] == branch for layer in stack["branches"])
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one tracked stack")
        return list(matches[0])

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

    async def ready_pr(self, repo: str, branch: str) -> None:
        result = await self._result(
            "gh", "pr", "view", branch, "--repo", repo, "--json", "isDraft", "--jq", ".isDraft"
        )
        if result.returncode or result.stdout.strip() not in ("true", "false"):
            raise GitHubError(
                result.argv, result.stderr or "Cannot establish pull request readiness"
            )
        if result.stdout.strip() == "false":
            return
        result = await self._result("gh", "pr", "ready", branch, "--repo", repo)
        if result.returncode:
            raise GitHubError(result.argv, result.stderr)


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
