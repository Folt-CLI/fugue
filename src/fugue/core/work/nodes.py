"""The steps of the work flow, in the order they run.

Each step adapts a reusable action to this flow's state and says where to go next. All the
nodes of a flow live in one module, so that steps that lead back to each other (a repair loop,
later) can name each other without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, cast

from pydantic_graph import BaseNode, End, GraphRunContext

from fugue.core.flow import (
    FlowDeps,
    MoveStatus,
    PrepareBranch,
    ReadDetails,
    ReadTicket,
    RunGate,
    Step,
)
from fugue.core.reporting import Ask, OutlineChanged, PlannedStep
from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.size import WORKED_SIZES, WORKED_TYPES, Size
from fugue.core.work.state import WorkState
from fugue.errors import FlowError

Deps = FlowDeps[WorkConfig]
Context = GraphRunContext[WorkState, Deps]


class WorkStep(Step[WorkState, Deps, WorkResult]):
    """The base of the steps of this flow: it only fixes the types.

    A step that belongs to the pipeline has a `stage`, its name there. `after` says what comes
    next in the ticket's pipeline, so a step does not have to know the sizes."""

    stage: ClassVar[str] = ""

    def after(self, state: WorkState) -> Next:
        """The step after this one in the pipeline, or the end of the flow."""
        pipeline = state.pipeline
        at = pipeline.index(self.stage) + 1 if self.stage in pipeline else 0
        return cast(Next, STAGES[pipeline[at]]()) if at < len(pipeline) else Finish()


@dataclass
class ReadTicketNode(WorkStep):
    """Read the ticket and what the project knows about it, and from its type and Size work out
    what the run will do."""

    name = "Read the ticket"

    async def act(self, state: WorkState, deps: Deps) -> str:
        state.ticket = await ReadTicket(deps.github, deps.reporter).perform(state.number)
        state.details = await ReadDetails(deps.github, deps.reporter).perform(state.number)
        state.kind = state.ticket.issue_type
        if state.kind not in WORKED_TYPES:
            raise FlowError(
                f"#{state.number} is a {state.kind or 'ticket with no type'}: only "
                f"{', '.join(WORKED_TYPES)} tickets have a workflow so far"
            )
        state.size = await self._size(state, deps)
        state.pipeline = PIPELINES[state.size]
        await deps.reporter.report(OutlineChanged(steps=outline(deps.config, state.pipeline)))
        return f"#{state.ticket.number} {state.ticket.title}"

    @staticmethod
    async def _size(state: WorkState, deps: Deps) -> Size:
        size = Size.parse(state.details.get("Size"))
        if size is None:
            answer = await deps.reporter.ask(
                Ask(
                    text=f"#{state.number} has no Size. Which Size is it?",
                    options=tuple(size.value for size in WORKED_SIZES),
                    default=Size.S.value,
                )
            )
            return Size(answer.value)
        if size not in WORKED_SIZES:
            raise FlowError(
                f"#{state.number} is {size}: only {', '.join(WORKED_SIZES)} tickets are worked"
            )
        return size

    async def run(self, ctx: Context) -> MoveStatusNode:
        await self.execute(ctx)
        return MoveStatusNode()


@dataclass
class MoveStatusNode(WorkStep):
    name = "Move the status"

    async def act(self, state: WorkState, deps: Deps) -> str:
        change = await MoveStatus(deps.github, deps.reporter).perform(
            state.number, field=deps.config.status_field, to=deps.config.started_status
        )
        if change is None:
            return "skipped"
        state.status = change.value
        return f"{change.field} = {change.value}"

    async def run(self, ctx: Context) -> PrepareBranchNode:
        await self.execute(ctx)
        return PrepareBranchNode()


@dataclass
class PrepareBranchNode(WorkStep):
    name = "Prepare the branch"

    async def act(self, state: WorkState, deps: Deps) -> str:
        state.worktree = await PrepareBranch(deps.github, deps.reporter).perform(
            state.read_ticket,
            base=deps.config.base_branch,
            root=deps.config.worktree_root,
            slug_length=deps.config.slug_length,
        )
        return state.worktree.branch

    async def run(self, ctx: Context) -> Next:
        await self.execute(ctx)
        return self.after(ctx.state)  # the first stage of the pipeline


