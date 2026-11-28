"""`fugue github label`: the repository's labels."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.views import LabelView, Messages
from fugue.core.github import Github

app = typer.Typer(help="List, create and delete labels.", no_args_is_help=True)


@app.command("list")
def list_() -> None:
    """List the labels."""
    messages = Messages()
    with messages.guard():
        github = Github()
        labels = asyncio.run(github.label.list())
    LabelView().table(labels)


@app.command()
def create(
    name: Annotated[str, typer.Argument(help="Label name.")],
    color: Annotated[str, typer.Option("--color", "-c", help="Hex color, e.g. d73a4a.")] = "ededed",
    description: Annotated[str, typer.Option("--description", "-d", help="What it is for.")] = "",
) -> None:
    """Create a label."""
    messages = Messages()
    with messages.guard():
        github = Github()
        label = asyncio.run(github.label.create(name, color, description))
    messages.success(f"Created label {label.name} in {github.slug}")
    LabelView().table([label])


@app.command()
def delete(
    name: Annotated[str, typer.Argument(help="Label name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Delete a label. It is removed from every issue."""
    messages = Messages()
    with messages.guard():
        github = Github()
        messages.confirm(f"Delete label {name!r} from {github.slug}?", yes=yes)
        asyncio.run(github.label.delete(name))
    messages.success(f"Deleted label {name} from {github.slug}")
