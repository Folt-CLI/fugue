"""Move the ticket to its starting status on the project."""

from fugue.core.reporting import Message, StatusMoved
from fugue.core.work.run import Run
from fugue.core.work.steps.step import Step


class MoveStatus(Step):
    name = "Move the status"

    async def run(self, run: Run) -> str:
        if run.github.config.project is None:
            text = "No project is set (GITHUB_PROJECT), so the status was not moved."
            await run.reporter.report(Message(text=text, level="warning"))
            return "skipped"
        change = await run.github.project.set(
            run.number, run.config.status_field, run.config.started_status
        )
        run.status = change.value
        await run.reporter.report(StatusMoved(repository=run.github.slug, change=change))
        return f"{change.field} = {change.value}"
