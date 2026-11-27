"""Issues: `github.issue`."""

from collections.abc import Sequence
from typing import Any, Literal

from githubkit.utils import UNSET

from fugue.core.github.area import Area
from fugue.errors import GitHubError
from fugue.models import Comment, Ticket


class Issues(Area):
    async def get(self, number: int) -> Ticket:
        """Read issue `number`. A pull request is not a ticket and raises GitHubError."""
        with self._github.guard(f"{self._slug}#{number}"):
            response = await self._client.rest.issues.async_get(self._owner, self._name, number)
        if response.parsed_data.pull_request is not UNSET:
            raise GitHubError(f"{self._slug}#{number} is a pull request, not a ticket")
        return self._to_ticket(response.parsed_data)

    async def list(
        self,
        *,
        state: Literal["open", "closed", "all"] = "open",
        labels: Sequence[str] = (),
        limit: int = 30,
    ) -> Sequence[Ticket]:
        """Issues of the repository, newest activity first. Pull requests are left out."""
        found: list[Ticket] = []
        page = 1
        while len(found) < limit:
            with self._github.guard(f"issues of {self._slug}"):
                response = await self._client.rest.issues.async_list_for_repo(
                    self._owner,
                    self._name,
                    state=state,
                    labels=",".join(labels) if labels else UNSET,
                    per_page=self.MAX_LIMIT,
                    page=page,
                )
            issues = self._each(response.parsed_data)
            # GitHub lists pull requests as issues, so a page can hold none: keep paging.
            found += [self._to_ticket(i) for i in issues if i.pull_request is UNSET]
            if len(issues) < self.MAX_LIMIT:
                break
            page += 1
        return found[:limit]

    async def create(self, title: str, body: str = "", *, labels: Sequence[str] = ()) -> Ticket:
        """Open a new issue and return it."""
        with self._github.guard(f"a new issue in {self._slug}"):
            response = await self._client.rest.issues.async_create(
                self._owner, self._name, title=title, body=body, labels=list(labels)
            )
        return self._to_ticket(response.parsed_data)

    async def edit(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        labels: Sequence[str] | None = None,
    ) -> Ticket:
        """Change an issue's title, body or labels. What is left out stays as it is."""
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if body is not None:
            changes["body"] = body
        if labels is not None:
            changes["labels"] = list(labels)
        if not changes:
            raise GitHubError("nothing to change: give a title, a body or labels")
        return await self._update(number, changes)

    async def close(self, number: int, *, not_planned: bool = False) -> Ticket:
        """Close an issue as completed, or as not planned."""
        reason = "not_planned" if not_planned else "completed"
        return await self._update(number, {"state": "closed", "state_reason": reason})

    async def reopen(self, number: int) -> Ticket:
        """Reopen a closed issue."""
        return await self._update(number, {"state": "open"})

    async def comment(self, number: int, body: str) -> Comment:
        """Add a comment to an issue or a pull request."""
        with self._github.guard(f"{self._slug}#{number}"):
            response = await self._client.rest.issues.async_create_comment(
                self._owner, self._name, number, body=body
            )
        return self._to_comment(response.parsed_data)

    async def comments(self, number: int, *, limit: int = 30) -> Sequence[Comment]:
        """The comments on an issue or a pull request, oldest first."""
        with self._github.guard(f"{self._slug}#{number}"):
            response = await self._client.rest.issues.async_list_comments(
                self._owner, self._name, number, per_page=self._page(limit)
            )
        return [self._to_comment(comment) for comment in self._each(response.parsed_data)][:limit]

    async def _update(self, number: int, changes: dict[str, Any]) -> Ticket:
        with self._github.guard(f"{self._slug}#{number}"):
            response = await self._client.rest.issues.async_update(
                self._owner, self._name, number, **changes
            )
        if response.parsed_data.pull_request is not UNSET:
            raise GitHubError(f"{self._slug}#{number} is a pull request, not a ticket")
        return self._to_ticket(response.parsed_data)

    def _to_ticket(self, issue: Any) -> Ticket:
        labels = (label if isinstance(label, str) else (label.name or "") for label in issue.labels)
        return Ticket(
            number=issue.number,
            title=issue.title,
            body=issue.body or "",
            state="open" if issue.state == "open" else "closed",
            labels=tuple(label for label in labels if label),
            author=issue.user.login if issue.user else "",
            comments=issue.comments,
            updated_at=issue.updated_at,
            url=issue.html_url,
        )

    def _to_comment(self, comment: Any) -> Comment:
        return Comment(
            id=comment.id,
            author=comment.user.login if comment.user else "",
            body=comment.body or "",
            created_at=comment.created_at,
            url=comment.html_url,
        )
