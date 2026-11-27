"""`fugue github label`: the repository's labels."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.output import Output
from fugue.core.github import Github

app = typer.Typer(help="List, create and delete labels.", no_args_is_help=True)


@app.command("list")
def list_() -> None:
    """List the labels."""
    output = Output()
    with output.guard():
        github = Github()
        labels = asyncio.run(github.label.list())
    output.labels(labels)


@app.command()
def create(
    name: Annotated[str, typer.Argument(help="Label name.")],
    color: Annotated[str, typer.Option("--color", "-c", help="Hex color, e.g. d73a4a.")] = "ededed",
    description: Annotated[str, typer.Option("--description", "-d", help="What it is for.")] = "",
) -> None:
    """Create a label."""
    output = Output()
    with output.guard():
        github = Github()
        label = asyncio.run(github.label.create(name, color, description))
    output.success(f"Created label {label.name} in {github.slug}")
    output.labels([label])


@app.command()
def delete(
    name: Annotated[str, typer.Argument(help="Label name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Delete a label. It is removed from every issue."""
    output = Output()
    with output.guard():
        github = Github()
        output.confirm(f"Delete label {name!r} from {github.slug}?", yes=yes)
        asyncio.run(github.label.delete(name))
    output.success(f"Deleted label {name} from {github.slug}")
