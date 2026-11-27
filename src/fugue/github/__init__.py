"""GitHub access through githubkit."""

from fugue.github.errors import GitHubError
from fugue.github.github import Github

__all__ = ["GitHubError", "Github"]
