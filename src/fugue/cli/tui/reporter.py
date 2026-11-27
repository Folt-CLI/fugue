"""Hands a flow's events and questions to the Textual app."""

from typing import TYPE_CHECKING

from fugue.core.reporting import Answer, Event, Question, Reporter

if TYPE_CHECKING:
    from fugue.cli.tui.app import WorkApp


class TuiReporter(Reporter):
    """The reporter a flow gets when it runs inside the app. It only forwards: the app
    decides how every event and question looks."""

    def __init__(self, app: "WorkApp") -> None:
        self._app = app

    async def report(self, event: Event) -> None:
        await self._app.show(event)

    async def ask(self, question: Question) -> Answer:
        return await self._app.ask(question)
