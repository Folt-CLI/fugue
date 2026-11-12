"""The GitHub operations consumed by the engine."""

from pathlib import Path
from typing import Protocol

from fugue.github import PullRequest
from fugue.models import Status, Ticket


class GitHubPort(Protocol):
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