class AgentStage(WorkStep):
    """A stage an agent does. Until an agent is set for it, it says so and goes on."""

    role: ClassVar[str] = ""

    async def act(self, state: WorkState, deps: Deps) -> str:
        return "no agent set"

    async def run(self, ctx: Context) -> Next:
        await self.execute(ctx)
        return self.after(ctx.state)


@dataclass
class AnalyseNode(AgentStage):
    """Read the ticket and the code, and decide what to do and whether the ticket is what it
    says it is. Only analysis may promote a ticket."""

    name = "Analyse the ticket"
    stage = "analyse"
    role = "analyst"


@dataclass
class ImplementNode(AgentStage):
    name = "Implement the change"
    stage = "implement"
    role = "implementer"


@dataclass
class ReviewNode(AgentStage):
    """Read the change as a reviewer: a blocking finding goes back to be fixed, a non-blocking
    one becomes a ticket."""

    name = "Review the change"
    stage = "review"
    role = "reviewer"


@dataclass
class GateNode(WorkStep):
    """Run the checks the repository asks for, in the worktree. Every check runs, and the run
    stops if any failed."""

    name = "Run the gate"

    @classmethod
    def parts(cls, config: Any) -> tuple[str, ...]:
        return tuple(config.gate)

    async def act(self, state: WorkState, deps: Deps) -> str:
        checks = deps.config.gate
        if not checks:
            return "no checks set"
        worktree = state.worktree
        if worktree is None:
            raise FlowError("there is no worktree to run the gate in")
        state.gate = await RunGate(deps.github, deps.reporter).perform(
            self.name, checks, worktree.path, timeout=deps.config.gate_timeout
        )
        if not state.gate.passed:
            failed = ", ".join(check.name for check in state.gate.failed)
            raise FlowError(f"the gate failed: {failed}")
        return f"{len(state.gate.checks)} checks passed"

    stage = "gate"

    async def run(self, ctx: Context) -> Next:
        await self.execute(ctx)
        return self.after(ctx.state)


@dataclass
class Finish(BaseNode[WorkState, Deps, WorkResult]):
    """The end of the flow: gather what the run left behind."""

    async def run(self, ctx: Context) -> End[WorkResult]:
        state = ctx.state
        return End(
            WorkResult(
                repository=ctx.deps.github.slug,
                ticket=state.read_ticket,
                status=state.status,
                worktree=state.worktree,
                gate=state.gate,
            )
        )


Next = AnalyseNode | ImplementNode | GateNode | ReviewNode | Finish

STAGES: dict[str, type[WorkStep]] = {
    "analyse": AnalyseNode,
    "implement": ImplementNode,
    "gate": GateNode,
    "review": ReviewNode,
}

PIPELINES: dict[Size, tuple[str, ...]] = {
    Size.XS: ("implement", "gate"),
    Size.S: ("analyse", "implement", "gate"),
    Size.M: ("analyse", "implement", "gate", "review"),
}
"""What a ticket goes through after its branch is ready, by Size. XS has no analysis, on purpose:
only analysis can change a ticket's Size or type, so an XS never does."""

PREFIX: tuple[type[WorkStep], ...] = (ReadTicketNode, MoveStatusNode, PrepareBranchNode)
"""The steps every ticket starts with."""


def outline(config: Any, pipeline: tuple[str, ...]) -> tuple[PlannedStep, ...]:
    """The steps a run with this pipeline goes through, as a checklist lists them."""
    nodes = [*PREFIX, *(STAGES[stage] for stage in pipeline)]
    return tuple(PlannedStep(name=node.name, parts=node.parts(config)) for node in nodes)


__all__: list[Any] = [
    "PIPELINES",
    "PREFIX",
    "STAGES",
    "AnalyseNode",
    "Finish",
    "GateNode",
    "ImplementNode",
    "MoveStatusNode",
    "PrepareBranchNode",
    "ReadTicketNode",
    "ReviewNode",
    "WorkStep",
]
