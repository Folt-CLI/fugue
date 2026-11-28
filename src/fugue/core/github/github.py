"""The Github class: one object for everything Fugue does on GitHub."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from githubkit import GitHub as _Client
from githubkit.exception import RequestFailed

from fugue.config import GithubConfig
from fugue.core.github.branches import Branches
from fugue.core.github.issues import Issues
from fugue.core.github.labels import Labels
from fugue.core.github.projects import Projects
from fugue.core.github.pulls import PullRequests
from fugue.core.github.repos import Repo
from fugue.core.gitops import Repository
from fugue.errors import GitHubError


class Github:
    """Talk to the repository's GitHub.

    `Github()` reads the repository from the `origin` of the current directory and the
    token from GITHUB_TOKEN. Pass a configuration and a repository to use others.

        github = Github()
        ticket = await github.issue.get(246)
        pull = await github.pr.create("Title", head="feature", base="develop")

    Areas: `issue`, `pr`, `label`, `branch`, `repo` and `project`.
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
        self.project = Projects(self)

    @property
    def slug(self) -> str:
        return self.repository.slug

    @contextmanager
    def guard(self, subject: str, *, scopes: Sequence[str] = ()) -> Iterator[None]:
        """Turn a failed request about `subject` into a GitHubError with a clear message.

        `scopes` lists the token scopes that would allow the request, any one of them. When the
        token is a classic one that has none of them, that is named as the cause: GitHub answers
        404 instead of 403 for things a token is not allowed to see.
        """
        try:
            yield
        except RequestFailed as error:
            status = error.response.status_code
            held = error.response.headers.get("x-oauth-scopes")
            if (
                scopes
                and held is not None
                and not set(scopes) & {s.strip() for s in held.split(",")}
            ):
                raise GitHubError(
                    f"{subject}: GitHub refused it because the token has no "
                    f"{' or '.join(scopes)} scope (it has: {held or 'none'}). "
                    f"Use a token with that scope in GITHUB_TOKEN."
                ) from error
            if status == 404:
                raise GitHubError(f"{subject} does not exist") from error
            if status in (401, 403):
                raise GitHubError(f"GitHub refused the token (HTTP {status})") from error
            if status in (405, 409, 422):
                raise GitHubError(f"GitHub rejected the request for {subject}: {error}") from error
            raise GitHubError(f"GitHub answered HTTP {status} for {subject}") from error
