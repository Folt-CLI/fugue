"""The Github class: one object for everything Fugue does on GitHub."""

from collections.abc import Iterator
from contextlib import contextmanager

from githubkit import GitHub as _Client
from githubkit.exception import RequestFailed

from fugue.config import GithubConfig
from fugue.errors import GitHubError
from fugue.github.branches import Branches
from fugue.github.issues import Issues
from fugue.github.labels import Labels
from fugue.github.pulls import PullRequests
from fugue.github.repos import Repo
from fugue.gitops import Repository


class Github:
    """Talk to the repository's GitHub.

    `Github()` reads the repository from the `origin` of the current directory and the
    token from GITHUB_TOKEN. Pass a configuration and a repository to use others.

        github = Github()
        ticket = await github.issue.get(246)
        pull = await github.pr.create("Title", head="feature", base="develop")

    Areas: `issue`, `pr`, `label`, `branch` and `repo`.
    """

    def __init__(
        self, config: GithubConfig | None = None, repository: Repository | None = None
    ) -> None:
        self.config = config or GithubConfig.load()
        self.repository = repository or Repository.discover()
        self.client = _Client(self.config.token.get_secret_value(), base_url=self.config.api_url)
        self.issue = Issues(self)
        self.pr = PullRequests(self)
        self.label = Labels(self)
        self.branch = Branches(self)
        self.repo = Repo(self)

    @property
    def slug(self) -> str:
        return self.repository.slug

    @contextmanager
    def guard(self, subject: str) -> Iterator[None]:
        """Turn a failed request about `subject` into a GitHubError with a clear message."""
        try:
            yield
        except RequestFailed as error:
            status = error.response.status_code
            if status == 404:
                raise GitHubError(f"{subject} does not exist") from error
            if status in (401, 403):
                raise GitHubError(f"GitHub refused the token (HTTP {status})") from error
            if status in (405, 409, 422):
                raise GitHubError(f"GitHub rejected the request for {subject}: {error}") from error
            raise GitHubError(f"GitHub answered HTTP {status} for {subject}") from error
