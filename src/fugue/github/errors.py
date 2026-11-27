"""GitHub errors and the translation of failed requests."""

from collections.abc import Iterator
from contextlib import contextmanager

from githubkit.exception import RequestFailed


class GitHubError(Exception):
    """GitHub refused or could not answer a request."""


@contextmanager
def translate(subject: str) -> Iterator[None]:
    """Turn a failed request about `subject` into a GitHubError with a clear message."""
    try:
        yield
    except RequestFailed as error:
        status = error.response.status_code
        if status == 404:
            raise GitHubError(f"{subject} does not exist") from error
        if status in (401, 403):
            raise GitHubError(f"GitHub refused the token (HTTP {status})") from error
        raise GitHubError(f"GitHub answered HTTP {status} for {subject}") from error
