"""The Git repository Fugue works in."""

import re
from pathlib import Path
from typing import ClassVar

import pygit2
from pydantic import BaseModel, ConfigDict

from fugue.errors import RepositoryError


class Repository(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: Path
    owner: str
    name: str

    _REMOTE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:https://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
    )

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def parse_remote(cls, url: str) -> tuple[str, str]:
        """Owner and name of a GitHub remote URL (https, ssh or scp-style)."""
        match = cls._REMOTE.match(url.strip())
        if match is None:
            raise RepositoryError(f"origin is not a GitHub remote: {url}")
        return match["owner"], match["name"]

    @classmethod
    def discover(cls, directory: Path | None = None) -> "Repository":
        """The repository containing `directory` (default: the current directory)."""
        start = (directory or Path.cwd()).resolve()
        git_dir = pygit2.discover_repository(str(start))
        if git_dir is None:
            raise RepositoryError(f"{start} is not inside a Git repository")
        repo = pygit2.Repository(git_dir)
        if repo.workdir is None:
            raise RepositoryError("a bare repository has no working tree")
        try:
            origin = repo.remotes["origin"]
        except KeyError:
            raise RepositoryError("the repository has no remote named origin") from None
        if origin.url is None:
            raise RepositoryError("origin has no URL")
        owner, name = cls.parse_remote(origin.url)
        return cls(root=Path(repo.workdir), owner=owner, name=name)
