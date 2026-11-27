"""`fugue github repo`: the repository itself."""

import asyncio

import typer

from fugue.cli.output import Output
from fugue.github import Github

app = typer.Typer(help="Show the repository.", no_args_is_help=True)


@app.command()
def info() -> None:
    """Show what GitHub knows about the repository."""
    output = Output()
    with output.guard():
        github = Github()
        found = asyncio.run(github.repo.info())
    output.repo(found)
