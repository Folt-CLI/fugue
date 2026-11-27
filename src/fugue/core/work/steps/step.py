"""One step of the work flow."""

from abc import ABC, abstractmethod
from typing import ClassVar

from fugue.core.work.run import Run


class Step(ABC):
    """A step does one thing, reports what it needs to, and raises a FugueError if it cannot."""

    name: ClassVar[str]

    @abstractmethod
    async def run(self, run: Run) -> str:
        """Do the step. Returns a short note for the report, or an empty string."""
