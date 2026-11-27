"""The window that lists every step of the flow and where the run is in it."""

from typing import ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from fugue.cli.tui.windows.window import Window

type StepState = Literal["pending", "running", "done", "failed"]


class ChecklistWindow(Window):
    """Every step of the flow, from the start: waiting, running with a spinner, done or failed.

    It is given the steps up front, so it shows the whole workflow before anything has run.
    """

    title = "Checklist"
    key = "c"

    FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, steps: list[str]) -> None:
        super().__init__()
        self._steps: dict[str, tuple[StepState, str]] = {step: ("pending", "") for step in steps}
        self._frame = 0
        self._list = Static()

    def compose(self) -> ComposeResult:
        yield self._list

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)
        self._draw()

    @property
    def total(self) -> int:
        return len(self._steps)

    def position(self, step: str) -> int:
        """Where the step is in the checklist, counting from 1."""
        return list(self._steps).index(step) + 1 if step in self._steps else 0

    def start(self, step: str) -> None:
        self._steps[step] = ("running", "")
        self._draw()

    def finish(self, step: str, detail: str = "") -> None:
        self._steps[step] = ("done", detail)
        self._draw()

    def fail(self, step: str) -> None:
        self._steps[step] = ("failed", "")
        self._draw()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(self.FRAMES)
        if any(state == "running" for state, _ in self._steps.values()):
            self._draw()

    def _draw(self) -> None:
        text = Text()
        for step, (state, detail) in self._steps.items():
            match state:
                case "pending":
                    text.append("○ ", style="dim")
                    text.append(step, style="dim")
                case "running":
                    text.append(f"{self.FRAMES[self._frame]} ", style="cyan")
                    text.append(step, style="bold")
                case "done":
                    text.append("✓ ", style="bold green")
                    text.append(step)
                case "failed":
                    text.append("✗ ", style="bold red")
                    text.append(step, style="red")
            if detail:
                text.append(f"\n  {detail}", style="dim")
            text.append("\n")
        self._list.update(text)
