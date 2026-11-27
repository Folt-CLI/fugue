"""What a work run carries: the data that changes, and the services it uses."""

from dataclasses import dataclass

from fugue.core.github import Github
from fugue.core.gitops import Worktree
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.models import Ticket


@dataclass
class WorkState:
    """What the steps of one run learn and leave for the next ones."""

    number: int
    ticket: Ticket | None = None
    status: str | None = None
    worktree: Worktree | None = None

    @property
    def read_ticket(self) -> Ticket:
        """The ticket, once ReadTicket has run."""
        if self.ticket is None:
            raise RuntimeError("the ticket has not been read yet")
        return self.ticket


@dataclass(frozen=True)
class WorkDeps:
    """What the steps use and never change."""

    github: Github
    config: WorkConfig
    reporter: Reporter
