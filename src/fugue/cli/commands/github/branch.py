"""`fugue github branch`: branches on GitHub."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.output import Output
from fugue.core.github import Github

app = typer.Typer(help="List and delete branches on GitHub.", no_args_is_help=True)


@app.command("list")
def list_(
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many to show.")] = 100,
) -> None:
    """List the branches."""
    output = Output()
    with output.guard():
        github = Github()
        branches = asyncio.run(github.branch.list(limit=limit))
        default = asyncio.run(github.branch.default())
    output.branches(branches, default)


@app.command()
def delete(
    name: Annotated[str, typer.Argument(help="Branch name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Delete a branch on GitHub. This cannot be undone."""
    output = Output()
    with output.guard():
        github = Github()
        output.confirm(f"Delete branch {name!r} from {github.slug}?", yes=yes)
        asyncio.run(github.branch.delete(name))
    output.success(f"Deleted branch {name} from {github.slug}")
