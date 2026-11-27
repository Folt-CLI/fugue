"""The state of one work run, shared by its steps."""

from fugue.core.github import Github
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.models import Ticket


class Run:
    """What the steps of one `work` need and what they leave for the next ones."""

    def __init__(self, number: int, github: Github, config: WorkConfig, reporter: Reporter) -> None:
        self.number = number
        self.github = github
        self.config = config
        self.reporter = reporter
        self.ticket: Ticket | None = None
        self.status: str | None = None

    @property
    def read_ticket(self) -> Ticket:
        """The ticket, once ReadTicket has run."""
        if self.ticket is None:
            raise RuntimeError("the ticket has not been read yet")
        return self.ticket
