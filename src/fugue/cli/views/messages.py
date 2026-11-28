"""Messages: success lines, errors, confirmations."""

from collections.abc import Iterator
from contextlib import contextmanager

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from fugue.cli.views.view import View
from fugue.errors import FlowStopped, FugueError


class Messages(View):
    """What a command says about itself. Errors go to stderr."""

    def __init__(self, console: Console | None = None, errors: Console | None = None) -> None:
        super().__init__(console)
        self.errors = errors or Console(stderr=True)

    def success(self, message: str) -> None:
        self.console.print(f"[bold green]✓[/] {message}")

    def warning(self, message: str) -> None:
        self.console.print(f"[yellow]![/] {message}")

    def error(self, message: str) -> None:
        self.errors.print(Panel(Text(message), title="[bold red]fugue[/]", border_style="red"))

    def confirm(self, question: str, *, yes: bool = False) -> None:
        """Ask before something that cannot be undone; exit 1 unless the answer is yes."""
        if yes:
            return
        if not typer.confirm(question):
            self.console.print("[dim]Nothing changed.[/]")
            raise typer.Exit(1)

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Show a FugueError as a red panel and exit with code 1."""
        try:
            yield
        except FlowStopped as stopped:
            self.console.print(f"[yellow]{stopped}[/]")
            raise typer.Exit(1) from stopped
        except FugueError as error:
            self.error(str(error))
            raise typer.Exit(1) from error
