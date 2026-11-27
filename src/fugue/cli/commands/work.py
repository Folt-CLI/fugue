"""`fugue work`: take one ticket to a pull request."""

import asyncio
import sys
from typing import Annotated

import typer

from fugue.cli.reporter import RichReporter
from fugue.cli.tui import WorkApp
from fugue.cli.views import Messages, WorkView
from fugue.core.work import Work, WorkResult


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
    plain: Annotated[
        bool, typer.Option("--plain", help="Print progress as lines instead of the full screen.")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Show no progress, only the final line.")
    ] = False,
) -> None:
    """Work one ticket."""
    messages = Messages()
    with messages.guard():
        flow = Work()
        if plain or quiet or not (sys.stdin.isatty() and sys.stdout.isatty()):
            result = asyncio.run(flow.run(ticket, RichReporter(quiet=quiet)))
        else:
            result = _in_screen(flow, ticket)
    WorkView().result(result.repository, result.ticket.number, result.status)


def _in_screen(flow: Work, ticket: int) -> WorkResult:
    """Run the flow in the Textual app. Its error is raised once the screen is closed, so
    it is shown in the terminal like any other."""
    app = WorkApp(flow, ticket)
    app.run()
    if app.error is not None:
        raise app.error
    if app.result is None:
        raise typer.Exit(1)
    return app.result
