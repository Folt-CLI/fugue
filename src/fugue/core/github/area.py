"""The base of every GitHub area (`github.issue`, `github.pr`, ...)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from githubkit import GitHub as Client

if TYPE_CHECKING:
    from fugue.core.github.github import Github


class Area:
    """One part of GitHub, bound to a Github object: its client, repository and guard."""

    MAX_LIMIT = 100

    def __init__(self, github: "Github") -> None:
        self._github = github

    @property
    def _client(self) -> Client[Any]:
        return self._github.client

    @property
    def _owner(self) -> str:
        return self._github.repository.owner

    @property
    def _name(self) -> str:
        return self._github.repository.name

    @property
    def _slug(self) -> str:
        return self._github.repository.slug

    def _page(self, limit: int) -> int:
        """A page size for a list: at least 1 and at most GitHub's maximum."""
        return max(1, min(limit, self.MAX_LIMIT))

    @staticmethod
    def _each(data: Any) -> Sequence[Any]:
        """The items of a list response (githubkit's generated list types do not resolve)."""
        return data  # type: ignore[no-any-return]
