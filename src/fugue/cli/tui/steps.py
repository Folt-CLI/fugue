"""The list of steps a run goes through, with their state."""

from typing import ClassVar, Literal

from rich.text import Text
from textual.widgets import Static

type StepState = Literal["running", "done", "failed"]


class StepList(Static):
    """Shows each step as it starts: a spinner while it runs, then a tick or a cross."""

    FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._steps: dict[str, tuple[StepState, str]] = {}
        self._frame = 0

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)
        self._draw()

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
        self.update(text)
