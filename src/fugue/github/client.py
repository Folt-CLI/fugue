"""Read from GitHub."""

from githubkit import GitHub
from githubkit.exception import RequestFailed
from githubkit.utils import UNSET

from fugue.context import Context
from fugue.models import Ticket


class GitHubError(Exception):
    """GitHub refused or could not answer a request."""


async def read_ticket(context: Context, number: int) -> Ticket:
    async with GitHub(context.token) as github:
        try:
            response = await github.rest.issues.async_get(context.owner, context.name, number)
        except RequestFailed as error:
            status = error.response.status_code
            if status == 404:
                raise GitHubError(f"{context.slug}#{number} does not exist") from error
            if status in (401, 403):
                raise GitHubError(f"GitHub refused the token (HTTP {status})") from error
            raise GitHubError(f"GitHub answered HTTP {status}") from error
    issue = response.parsed_data
    if issue.pull_request is not UNSET:
        raise GitHubError(f"{context.slug}#{number} is a pull request, not a ticket")
    labels = tuple(
        label if isinstance(label, str) else (label.name or "") for label in issue.labels
    )
    return Ticket(
        number=issue.number,
        title=issue.title,
        body=issue.body or "",
        state="open" if issue.state == "open" else "closed",
        labels=tuple(label for label in labels if label),
        url=issue.html_url,
    )
