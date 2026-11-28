"""The flow of a unit: a Task, a Test or a Documentation ticket under an M."""

from fugue.core.flow import Flow, StepOutline
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.unit.nodes import PREFIX, ReadTicketNode
from fugue.core.work.unit.state import UnitState


class UnitFlow(Flow[WorkConfig, UnitState, WorkResult]):
    """One unit, from reading it to a worktree ready for the agents.

        result = await UnitFlow().run(5, reporter)

    A unit is a commit on its M's branch, so the branch, the worktree and the lock are the M's.
    To add a step, write its node in `nodes.py` and change the return type of the node before it.
    """

    name = "unit"
    config_type = WorkConfig
    state_type = UnitState
    result_type = WorkResult
    start = ReadTicketNode

    def steps(self) -> list[StepOutline]:
        """The steps every unit starts with: the rest depend on its Size, which is known once it
        is read, and arrive as an `OutlineChanged` event."""
        return [StepOutline(node.name) for node in PREFIX]

    async def run(self, number: int, reporter: Reporter) -> WorkResult:
        state = UnitState(number=number)
        try:
            return await self.execute(state, reporter, f"#{number}")
        finally:
            if state.lock is not None:
                state.lock.release()
