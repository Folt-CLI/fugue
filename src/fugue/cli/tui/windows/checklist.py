"""The window that lists every step of the flow and where the run is in it."""

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from fugue.cli.tui.windows.window import Window
from fugue.core.flow import StepOutline

type State = Literal["pending", "running", "done", "failed"]


@dataclass
class Line:
    """A step, or a part of one, and how far along it is."""

    state: State = "pending"
    detail: str = ""


@dataclass
class Row:
    """A step with the parts it is made of, which show under it."""

    line: Line = field(default_factory=Line)
    parts: dict[str, Line] = field(default_factory=dict)


class ChecklistWindow(Window):
    """Every step of the flow, from the start: waiting, running with a spinner, done or failed.
    A step that is made of parts, such as a gate and its checks, lists them under it.

    It is given the steps up front, so it shows the whole workflow before anything has run.
    """

    title = "Checklist"
    key = "c"

    FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, steps: list[StepOutline]) -> None:
        super().__init__()
        self._rows: dict[str, Row] = {
            step.name: Row(parts={part: Line() for part in step.parts}) for step in steps
        }
        self._frame = 0
        self._list = Static()

    def compose(self) -> ComposeResult:
        yield self._list

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)
        self._draw()

    @property
    def total(self) -> int:
        return len(self._rows)

    def position(self, step: str) -> int:
        """Where the step is in the checklist, counting from 1."""
        return list(self._rows).index(step) + 1 if step in self._rows else 0

    def start(self, step: str) -> None:
        self._row(step).line = Line("running")
        self._draw()

    def finish(self, step: str, detail: str = "") -> None:
        self._row(step).line = Line("done", detail)
        self._draw()

    def fail(self, step: str) -> None:
        self._row(step).line = Line("failed")
        self._draw()

    def start_part(self, step: str, part: str) -> None:
        self._row(step).parts[part] = Line("running")
        self._draw()

    def finish_part(self, step: str, part: str, *, ok: bool = True, detail: str = "") -> None:
        self._row(step).parts[part] = Line("done" if ok else "failed", detail)
        self._draw()

    def _row(self, step: str) -> Row:
        return self._rows.setdefault(step, Row())

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(self.FRAMES)
        if any(line.state == "running" for row in self._rows.values() for line in self._lines(row)):
            self._draw()

    @staticmethod
    def _lines(row: Row) -> list[Line]:
        return [row.line, *row.parts.values()]

    def _draw(self) -> None:
        text = Text()
        for name, row in self._rows.items():
            self._line(text, name, row.line, indent=0, inline=bool(row.parts))
            for part, line in row.parts.items():
                self._line(text, part, line, indent=2)
        self._list.update(text)

    def _line(
        self, text: Text, name: str, line: Line, *, indent: int, inline: bool = False
    ) -> None:
        """One line; its detail goes on the same line when the step has parts under it, so it
        is not mistaken for one of them."""
        pad = " " * indent
        match line.state:
            case "pending":
                text.append(f"{pad}○ ", style="dim")
                text.append(name, style="dim")
            case "running":
                text.append(f"{pad}{self.FRAMES[self._frame]} ", style="cyan")
                text.append(name, style="bold")
            case "done":
                text.append(f"{pad}✓ ", style="bold green")
                text.append(name)
            case "failed":
                text.append(f"{pad}✗ ", style="bold red")
                text.append(name, style="red")
        if line.detail:
            text.append(
                f" · {line.detail}" if inline else f"\n{pad}\u00a0\u00a0{line.detail}", style="dim"
            )
        text.append("\n")
