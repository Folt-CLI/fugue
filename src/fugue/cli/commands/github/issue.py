"""`fugue github issue`: read and create issues."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from fugue.cli.output import Output
from fugue.github import Github

app = typer.Typer(help="Read and create issues.", no_args_is_help=True)


@app.command()
def get(number: Annotated[int, typer.Argument(help="Issue number.")]) -> None:
    """Show an issue."""
    output = Output()
    with output.guard():
        github = Github()
        ticket = asyncio.run(github.issue.get(number))
    output.ticket(ticket, github.slug)


@app.command()
def create(
    title: Annotated[str, typer.Argument(help="Issue title.")],
    body: Annotated[str, typer.Option("--body", "-b", help="Issue body.")] = "",
    body_file: Annotated[
        Path | None, typer.Option("--body-file", "-f", help="Read the body from a file.")
    ] = None,
    label: Annotated[
        list[str] | None, typer.Option("--label", "-l", help="Label to add. Repeatable.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the issue without creating it.")
    ] = False,
) -> None:
    """Create an issue."""
    output = Output()
    with output.guard():
        github = Github()
        text = body_file.read_text(encoding="utf-8") if body_file else body
        labels = label or []
        if dry_run:
            output.preview(title, text, labels, github.slug)
            return
        ticket = asyncio.run(github.issue.create(title, text, labels=labels))
    output.success(f"Created {github.slug}#{ticket.number}")
    output.ticket(ticket, github.slug)
