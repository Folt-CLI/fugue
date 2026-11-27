"""`fugue github branch`: branches on GitHub."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.views import BranchView, Messages
from fugue.core.github import Github

app = typer.Typer(help="List and delete branches on GitHub.", no_args_is_help=True)


@app.command("list")
def list_(
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many to show.")] = 100,
) -> None:
    """List the branches."""
    messages = Messages()
    with messages.guard():
        github = Github()
        branches = asyncio.run(github.branch.list(limit=limit))
        default = asyncio.run(github.branch.default())
    BranchView().table(branches, default)


@app.command()
def delete(
    name: Annotated[str, typer.Argument(help="Branch name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Delete a branch on GitHub. This cannot be undone."""
    messages = Messages()
    with messages.guard():
        github = Github()
        messages.confirm(f"Delete branch {name!r} from {github.slug}?", yes=yes)
        asyncio.run(github.branch.delete(name))
    messages.success(f"Deleted branch {name} from {github.slug}")
