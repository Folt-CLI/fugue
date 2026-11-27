"""Read what the project knows about a ticket."""

from fugue.core.flow.actions.action import Action
from fugue.core.reporting import Message, TicketDetails
from fugue.errors import GitHubError


class ReadDetails(Action):
    FIRST = (
        "status",
        "size",
        "type",
        "priority",
        "component",
        "disposition",
        "milestone",
        "assignee",
    )
    """The fields worth reading first, in this order; the rest follow in the project's order."""

    async def perform(self, number: int) -> dict[str, str]:
        """The ticket's fields on the project, with the useful ones first. Without a project, or
        when the ticket is not on it, there is nothing to show and a warning says why; it does not
        stop the run, because moving the status will say so if that matters."""
        if self.github.config.project is None:
            return {}
        try:
            item = await self.github.project.details(number)
        except GitHubError as error:
            await self.reporter.report(
                Message(text=f"Could not read the project fields: {error}", level="warning")
            )
            return {}
        fields = self._ordered(item.fields)
        await self.reporter.report(
            TicketDetails(repository=self.github.slug, number=number, fields=fields)
        )
        return fields

    @classmethod
    def _ordered(cls, fields: dict[str, str]) -> dict[str, str]:
        def rank(name: str) -> int:
            key = name.casefold()
            for place, wanted in enumerate(cls.FIRST):
                if key in (wanted, f"{wanted}s"):  # Assignee and Assignees are the same field
                    return place
            return len(cls.FIRST)

        return dict(sorted(fields.items(), key=lambda pair: rank(pair[0])))
