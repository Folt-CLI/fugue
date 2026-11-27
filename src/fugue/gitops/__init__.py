"""Git operations and commit validation."""

from fugue.gitops.commit import CommitMessage, protected_paths, validate_message, validate_staging
from fugue.gitops.git import Git, GitError

__all__ = [
    "CommitMessage",
    "Git",
    "GitError",
    "protected_paths",
    "validate_message",
    "validate_staging",
]
