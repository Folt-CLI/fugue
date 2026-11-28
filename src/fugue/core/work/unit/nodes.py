"""The steps of the unit flow, in the order they run.

A unit is a Task, a Test or a Documentation ticket (S or XS). Each step adapts a reusable action
to this flow's state and says where to go next. All the nodes of a flow live in one module, so
that steps that lead back to each other (a repair loop, later) can name each other without a
circular import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, cast

from pydantic_graph import BaseNode, End, GraphRunContext

from fugue.core.flow import (
    FlowDeps,
    LockBranch,
    MoveStatus,
    Naming,
    PrepareBranch,
    ReadDetails,
    ReadParent,
    ReadTicket,
    RunGate,
    Step,
)
from fugue.core.reporting import Ask, OutlineChanged, PlannedStep
from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.route import Route, Tier
from fugue.core.work.size import UNIT_SIZES, Size
from fugue.core.work.unit.state import UnitState
from fugue.errors import FlowError

Deps = FlowDeps[WorkConfig]
Context = GraphRunContext[UnitState, Deps]


class UnitStep(Step[UnitState, Deps, WorkResult]):
    """The base of the steps of this flow: it only fixes the types.

    A step that belongs to the pipeline has a `stage`, its name there. `after` says what comes
    next in the unit's pipeline, so a step does not have to know the sizes."""

    stage: ClassVar[str] = ""

    def after(self, state: UnitState) -> Next:
        """The step after this one in the pipeline, or the end of the flow."""
        pipeline = state.pipeline
        at = pipeline.index(self.stage) + 1 if self.stage in pipeline else 0
        return cast(Next, STAGES[pipeline[at]]()) if at < len(pipeline) else Finish()


@dataclass
class ReadTicketNode(UnitStep):
    """Read the unit and what the project knows about it, and from its Size work out what the
    run will do."""

    name = "Read the ticket"

    async def act(self, state: UnitState, deps: Deps) -> str:
        state.ticket = await ReadTicket(deps.github, deps.reporter).perform(state.number)
        state.details = await ReadDetails(deps.github, deps.reporter).perform(state.number)
        if Route.tier(state.number, state.ticket.issue_type) is not Tier.UNIT:
            raise FlowError(
                f"#{state.number} is a {state.ticket.issue_type}, not a Task, Test or Documentation"
            )
        state.size = await self._size(state, deps)
        state.pipeline = PIPELINES[state.size]
        await deps.reporter.report(OutlineChanged(steps=outline(deps.config, state.pipeline)))
        return f"#{state.ticket.number} {state.ticket.title}"

    @staticmethod
    async def _size(state: UnitState, deps: Deps) -> Size:
        size = Size.parse(state.details.get("Size"))
        if size is None:
            answer = await deps.reporter.ask(
                Ask(
                    text=f"#{state.number} has no Size. Which Size is it?",
                    options=tuple(size.value for size in UNIT_SIZES),
                    default=Size.S.value,
                )
            )
            return Size(answer.value)
        if size not in UNIT_SIZES:
            raise FlowError(f"#{state.number} is {size}: a unit is XS or S")
        return size

    async def run(self, ctx: Context) -> ReadParentNode:
        await self.execute(ctx)
        return ReadParentNode()


@dataclass
class ReadParentNode(UnitStep):
    """A unit belongs to an M ticket: its branch, worktree and pull request are the M's."""

    name = "Check the parent"

    async def act(self, state: UnitState, deps: Deps) -> str:
        state.parent = await ReadParent(deps.github, deps.reporter).perform(state.read_ticket)
        return f"#{state.parent.number} {state.parent.title}"

    async def run(self, ctx: Context) -> LockBranchNode:
        await self.execute(ctx)
        return LockBranchNode()


@dataclass
class LockBranchNode(UnitStep):
    """Two runs must not work the same M's worktree at once."""

    name = "Lock the branch"

    async def act(self, state: UnitState, deps: Deps) -> str:
        branch = Naming(deps.config.slug_length).branch(state.read_parent)
        state.lock = await LockBranch(deps.github, deps.reporter).perform(
            branch, run_id=deps.run_id
        )
        return branch

    async def run(self, ctx: Context) -> PrepareBranchNode:
        await self.execute(ctx)
        return PrepareBranchNode()


