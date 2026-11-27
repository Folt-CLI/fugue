"""The stop between steps."""

from fugue.core.reporting import PERMISSION, Ask, Reporter, RunPaused, RunResumed
from fugue.errors import FlowStopped


class Checkpoint:
    """Asks the person running a flow before each step, except the first, whether to go on.

    One is made for each run and handed to its steps. Answering no pauses the run: it waits
    before the step, with nothing lost, until the person chooses to continue or to stop.
    Stopping raises `FlowStopped`, which ends the run cleanly. A run started with `auto`
    answers the first question itself (it is tagged as a permission) and never pauses.
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
        if answer.value == "yes":
            return
        await self._pause(step)

    async def _pause(self, step: str) -> None:
        """Wait before the step until the person continues; stopping ends the run."""
        await self._reporter.report(RunPaused(step=step))
        answer = await self._reporter.ask(
            Ask(
                text=f"Paused before \u201c{step}\u201d.",
                body="The run waits here and nothing is lost. Look around, then continue "
                "when you are ready, or stop the run.",
                options=("continue", "stop"),
                default="continue",
            )
        )
        if answer.value != "continue":
            raise FlowStopped(f"Stopped before \u201c{step}\u201d.")
        await self._reporter.report(RunResumed(step=step))
