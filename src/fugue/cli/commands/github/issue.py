"""`fugue github issue`: read and change issues."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from fugue.cli.choices import StateFilter
from fugue.cli.inputs import Inputs
from fugue.cli.output import Output
from fugue.github import Github

app = typer.Typer(help="Read and change issues.", no_args_is_help=True)


@app.command()
def get(
    number: Annotated[int, typer.Argument(help="Issue number.")],
    comments: Annotated[bool, typer.Option("--comments", "-c", help="Also show comments.")] = False,
) -> None:
    """Show an issue."""
    output = Output()
    with output.guard():
        github = Github()
        ticket = asyncio.run(github.issue.get(number))
        found = asyncio.run(github.issue.comments(number)) if comments else []
    output.ticket(ticket, github.slug)
    if comments:
        output.comments(found)


@app.command("list")
def list_(
    state: Annotated[StateFilter, typer.Option("--state", "-s", help="Which issues.")] = (
        StateFilter.open
    ),
    label: Annotated[
        list[str] | None, typer.Option("--label", "-l", help="Only this label. Repeatable.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many to show.")] = 30,
) -> None:
    """List issues."""
    output = Output()
    with output.guard():
        github = Github()
        tickets = asyncio.run(github.issue.list(state=state.value, labels=label or [], limit=limit))
    output.tickets(tickets, github.slug)


@app.command()
def create(
    title: Annotated[str, typer.Argument(help="Issue title.")],
    body: Annotated[str, typer.Option("--body", "-b", help="Text of the issue.")] = "",
    body_file: Annotated[
        Path | None, typer.Option("--body-file", "-f", help="Read the text from a file.")
    ] = None,
    label: Annotated[
        list[str] | None, typer.Option("--label", "-l", help="A label. Repeatable.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the issue without creating it.")
    ] = False,
) -> None:
    """Create an issue."""
    output = Output()
    with output.guard():
        github = Github()
        text = Inputs.body(body, body_file)
        if dry_run:
            output.preview(title, text, label or [], f"Would create in {github.slug}")
            return
        ticket = asyncio.run(github.issue.create(title, text, labels=label or []))
    output.success(f"Created {github.slug}#{ticket.number}")
    output.ticket(ticket, github.slug)


@app.command()
def edit(
    number: Annotated[int, typer.Argument(help="Issue number.")],
    title: Annotated[str | None, typer.Option("--title", "-t", help="New title.")] = None,
    body: Annotated[str | None, typer.Option("--body", "-b", help="New text.")] = None,
    body_file: Annotated[
        Path | None, typer.Option("--body-file", "-f", help="Read the new text from a file.")
    ] = None,
    label: Annotated[
        list[str] | None, typer.Option("--label", "-l", help="Replace the labels. Repeatable.")
    ] = None,
) -> None:
    """Change an issue's title, text or labels."""
    output = Output()
    with output.guard():
        github = Github()
        text = Inputs.body(body or "", body_file) if body is not None or body_file else None
        ticket = asyncio.run(github.issue.edit(number, title=title, body=text, labels=label))
    output.success(f"Updated {github.slug}#{number}")
    output.ticket(ticket, github.slug)


@app.command()
def comment(
    number: Annotated[int, typer.Argument(help="Issue number.")],
    body: Annotated[str, typer.Argument(help="The comment.")],
) -> None:
    """Comment on an issue."""
    output = Output()
    with output.guard():
        github = Github()
        added = asyncio.run(github.issue.comment(number, body))
    output.success(f"Commented on {github.slug}#{number}")
    output.comment(added)


@app.command()
def close(
    number: Annotated[int, typer.Argument(help="Issue number.")],
    not_planned: Annotated[
        bool, typer.Option("--not-planned", help="Close as not planned.")
    ] = False,
) -> None:
    """Close an issue."""
    output = Output()
    with output.guard():
        github = Github()
        ticket = asyncio.run(
            github.issue.close(number, reason="not_planned" if not_planned else "completed")
        )
    output.success(
        f"Closed {github.slug}#{ticket.number} as {'not planned' if not_planned else 'completed'}"
    )


@app.command()
def reopen(number: Annotated[int, typer.Argument(help="Issue number.")]) -> None:
    """Reopen an issue."""
    output = Output()
    with output.guard():
        github = Github()
        ticket = asyncio.run(github.issue.reopen(number))
    output.success(f"Reopened {github.slug}#{ticket.number}")
