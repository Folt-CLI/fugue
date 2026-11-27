"""Pull requests: `github.pr`."""

from collections.abc import Sequence
from typing import Any, Literal

from githubkit.utils import UNSET

from fugue.core.github.area import Area
from fugue.errors import GitHubError
from fugue.models import Check, Comment, PullRequest


class PullRequests(Area):
    async def get(self, number: int) -> PullRequest:
        """Read pull request `number`."""
        with self._github.guard(f"{self._slug}#{number}"):
            response = await self._client.rest.pulls.async_get(self._owner, self._name, number)
        return self._to_pull_request(response.parsed_data)

    async def list(
        self,
        *,
        state: Literal["open", "closed", "all"] = "open",
        base: str | None = None,
        limit: int = 30,
    ) -> Sequence[PullRequest]:
        """Pull requests of the repository, newest first."""
        with self._github.guard(f"pull requests of {self._slug}"):
            response = await self._client.rest.pulls.async_list(
                self._owner,
                self._name,
                state=state,
                base=base if base else UNSET,
                per_page=self._page(limit),
            )
        return [self._to_pull_request(pull) for pull in self._each(response.parsed_data)][:limit]

    async def resolve(self, head: str | None = None, base: str | None = None) -> tuple[str, str]:
        """The branches a pull request would use: (head, base).

        The head defaults to the checked-out branch and the base to the repository's default
        branch. A pull request from a branch into itself is refused.
        """
        source = head or self._github.repository.branch
        if not source:
            raise GitHubError("no branch to open from: check one out or give a head")
        target = base or (await self._github.repo.info()).default_branch
        if source == target:
            raise GitHubError(f"{source} is the base branch: check out a branch or give a head")
        return source, target

    async def create(
        self,
        title: str,
        body: str = "",
        *,
        head: str | None = None,
        base: str | None = None,
        draft: bool = False,
    ) -> PullRequest:
        """Open a pull request. See `resolve` for the default branches."""
        source, target = await self.resolve(head, base)
        with self._github.guard(f"a new pull request in {self._slug}"):
            response = await self._client.rest.pulls.async_create(
                self._owner,
                self._name,
                title=title,
                head=source,
                base=target,
                body=body,
                draft=draft,
            )
        return self._to_pull_request(response.parsed_data)

    async def edit(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        base: str | None = None,
    ) -> PullRequest:
        """Change a pull request's title, body or base branch."""
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if body is not None:
            changes["body"] = body
        if base is not None:
            changes["base"] = base
        if not changes:
            raise GitHubError("nothing to change: give a title, a body or a base")
        return await self._update(number, changes)

    async def close(self, number: int) -> PullRequest:
        """Close a pull request without merging it."""
        return await self._update(number, {"state": "closed"})

    async def reopen(self, number: int) -> PullRequest:
        """Reopen a closed pull request."""
        return await self._update(number, {"state": "open"})

    async def comment(self, number: int, body: str) -> Comment:
        """Add a comment to a pull request."""
        return await self._github.issue.comment(number, body)

    async def comments(self, number: int, *, limit: int = 30) -> Sequence[Comment]:
        """The comments on a pull request, oldest first."""
        return await self._github.issue.comments(number, limit=limit)

    async def merge(
        self, number: int, *, method: Literal["merge", "squash", "rebase"] = "merge"
    ) -> PullRequest:
        """Merge a pull request. The default is a merge commit."""
        with self._github.guard(f"{self._slug}#{number}"):
            await self._client.rest.pulls.async_merge(
                self._owner, self._name, number, merge_method=method
            )
        return await self.get(number)

    async def checks(self, number: int) -> Sequence[Check]:
        """The check runs on the pull request's current head commit."""
        sha = (await self.get(number)).sha
        with self._github.guard(f"checks of {self._slug}#{number}"):
            response = await self._client.rest.checks.async_list_for_ref(
                self._owner, self._name, sha, per_page=self.MAX_LIMIT
            )
        return [
            Check(
                name=run.name,
                status=str(run.status),
                conclusion=str(run.conclusion) if run.conclusion else None,
                url=run.html_url or "",
            )
            for run in response.parsed_data.check_runs
        ]

    async def _update(self, number: int, changes: dict[str, Any]) -> PullRequest:
        with self._github.guard(f"{self._slug}#{number}"):
            response = await self._client.rest.pulls.async_update(
                self._owner, self._name, number, **changes
            )
        return self._to_pull_request(response.parsed_data)

    def _to_pull_request(self, pull: Any) -> PullRequest:
        labels: Sequence[Any] = pull.labels
        merged = pull.merged_at is not None
        mergeable = getattr(pull, "mergeable", None)
        return PullRequest(
            number=pull.number,
            title=pull.title,
            body=pull.body or "",
            state="merged" if merged else ("open" if pull.state == "open" else "closed"),
            draft=bool(pull.draft) if pull.draft is not UNSET else False,
            head=pull.head.ref,
            base=pull.base.ref,
            sha=pull.head.sha,
            labels=tuple(label.name for label in labels if label.name),
            author=pull.user.login if pull.user else "",
            mergeable=mergeable if isinstance(mergeable, bool) else None,
            updated_at=pull.updated_at,
            url=pull.html_url,
        )
