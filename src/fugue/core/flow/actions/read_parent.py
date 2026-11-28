"""Read the M ticket a unit belongs to."""

import re

from fugue.core.flow.actions.action import Action
from fugue.core.reporting import Message
from fugue.errors import FlowError
from fugue.models import Ticket


class ReadParent(Action):
    BUILDS_ON = re.compile(r"Builds on[:\s]+((?:#\d+[,\s]*(?:and\s+)?)+)", re.IGNORECASE)

    async def perform(self, unit: Ticket) -> Ticket:
        """The parent of a unit. A unit must have an M parent (a Feature, Bug or Maintenance),
        and it must be open. An M that builds on another M waits until that one is closed, since
        its branch would be missing that code."""
        # Imported here: `core.work` uses the flow package, so the route cannot be a top import.
        from fugue.core.work.route import Route

        parent = await self.github.issue.parent(unit.number)
        if parent is None:
            raise FlowError(
                f"#{unit.number} has no parent: a {unit.issue_type} belongs to an M ticket "
                f"(a Feature, Bug or Maintenance). Add it as a sub-issue of one."
            )
        if not Route.is_logic(parent.issue_type):
            kind = parent.issue_type or "ticket with no type"
            raise FlowError(
                f"#{unit.number}'s parent #{parent.number} is a {kind}: "
                f"a unit's parent must be a Feature, Bug or Maintenance"
            )
        if parent.state == "closed":
            raise FlowError(f"#{parent.number} is closed: reopen it to work on #{unit.number}")
        for number in self._builds_on(parent.body):
            earlier = await self.github.issue.get(number)
            if earlier.state == "open":
                raise FlowError(
                    f"#{parent.number} builds on #{number}, which is not closed yet: its code is "
                    f"not on the base branch, so wait until #{number} is merged"
                )
        await self.reporter.report(
            Message(text=f"Part of #{parent.number} {parent.title} ({parent.issue_type})")
        )
        return parent

    @classmethod
    def _builds_on(cls, body: str) -> list[int]:
        found = [int(n) for m in cls.BUILDS_ON.findall(body) for n in re.findall(r"#(\d+)", m)]
        return list(dict.fromkeys(found))
