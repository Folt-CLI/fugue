"""Move a ticket to a status on the project."""

from fugue.core.flow.actions.action import Action
from fugue.core.reporting import Message, StatusMoved
from fugue.models import FieldChange


class MoveStatus(Action):
    async def perform(self, number: int, *, field: str, to: str) -> FieldChange | None:
        """Set the ticket's `field` on the project to `to`.

        Returns None, with a warning, when no project is set.
        """
        if self.github.config.project is None:
            text = (
                "No project is set, so the status was not moved. Set one with --project,"
                " GITHUB_PROJECT, or `project` in the github table of fugue.toml."
            )
            await self.reporter.report(Message(text=text, level="warning"))
            return None
        change = await self.github.project.set(number, field, to)
        await self.reporter.report(StatusMoved(repository=self.github.slug, change=change))
        return change
