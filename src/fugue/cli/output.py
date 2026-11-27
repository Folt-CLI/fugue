"""Everything Fugue prints for a person, in one place."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import ClassVar

import typer
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from fugue.errors import FugueError
from fugue.models import Ticket


class Output:
    """Rich output. Results go to stdout; problems go to stderr."""

    STATE_STYLE: ClassVar[dict[str, str]] = {"open": "bold green", "closed": "bold magenta"}

    def __init__(self, console: Console | None = None, errors: Console | None = None) -> None:
        self.console = console or Console()
        self.errors = errors or Console(stderr=True)

    def ticket(self, ticket: Ticket, repository: str, *, heading: str | None = None) -> None:
        """Show a ticket as a panel: state, title, labels, then the body as Markdown."""
        title = Text()
        title.append(f" {ticket.state.upper()} ", style=f"reverse {self.STATE_STYLE[ticket.state]}")
        title.append("  ")
        title.append(ticket.title, style="bold")
        parts: list[RenderableType] = [title]
        if ticket.labels:
            parts.append(self._labels(ticket.labels))
        parts.append(Text())
        parts.append(
            Markdown(ticket.body) if ticket.body.strip() else Text("No description.", "dim")
        )
        self.console.print(
            Panel(
                Group(*parts),
                title=heading or f"[bold cyan]{repository}#{ticket.number}[/]",
                subtitle=f"[link={ticket.url}]{ticket.url}[/link]",
                border_style=self.STATE_STYLE[ticket.state].replace("bold ", ""),
                padding=(1, 2),
            )
        )

    def preview(self, title: str, body: str, labels: Sequence[str], repository: str) -> None:
        """Show an issue that has not been created."""
        parts: list[RenderableType] = [Text(title, style="bold")]
        if labels:
            parts.append(self._labels(labels))
        parts.append(Text())
        parts.append(self._body(body))
        self.console.print(
            Panel(
                Group(*parts),
                title=f"[bold yellow]Would create in {repository}[/]",
                border_style="yellow",
                padding=(1, 2),
            )
        )

    def success(self, message: str) -> None:
        self.console.print(f"[bold green]✓[/] {message}")

    def error(self, message: str) -> None:
        self.errors.print(Panel(Text(message), title="[bold red]fugue[/]", border_style="red"))

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Show a FugueError as a red panel and exit with code 1."""
        try:
            yield
        except FugueError as error:
            self.error(str(error))
            raise typer.Exit(1) from error

    @staticmethod
    def _body(text: str) -> RenderableType:
        return Markdown(text) if text.strip() else Text("No description.", style="dim")

    @staticmethod
    def _labels(labels: Sequence[str]) -> Text:
        line = Text()
        for label in labels:
            line.append(f" {label} ", style="black on cyan")
            line.append(" ")
        return line
