"""`fugue work N`: take one ticket through the flow its type calls for."""

from typing import ClassVar

from fugue.core.flow import StepOutline
from fugue.core.github import Github
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.route import Route, Tier
from fugue.core.work.unit import UnitFlow
from fugue.errors import FlowError


class Work:
    """The entry of `fugue work`: it looks at the ticket's type and hands the run to the flow
    for its tier. Each tier is a flow of its own, so each can grow without the others.

        result = await Work().run(5, reporter)

    Built so far: the unit flow (Task, Test, Documentation). The M and L flows are not built, and
    an Epic is never worked.
    """

    NOT_BUILT: ClassVar[dict[Tier, str]] = {
        Tier.QUESTION: "the L flow (Question, Incident)",
        Tier.LOGIC: "the M flow (Feature, Bug, Maintenance)",
    }

    def __init__(self, github: Github | None = None, config: WorkConfig | None = None) -> None:
        self.github = github or Github()
        self.config = config or WorkConfig.load()

    def steps(self) -> list[StepOutline]:
        """What is known before the ticket is read: the first steps of the unit flow. The rest
        arrive as an `OutlineChanged` event once the flow is chosen."""
        return self._unit().steps()

    async def run(self, number: int, reporter: Reporter) -> WorkResult:
        ticket = await self.github.issue.get(number)
        tier = Route.tier(number, ticket.issue_type)
        if tier in self.NOT_BUILT:
            raise FlowError(
                f"#{number} is a {ticket.issue_type}: {self.NOT_BUILT[tier]} is not built yet"
            )
        return await self._unit().run(number, reporter)

    def _unit(self) -> UnitFlow:
        return UnitFlow(self.github, self.config)
