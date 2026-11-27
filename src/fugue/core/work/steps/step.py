"""A step of the work flow: a node of the graph."""

from abc import abstractmethod
from typing import ClassVar

from pydantic_graph import BaseNode, GraphRunContext

from fugue.core.reporting import StepFailed, StepFinished, StepStarted
from fugue.core.work.result import WorkResult
from fugue.core.work.state import WorkDeps, WorkState
from fugue.errors import FugueError, WorkError


class Step(BaseNode[WorkState, WorkDeps, WorkResult]):
    """A node that does one thing, reports it, and raises a FugueError if it cannot.

    A subclass writes `act` for the work and a `run` whose return type names the next node,
    which is what draws the edge in the graph:

        async def run(self, ctx: GraphRunContext[WorkState, WorkDeps]) -> NextStep:
            await self.execute(ctx)
            return NextStep()
    """

    name: ClassVar[str]

    @abstractmethod
    async def act(self, state: WorkState, deps: WorkDeps) -> str:
        """Do the step. Returns a short note for the report, or an empty string."""

    async def execute(self, ctx: GraphRunContext[WorkState, WorkDeps]) -> None:
        """Run `act` between the started and finished events; a failure is reported and raised."""
        reporter = ctx.deps.reporter
        await reporter.report(StepStarted(step=self.name))
        try:
            detail = await self.act(ctx.state, ctx.deps)
        except FugueError as error:
            await reporter.report(StepFailed(step=self.name, message=str(error)))
            raise
        except Exception as error:
            message = f"{self.name} failed unexpectedly: {error!r}"
            await reporter.report(StepFailed(step=self.name, message=message))
            raise WorkError(message) from error
        await reporter.report(StepFinished(step=self.name, detail=detail))
