"""Issues: `github.issue`."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from githubkit.utils import UNSET

from fugue.errors import GitHubError
from fugue.models import Ticket

if TYPE_CHECKING:
    from githubkit_schemas.latest.models import Issue

    from fugue.github.github import Github


class Issues:
    def __init__(self, github: "Github") -> None:
        self._github = github

    async def get(self, number: int) -> Ticket:
        """Read issue `number`. A pull request is not a ticket and raises GitHubError."""
        repository = self._github.repository
        with self._github.guard(f"{repository.slug}#{number}"):
            response = await self._github.client.rest.issues.async_get(
                repository.owner, repository.name, number
            )
        if response.parsed_data.pull_request is not UNSET:
            raise GitHubError(f"{repository.slug}#{number} is a pull request, not a ticket")
        return self._to_ticket(response.parsed_data)

    async def create(self, title: str, body: str = "", *, labels: Sequence[str] = ()) -> Ticket:
        """Open a new issue and return it."""
        repository = self._github.repository
        with self._github.guard(f"a new issue in {repository.slug}"):
            response = await self._github.client.rest.issues.async_create(
                repository.owner, repository.name, title=title, body=body, labels=list(labels)
            )
        return self._to_ticket(response.parsed_data)

    def _to_ticket(self, issue: "Issue") -> Ticket:
        labels = (label if isinstance(label, str) else (label.name or "") for label in issue.labels)
        return Ticket(
            number=issue.number,
            title=issue.title,
            body=issue.body or "",
            state="open" if issue.state == "open" else "closed",
            labels=tuple(label for label in labels if label),
            url=issue.html_url,
        )
