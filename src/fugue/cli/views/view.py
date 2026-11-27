"""The base of every view."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

from rich import box
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class View:
    """Shows one kind of thing. Holds the console and the pieces views have in common."""

    STATE_STYLE: ClassVar[dict[str, str]] = {
        "open": "green",
        "closed": "red",
        "merged": "magenta",
    }

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def _item(
        self,
        heading: str,
        state: str,
        title: str,
        meta: str,
        labels: Sequence[str],
        body: str,
        url: str,
    ) -> None:
        """Print an issue or pull request as a panel."""
        self.console.print(self._panel(heading, state, title, meta, labels, body, url))

    def _panel(
        self,
        heading: str,
        state: str,
        title: str,
        meta: str,
        labels: Sequence[str],
        body: str,
        url: str,
    ) -> Panel:
        """An issue or pull request as a panel."""
        style = self.STATE_STYLE.get(state, "yellow" if state == "draft" else "white")
        head = Text()
        head.append(f" {state.upper()} ", style=f"bold reverse {style}")
        head.append("  ")
        head.append(title, style="bold")
        parts: list[RenderableType] = [head, Text.from_markup(f"[dim]{meta}[/]")]
        if labels:
            parts.append(self._labels(labels))
        parts += [Text(), self._body(body)]
        return Panel(
            Group(*parts),
            title=f"[bold cyan]{heading}[/]",
            subtitle=f"[link={url}]{url}[/link]",
            border_style=style,
            padding=(1, 2),
        )

    def _state(self, state: str) -> str:
        style = self.STATE_STYLE.get(state, "yellow" if state == "draft" else "white")
        return f"[{style}]● {state}[/]"

    @staticmethod
    def _table(title: str) -> Table:
        return Table(title=title, title_justify="left", box=box.ROUNDED, expand=True)

    @staticmethod
    def _body(text: str) -> RenderableType:
        return Markdown(text) if text.strip() else Text("No description.", style="dim")

    @staticmethod
    def _chip(name: str, color: str | None = None) -> Text:
        """A label in its own colour, with text that stays readable on it."""
        if color is None:
            return Text(f" {name} ", style="black on cyan")
        red, green, blue = (int(color[i : i + 2], 16) for i in (0, 2, 4))
        light = (0.299 * red + 0.587 * green + 0.114 * blue) > 150
        return Text(f" {name} ", style=f"{'black' if light else 'white'} on #{color}")

    def _labels(self, labels: Sequence[str]) -> Text:
        line = Text()
        for label in labels:
            line.append_text(self._chip(label))
            line.append(" ")
        return line

    @staticmethod
    def _ago(moment: datetime) -> str:
        seconds = int((datetime.now(UTC) - moment).total_seconds())
        for size, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"), (3600, "h")):
            if seconds >= size:
                return f"{seconds // size}{unit} ago"
        return "just now" if seconds < 60 else f"{seconds // 60}m ago"
