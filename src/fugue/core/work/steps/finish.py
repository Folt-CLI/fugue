"""The end of the flow: gather what the run left behind."""

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphRunContext

from fugue.core.work.result import WorkResult
from fugue.core.work.state import WorkDeps, WorkState


@dataclass
class Finish(BaseNode[WorkState, WorkDeps, WorkResult]):
    async def run(self, ctx: GraphRunContext[WorkState, WorkDeps]) -> End[WorkResult]:
        state = ctx.state
        return End(
            WorkResult(
                repository=ctx.deps.github.slug,
                ticket=state.read_ticket,
                status=state.status,
                worktree=state.worktree,
            )
        )
