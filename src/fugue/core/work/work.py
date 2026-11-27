"""`fugue work N`: take one ticket through the whole flow."""

from fugue.core.flow import Flow
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.core.work.nodes import ReadTicketNode
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

    async def run(self, number: int, reporter: Reporter) -> WorkResult:
        return await self.execute(WorkState(number=number), reporter, f"#{number}")
