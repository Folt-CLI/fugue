"""How a flow talks to whoever runs it."""

from abc import ABC, abstractmethod

from fugue.core.reporting.events import Event
from fugue.core.reporting.questions import Question


class Reporter(ABC):
    """An interface implements this to show events and answer questions.

    Core calls it and never prints or prompts itself. The command line shows events with
    Rich; a server could forward them as JSON; a script can use `SilentReporter`.
    """

    @abstractmethod
    async def report(self, event: Event) -> None:
        """Something happened."""

    @abstractmethod
    async def ask(self, question: Question) -> str:
        """Stop and ask. Returns one of the question's choices."""


class SilentReporter(Reporter):
    """Shows nothing and answers every question with its default."""

    async def report(self, event: Event) -> None:
        return None

    async def ask(self, question: Question) -> str:
        return question.default
