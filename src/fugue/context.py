"""Where Fugue is running: the repository, its GitHub name and the token."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pygit2

TOKEN_VARIABLE = "GITHUB_TOKEN"

_GITHUB_REMOTE = re.compile(
    r"^(?:https://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


class ContextError(Exception):
    """The working directory or environment cannot support a run."""


@dataclass(frozen=True)
class Context:
    root: Path
    owner: str
    name: str
    token: str = field(default="", repr=False)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_remote(url: str) -> tuple[str, str]:
    match = _GITHUB_REMOTE.match(url.strip())
    if match is None:
        raise ContextError(f"origin is not a GitHub remote: {url}")
    return match["owner"], match["name"]


def resolve(directory: Path | None = None) -> Context:
    start = (directory or Path.cwd()).resolve()
    git_dir = pygit2.discover_repository(str(start))
    if git_dir is None:
        raise ContextError(f"{start} is not inside a Git repository")
    repo = pygit2.Repository(git_dir)
    if repo.workdir is None:
        raise ContextError("a bare repository has no working tree")
    try:
        origin = repo.remotes["origin"]
    except KeyError:
        raise ContextError("the repository has no remote named origin") from None
    if origin.url is None:
        raise ContextError("origin has no URL")
    owner, name = parse_remote(origin.url)
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not token:
        raise ContextError(f"{TOKEN_VARIABLE} is not set")
    return Context(root=Path(repo.workdir), owner=owner, name=name, token=token)
