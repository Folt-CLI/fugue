"""How a flow talks to whoever runs it."""

from abc import ABC, abstractmethod

from fugue.core.reporting.events import Event
from fugue.core.reporting.questions import Answer, Question


class Reporter(ABC):
    """An interface implements this to show events and answer questions.

    Core calls it and never prints or prompts itself. The command line shows events with
    Rich; a server could forward them as JSON; a script can use `SilentReporter`.
    """

    can_hold: bool = False
    """Whether the interface can leave a question waiting and bring it back later. A terminal
    prompt cannot, so a run that is declined there ends and is run again to continue."""

    @abstractmethod
    async def report(self, event: Event) -> None:
        """Something happened."""

    @abstractmethod
    async def ask(self, question: Question) -> Answer:
        """Stop and ask. Returns the answer."""


class SilentReporter(Reporter):
    """Shows nothing and answers every question with its default answer."""

    async def report(self, event: Event) -> None:
        return None

    async def ask(self, question: Question) -> Answer:
        return question.default_answer()
