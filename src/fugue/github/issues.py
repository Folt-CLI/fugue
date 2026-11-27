"""Issues: `github.issue`."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from githubkit.utils import UNSET

from fugue.github.errors import GitHubError, translate
from fugue.models import Ticket

if TYPE_CHECKING:
    from githubkit_schemas.latest.models import Issue

    from fugue.github.github import Github


def _ticket(issue: "Issue") -> Ticket:
    labels = (label if isinstance(label, str) else (label.name or "") for label in issue.labels)
    return Ticket(
        number=issue.number,
        title=issue.title,
        body=issue.body or "",
        state="open" if issue.state == "open" else "closed",
        labels=tuple(label for label in labels if label),
        url=issue.html_url,
    )


class Issues:
    def __init__(self, github: "Github") -> None:
        self._github = github

    async def get(self, number: int) -> Ticket:
        """Read issue `number`. A pull request is not a ticket and raises GitHubError."""
        slug = self._github.slug
        with translate(f"{slug}#{number}"):
            response = await self._github.client.rest.issues.async_get(
                self._github.context.owner, self._github.context.name, number
            )
        if response.parsed_data.pull_request is not UNSET:
            raise GitHubError(f"{slug}#{number} is a pull request, not a ticket")
        return _ticket(response.parsed_data)

    async def create(self, title: str, body: str = "", *, labels: Sequence[str] = ()) -> Ticket:
        """Open a new issue and return it."""
        with translate(f"a new issue in {self._github.slug}"):
            response = await self._github.client.rest.issues.async_create(
                self._github.context.owner,
                self._github.context.name,
                title=title,
                body=body,
                labels=list(labels),
            )
        return _ticket(response.parsed_data)
