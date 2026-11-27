"""What a finished work run leaves behind."""

from pydantic import BaseModel, ConfigDict

from fugue.core.gitops import Worktree
from fugue.models import Ticket


class WorkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    repository: str
    ticket: Ticket
    status: str | None = None
    worktree: Worktree | None = None
