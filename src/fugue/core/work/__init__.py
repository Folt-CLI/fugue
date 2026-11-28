"""The work flow: one ticket, start to pull request."""

from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.work import Work

__all__ = ["Work", "WorkConfig", "WorkResult"]
