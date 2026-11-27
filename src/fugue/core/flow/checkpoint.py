"""The stop between steps."""

from fugue.core.reporting import (
    HOLD,
    PERMISSION,
    Ask,
    Reporter,
    RunResumed,
    RunStopped,
    StopReason,
)
from fugue.errors import FlowStopped


class Checkpoint:
    """Asks the person running a flow before each step, except the first, whether to go on.

    One is made for each run and handed to its steps. Answering no stops the run. The run is
    then Stopped, not Finished: it waits before the step, with nothing lost, and the same
    question comes back whenever the person asks for it. They can answer no again, as often as
    they like; a yes goes on. A run started with `auto` answers the first question itself (it is
    tagged as a permission) and never stops here.

    An interface that cannot leave a question waiting (a terminal prompt) ends the run instead,
    by raising `FlowStopped`; running the command again continues from where it stopped.
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
        question = Ask(text=f"Continue with \u201c{step}\u201d?", default="yes", tags=(PERMISSION,))
        answer = await self._reporter.ask(question)
        if answer.value == "yes":
            return
        message = f"Stopped before \u201c{step}\u201d."
        if not self._reporter.can_hold:
            raise FlowStopped(message)
        await self._reporter.report(
            RunStopped(reason=StopReason.DECLINED, message=message, step=step)
        )
        held = question.model_copy(update={"tags": (HOLD,)})
        while answer.value != "yes":
            answer = await self._reporter.ask(held)
        await self._reporter.report(RunResumed(step=step))
