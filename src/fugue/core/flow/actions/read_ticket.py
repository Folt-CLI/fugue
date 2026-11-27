"""Read a ticket."""

from fugue.core.flow.actions.action import Action
from fugue.core.reporting import TicketRead
from fugue.errors import FlowError
from fugue.models import Ticket


class ReadTicket(Action):
    async def perform(self, number: int, *, allow_closed: bool = False) -> Ticket:
        """The ticket. A closed one is refused unless `allow_closed`."""
        ticket = await self.github.issue.get(number)
        if ticket.state == "closed" and not allow_closed:
            raise FlowError(f"#{ticket.number} is closed: reopen it to work on it")
        await self.reporter.report(TicketRead(repository=self.github.slug, ticket=ticket))
        return ticket
