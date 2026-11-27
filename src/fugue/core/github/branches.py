"""Branches on GitHub: `github.branch`."""

from collections.abc import Sequence

from fugue.core.github.area import Area
from fugue.models import Branch


class Branches(Area):
    async def list(self, *, limit: int = 100) -> Sequence[Branch]:
        """The branches of the repository."""
        with self._github.guard(f"branches of {self._slug}"):
            response = await self._client.rest.repos.async_list_branches(
                self._owner, self._name, per_page=self._page(limit)
            )
        return [
            Branch(name=branch.name, sha=branch.commit.sha, protected=branch.protected)
            for branch in response.parsed_data
        ][:limit]

    async def delete(self, name: str) -> None:
        """Delete a branch on GitHub."""
        with self._github.guard(f"branch {name!r} in {self._slug}"):
            await self._client.rest.git.async_delete_ref(self._owner, self._name, f"heads/{name}")

    async def default(self) -> str:
        """The name of the repository's default branch."""
        return (await self._github.repo.info()).default_branch
