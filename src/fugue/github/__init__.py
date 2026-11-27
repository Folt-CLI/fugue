"""GitHub access through githubkit."""

from fugue.errors import GitHubError
from fugue.github.github import Github

__all__ = ["GitHubError", "Github"]
