"""Move the ticket to its starting status on the project."""

from dataclasses import dataclass

from pydantic_graph import GraphRunContext

from fugue.core.reporting import Message, StatusMoved
from fugue.core.work.state import WorkDeps, WorkState
from fugue.core.work.steps.prepare_branch import PrepareBranch
from fugue.core.work.steps.step import Step


@dataclass
class MoveStatus(Step):
    name = "Move the status"

    async def act(self, state: WorkState, deps: WorkDeps) -> str:
        if deps.github.config.project is None:
            text = "No project is set (GITHUB_PROJECT), so the status was not moved."
            await deps.reporter.report(Message(text=text, level="warning"))
            return "skipped"
        change = await deps.github.project.set(
            state.number, deps.config.status_field, deps.config.started_status
        )
        state.status = change.value
        await deps.reporter.report(StatusMoved(repository=deps.github.slug, change=change))
        return f"{change.field} = {change.value}"

    async def run(self, ctx: GraphRunContext[WorkState, WorkDeps]) -> PrepareBranch:
        await self.execute(ctx)
        return PrepareBranch()
