"""The shapes of state and dependencies that flows share."""

from dataclasses import dataclass, field

from fugue.core.flow.checkpoint import Checkpoint
from fugue.core.github import Github
from fugue.core.gitops import Worktree
from fugue.core.reporting import Reporter
from fugue.models import Ticket


@dataclass
class TicketState:
    """What a flow that starts from a ticket learns about it."""

    number: int
    ticket: Ticket | None = None
    details: dict[str, str] = field(default_factory=dict)
    status: str | None = None

    @property
    def read_ticket(self) -> Ticket:
        """The ticket, once it has been read."""
        if self.ticket is None:
            raise RuntimeError("the ticket has not been read yet")
        return self.ticket


@dataclass
class WorktreeState(TicketState):
    """A ticket flow that also works in a worktree."""

    worktree: Worktree | None = None


@dataclass(frozen=True)
class FlowDeps[ConfigT]:
    """What every step of a flow uses and never changes."""

    github: Github
    config: ConfigT
    reporter: Reporter
    checkpoint: Checkpoint
    run_id: str = ""