@dataclass
class PrepareBranchNode(UnitStep):
    """The M's branch, brought up to date with origin, in its worktree."""

    name = "Prepare the branch"

    async def act(self, state: UnitState, deps: Deps) -> str:
        state.worktree = await PrepareBranch(deps.github, deps.reporter).perform(
            state.read_parent,
            base=deps.config.base_branch,
            root=deps.config.worktree_root,
            slug_length=deps.config.slug_length,
        )
        return state.worktree.branch

    async def run(self, ctx: Context) -> MoveStatusNode:
        await self.execute(ctx)
        return MoveStatusNode()


@dataclass
class MoveStatusNode(UnitStep):
    name = "Move the status"

    async def act(self, state: UnitState, deps: Deps) -> str:
        change = await MoveStatus(deps.github, deps.reporter).perform(
            state.number, field=deps.config.status_field, to=deps.config.started_status
        )
        if change is None:
            return "skipped"
        state.status = change.value
        return f"{change.field} = {change.value}"

    async def run(self, ctx: Context) -> Next:
        await self.execute(ctx)
        return self.after(ctx.state)  # the first stage of the pipeline


class AgentStage(UnitStep):
    """A stage an agent does. Until an agent is set for it, it says so and goes on."""

    role: ClassVar[str] = ""

    async def act(self, state: UnitState, deps: Deps) -> str:
        return "no agent set"

    async def run(self, ctx: Context) -> Next:
        await self.execute(ctx)
        return self.after(ctx.state)


@dataclass
class PlanNode(AgentStage):
    """Read the M's logic and plan the unit: what to change and where. Only the plan agent may
    promote a unit (XS to S, S to M)."""

    name = "Plan the unit"
    stage = "plan"
    role = "plan"


@dataclass
class ImplementNode(AgentStage):
    name = "Implement the change"
    stage = "implement"
    role = "implementation"


@dataclass
class ReviewNode(AgentStage):
    """Read the change as a reviewer: a blocking finding goes back to be fixed, a non-blocking
    one becomes a ticket."""

    name = "Review the change"
    stage = "review"
    role = "reviewer"


@dataclass
class GateNode(UnitStep):
    """Run the checks the repository asks for, in the worktree. Every check runs, and the run
    stops if any failed."""

    name = "Run the gate"

    @classmethod
    def parts(cls, config: Any) -> tuple[str, ...]:
        return tuple(config.gate)

    async def act(self, state: UnitState, deps: Deps) -> str:
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
class Finish(BaseNode[UnitState, Deps, WorkResult]):
    """The end of the flow: gather what the run left behind."""

    async def run(self, ctx: Context) -> End[WorkResult]:
        state = ctx.state
        return End(
            WorkResult(
                repository=ctx.deps.github.slug,
                ticket=state.read_ticket,
                parent=state.parent,
                status=state.status,
                worktree=state.worktree,
                gate=state.gate,
            )
        )


Next = PlanNode | ImplementNode | GateNode | ReviewNode | Finish

STAGES: dict[str, type[UnitStep]] = {
    "plan": PlanNode,
    "implement": ImplementNode,
    "gate": GateNode,
    "review": ReviewNode,
}

PIPELINES: dict[Size, tuple[str, ...]] = {
    Size.XS: ("plan", "implement", "gate"),
    Size.S: ("plan", "implement", "gate", "review"),
}
"""What a unit goes through once its worktree is ready, by Size. Both are planned, because the
plan is where the unit learns what to change and where; only S is reviewed."""

PREFIX: tuple[type[UnitStep], ...] = (
    ReadTicketNode,
    ReadParentNode,
    LockBranchNode,
    PrepareBranchNode,
    MoveStatusNode,
)
"""The steps every unit starts with."""


def outline(config: Any, pipeline: tuple[str, ...]) -> tuple[PlannedStep, ...]:
    """The steps a run with this pipeline goes through, as a checklist lists them."""
    nodes = [*PREFIX, *(STAGES[stage] for stage in pipeline)]
    return tuple(PlannedStep(name=node.name, parts=node.parts(config)) for node in nodes)


__all__: list[Any] = [
    "PIPELINES",
    "PREFIX",
    "STAGES",
    "Finish",
    "GateNode",
    "ImplementNode",
    "LockBranchNode",
    "MoveStatusNode",
    "PlanNode",
    "PrepareBranchNode",
    "ReadParentNode",
    "ReadTicketNode",
    "ReviewNode",
    "UnitStep",
]
