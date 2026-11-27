"""Read the ticket."""

from fugue.core.reporting import TicketRead
from fugue.core.work.run import Run
from fugue.core.work.steps.step import Step
from fugue.errors import WorkError


class ReadTicket(Step):
    name = "Read the ticket"

    async def run(self, run: Run) -> str:
        ticket = await run.github.issue.get(run.number)
        if ticket.state == "closed":
            raise WorkError(f"#{ticket.number} is closed: reopen it to work on it")
        run.ticket = ticket
        await run.reporter.report(TicketRead(repository=run.github.slug, ticket=ticket))
        return f"#{ticket.number} {ticket.title}"
