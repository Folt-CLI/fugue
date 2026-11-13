"""The GitHub operations consumed by the engine."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from fugue.github import CheckResult, PullRequest
from fugue.models import Status, Ticket


class GitHubPort(Protocol):
    repo: str

    async def get_ticket(self, number: int) -> Ticket: ...

    async def set_status(self, number: int, status: Status) -> None: ...

    async def comment(self, number: int, body: str) -> None: ...

    async def find_pr(self, branch: str) -> PullRequest | None: ...

    async def create_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        assignee: str,
        milestone: str,
        project_title: str,
    ) -> PullRequest: ...

    async def download_attachment(self, url: str, dest: Path) -> Path: ...


@runtime_checkable
class CiGitHubPort(GitHubPort, Protocol):
    """The GitHub operations needed to monitor and repair continuous integration."""

    async def checks(self, pr: PullRequest) -> list[CheckResult]: ...

    async def failed_log_tail(self, run_link: str, max_lines: int = 200) -> str: ...

    async def rerun_failed(self, run_link: str) -> None: ...
