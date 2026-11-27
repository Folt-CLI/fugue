"""GitHub access through githubkit."""

from fugue.core.github.github import Github
from fugue.errors import GitHubError

__all__ = ["GitHubError", "Github"]
