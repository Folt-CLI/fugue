"""The stop between steps."""

from fugue.core.reporting import PERMISSION, Ask, Reporter
from fugue.errors import FlowStopped


class Checkpoint:
    """Asks the person running a flow before each step, except the first, whether to go on.

    One is made for each run and handed to its steps. Answering no raises `FlowStopped`, which
    ends the run cleanly. When `enabled` is false it never asks, for runs nobody is watching.
    """

    def __init__(self, reporter: Reporter) -> None:
        self._reporter = reporter
        self._passed = 0

    async def pass_(self, step: str) -> None:
        """Called by a step before it starts."""
        first = self._passed == 0
        self._passed += 1
        if first:
            return
        answer = await self._reporter.ask(
            Ask(text=f"Continue with \u201c{step}\u201d?", default="yes", tags=(PERMISSION,))
        )
        if answer.value != "yes":
            raise FlowStopped(f"Stopped before \u201c{step}\u201d.")
