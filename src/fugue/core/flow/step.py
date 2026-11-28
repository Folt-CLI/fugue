"""A step of a flow: a node of its graph."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic_graph import BaseNode, GraphRunContext

from fugue.core.flow.state import FlowDeps
from fugue.core.reporting import StepFailed, StepFinished, StepStarted
from fugue.errors import FlowError, FugueError


@dataclass(frozen=True)
class StepOutline:
    """A step as an interface lists it before the run: its name, and the names of its parts."""

    name: str
    parts: tuple[str, ...] = ()


class Step[StateT, DepsT: FlowDeps[Any], ResultT](BaseNode[StateT, DepsT, ResultT], ABC):
    """A node that does one thing, reports it, and raises a FugueError if it cannot.

    Before it starts, a step passes the run's checkpoint, which may stop the run.

    A step is re-runnable: running it again after a stop must be safe, because that is how a
    run is resumed.

    A flow gives its steps a short base class that fixes the three types, then each step
    writes `act` for the work (usually one call to an action) and a `run` whose return type
    names the next node, which is what draws the edge in the graph:

        async def run(self, ctx: GraphRunContext[S, D]) -> NextStep:
            await self.execute(ctx)
            return NextStep()
    """

    name: ClassVar[str]

    @classmethod
    def parts(cls, config: Any) -> tuple[str, ...]:
        """The parts the step is made of, when it has some that are known before it runs, such as
        the checks of a gate. They are listed under the step in the checklist, and the step
        reports each with `PartStarted` and `PartFinished`."""
        return ()

    @abstractmethod
    async def act(self, state: StateT, deps: DepsT) -> str:
        """Do the step. Returns a short note for the report, or an empty string."""

    async def execute(self, ctx: GraphRunContext[StateT, DepsT]) -> None:
        """Run `act` between the started and finished events; a failure is reported and raised."""
        reporter = ctx.deps.reporter
        await ctx.deps.checkpoint.pass_(self.name)
        await reporter.report(StepStarted(step=self.name))
        try:
            detail = await self.act(ctx.state, ctx.deps)
        except FugueError as error:
            await reporter.report(StepFailed(step=self.name, message=str(error)))
            raise
        except Exception as error:
            message = f"{self.name} failed unexpectedly: {error!r}"
            await reporter.report(StepFailed(step=self.name, message=message))
            raise FlowError(message) from error
        await reporter.report(StepFinished(step=self.name, detail=detail))
