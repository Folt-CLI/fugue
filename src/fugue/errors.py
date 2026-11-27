"""Errors Fugue reports to the user without a traceback."""


class FugueError(Exception):
    """Base of every error with a message meant for the user."""


class ConfigError(FugueError):
    """A setting is missing or invalid."""


class RepositoryError(FugueError):
    """The working directory cannot support a run."""


class GitHubError(FugueError):
    """GitHub refused or could not answer a request."""


class FlowError(FugueError):
    """A flow cannot go on."""
