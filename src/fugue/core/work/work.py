"""`fugue work N`: take one ticket through the whole flow."""

from fugue.core.flow import Flow, StepOutline
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.core.work.nodes import PREFIX, ReadTicketNode
from fugue.core.work.result import WorkResult
from fugue.core.work.state import WorkState


class Work(Flow[WorkConfig, WorkState, WorkResult]):
    """One ticket, from reading it to a worktree ready for the agent.

        result = await Work().run(5, reporter)

    `diagram()` draws the flow. To add a step, write its node in `nodes.py` and change the
    return type of the node before it.
    """

    name = "work"
    config_type = WorkConfig
    state_type = WorkState
    result_type = WorkResult
    start = ReadTicketNode

    def steps(self) -> list[StepOutline]:
        """Only the steps every ticket starts with: the rest depend on the ticket's Size, which is
        known once it is read, and arrive as an `OutlineChanged` event."""
        return [StepOutline(node.name) for node in PREFIX]

    async def run(self, number: int, reporter: Reporter) -> WorkResult:
        return await self.execute(WorkState(number=number), reporter, f"#{number}")
