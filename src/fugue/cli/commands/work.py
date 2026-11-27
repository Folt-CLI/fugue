"""`fugue work`: take one ticket to a pull request."""

import asyncio
from typing import Annotated

import typer

from fugue.errors import FugueError
from fugue.github import Github


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
) -> None:
    """Work one ticket."""
    try:
        github = Github()
        found = asyncio.run(github.issue.get(ticket))
    except FugueError as error:
        typer.echo(f"fugue: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"{github.slug}#{found.number} [{found.state}] {found.title}")
    if found.labels:
        typer.echo(f"labels: {', '.join(found.labels)}")
    typer.echo(f"url: {found.url}")
    typer.echo()
    typer.echo(found.body or "(no description)")
