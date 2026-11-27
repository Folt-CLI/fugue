"""Read the ticket."""

from dataclasses import dataclass

from pydantic_graph import GraphRunContext

from fugue.core.reporting import TicketRead
from fugue.core.work.state import WorkDeps, WorkState
from fugue.core.work.steps.move_status import MoveStatus
from fugue.core.work.steps.step import Step
from fugue.errors import WorkError


@dataclass
class ReadTicket(Step):
    name = "Read the ticket"

    async def act(self, state: WorkState, deps: WorkDeps) -> str:
        ticket = await deps.github.issue.get(state.number)
        if ticket.state == "closed":
            raise WorkError(f"#{ticket.number} is closed: reopen it to work on it")
        state.ticket = ticket
        await deps.reporter.report(TicketRead(repository=deps.github.slug, ticket=ticket))
        return f"#{ticket.number} {ticket.title}"

    async def run(self, ctx: GraphRunContext[WorkState, WorkDeps]) -> MoveStatus:
        await self.execute(ctx)
        return MoveStatus()
