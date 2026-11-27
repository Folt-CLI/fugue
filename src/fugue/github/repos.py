"""The repository itself: `github.repo`."""

from fugue.github.area import Area
from fugue.models import RepoInfo


class Repo(Area):
    async def info(self) -> RepoInfo:
        """What GitHub knows about the repository."""
        with self._github.guard(self._slug):
            response = await self._client.rest.repos.async_get(self._owner, self._name)
        repo = response.parsed_data
        return RepoInfo(
            slug=repo.full_name,
            description=repo.description or "",
            default_branch=repo.default_branch,
            private=repo.private,
            open_issues=repo.open_issues_count,
            stars=repo.stargazers_count,
            url=repo.html_url,
        )
