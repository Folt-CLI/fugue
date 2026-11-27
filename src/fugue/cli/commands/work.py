"""`fugue work`: take one ticket to a pull request."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.reporter import RichReporter
from fugue.cli.views import Messages, WorkView
from fugue.core.work import Work


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Show no progress, only the final line.")
    ] = False,
) -> None:
    """Work one ticket."""
    messages = Messages()
    reporter = RichReporter(quiet=quiet)
    with messages.guard():
        result = asyncio.run(Work().run(ticket, reporter))
    WorkView().result(result.repository, result.ticket.number, result.status)
