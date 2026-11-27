"""`fugue work`: take one ticket to a pull request."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.output import Output
from fugue.core.github import Github


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
) -> None:
    """Work one ticket."""
    output = Output()
    with output.guard():
        github = Github()
        found = asyncio.run(github.issue.get(ticket))
    output.ticket(found, github.slug)
