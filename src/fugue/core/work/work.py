"""`fugue work N`: take one ticket through the whole flow."""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from fugue.core.github import Github
from fugue.core.reporting import Reporter, StepFailed, StepFinished, StepStarted
from fugue.core.work.config import WorkConfig
from fugue.core.work.run import Run
from fugue.core.work.steps import MoveStatus, ReadTicket, Step
from fugue.errors import FugueError, WorkError
from fugue.models import Ticket


class WorkResult(BaseModel):
    """What a finished work run leaves behind."""

    model_config = ConfigDict(frozen=True)

    repository: str
    ticket: Ticket
    status: str | None = None


class Work:
    """Runs the steps in order. A step that cannot go on raises, and the run stops there.

    result = await Work().run(5, reporter)
    """

    def __init__(self, github: Github | None = None, config: WorkConfig | None = None) -> None:
        self.github = github or Github()
        self.config = config or WorkConfig.load()
        self.steps: Sequence[Step] = (ReadTicket(), MoveStatus())

    async def run(self, number: int, reporter: Reporter) -> WorkResult:
        run = Run(number, self.github, self.config, reporter)
        for step in self.steps:
            await reporter.report(StepStarted(step=step.name))
            try:
                detail = await step.run(run)
            except FugueError as error:
                await reporter.report(StepFailed(step=step.name, message=str(error)))
                raise
            except Exception as error:
                message = f"{step.name} failed unexpectedly: {error!r}"
                await reporter.report(StepFailed(step=step.name, message=message))
                raise WorkError(message) from error
            await reporter.report(StepFinished(step=step.name, detail=detail))
        return WorkResult(repository=self.github.slug, ticket=run.read_ticket, status=run.status)
