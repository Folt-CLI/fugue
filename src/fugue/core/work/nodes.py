"""The steps of the work flow, in the order they run.

Each step adapts a reusable action to this flow's state and says where to go next. All the
nodes of a flow live in one module, so that steps that lead back to each other (a repair loop,
later) can name each other without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.state import WorkState
from fugue.errors import FlowError

Deps = FlowDeps[WorkConfig]
Context = GraphRunContext[WorkState, Deps]


class WorkStep(Step[WorkState, Deps, WorkResult]):
    """The base of the steps of this flow: it only fixes the types."""


@dataclass
class ReadTicketNode(WorkStep):
    name = "Read the ticket"

    async def act(self, state: WorkState, deps: Deps) -> str:
        state.ticket = await ReadTicket(deps.github, deps.reporter).perform(state.number)
        state.details = await ReadDetails(deps.github, deps.reporter).perform(state.number)
        return f"#{state.ticket.number} {state.ticket.title}"

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

    async def run(self, ctx: Context) -> GateNode:
        await self.execute(ctx)
        return GateNode()


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

    async def run(self, ctx: Context) -> Finish:
        await self.execute(ctx)
        return Finish()


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


__all__: list[Any] = [
    "Finish",
    "GateNode",
    "MoveStatusNode",
    "PrepareBranchNode",
    "ReadTicketNode",
    "WorkStep",
]
