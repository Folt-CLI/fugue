"""`fugue work`: take one ticket to a pull request."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.output import Output
from fugue.cli.reporter import RichReporter
from fugue.core.work import Work


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Show no progress, only the final line.")
    ] = False,
) -> None:
    """Work one ticket."""
    output = Output()
    reporter = RichReporter(output, quiet=quiet)
    with output.guard():
        result = asyncio.run(Work().run(ticket, reporter))
    output.work_result(result.repository, result.ticket.number, result.status)
