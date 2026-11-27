"""The Github class: one object for everything Fugue does on GitHub."""

from githubkit import GitHub as _Client

from fugue.context import Context, resolve
from fugue.github.issues import Issues


class Github:
    """Talk to the repository's GitHub.

    `Github()` reads the repository from the `origin` of the current directory and the
    token from GITHUB_TOKEN. Pass a `Context` to use another repository.

        github = Github()
        ticket = await github.issue.get(246)
    """

    def __init__(self, context: Context | None = None) -> None:
        self.context = context or resolve()
        self.client = _Client(self.context.token)
        self.issue = Issues(self)

    @property
    def slug(self) -> str:
        return self.context.slug
