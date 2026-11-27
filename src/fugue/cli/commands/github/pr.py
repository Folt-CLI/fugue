"""`fugue github pr`: read and change pull requests."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from fugue.cli.choices import MergeMethod, StateFilter
from fugue.cli.inputs import Inputs
from fugue.cli.output import Output
from fugue.core.github import Github
from fugue.errors import GitHubError

app = typer.Typer(help="Read and change pull requests.", no_args_is_help=True)


@app.command()
def get(
    number: Annotated[int, typer.Argument(help="Pull request number.")],
    comments: Annotated[bool, typer.Option("--comments", "-c", help="Also show comments.")] = False,
) -> None:
    """Show a pull request."""
    output = Output()
    with output.guard():
        github = Github()
        pull = asyncio.run(github.pr.get(number))
        found = asyncio.run(github.pr.comments(number)) if comments else []
    output.pull_request(pull, github.slug)
    if comments:
        output.comments(found)


@app.command("list")
def list_(
    state: Annotated[StateFilter, typer.Option("--state", "-s", help="Which pull requests.")] = (
        StateFilter.open
    ),
    base: Annotated[str | None, typer.Option("--base", help="Only into this branch.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many to show.")] = 30,
) -> None:
    """List pull requests."""
    output = Output()
    with output.guard():
        github = Github()
        pulls = asyncio.run(github.pr.list(state=state.value, base=base, limit=limit))
    output.pull_requests(pulls, github.slug)


@app.command()
def create(
    title: Annotated[str, typer.Argument(help="Pull request title.")],
    head: Annotated[
        str | None, typer.Option("--head", help="Branch with the changes. Default: current branch.")
    ] = None,
    base: Annotated[
        str | None,
        typer.Option("--base", help="Branch to merge into. Default: repo default."),
    ] = None,
    body: Annotated[str, typer.Option("--body", "-b", help="Text of the pull request.")] = "",
    body_file: Annotated[
        Path | None, typer.Option("--body-file", "-f", help="Read the text from a file.")
    ] = None,
    draft: Annotated[bool, typer.Option("--draft", help="Open it as a draft.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the pull request without creating it.")
    ] = False,
) -> None:
    """Open a pull request."""
    output = Output()
    with output.guard():
        github = Github()
        source = head or github.repository.branch
        if not source:
            raise GitHubError("no branch to open from: check one out or give --head")
        target = base or asyncio.run(github.repo.info()).default_branch
        if source == target:
            raise GitHubError(f"{source} is the base branch: check out a branch or give --head")
        text = Inputs.body(body, body_file)
        if dry_run:
            output.preview(
                title,
                text,
                [],
                f"Would open in {github.slug}",
                f"{source} → {target}{' · draft' if draft else ''}",
            )
            return
        pull = asyncio.run(github.pr.create(title, source, target, text, draft=draft))
    output.success(f"Opened {github.slug}#{pull.number}")
    output.pull_request(pull, github.slug)


@app.command()
def edit(
    number: Annotated[int, typer.Argument(help="Pull request number.")],
    title: Annotated[str | None, typer.Option("--title", "-t", help="New title.")] = None,
    body: Annotated[str | None, typer.Option("--body", "-b", help="New text.")] = None,
    body_file: Annotated[
        Path | None, typer.Option("--body-file", "-f", help="Read the new text from a file.")
    ] = None,
    base: Annotated[str | None, typer.Option("--base", help="New base branch.")] = None,
) -> None:
    """Change a pull request's title, text or base branch."""
    output = Output()
    with output.guard():
        github = Github()
        text = Inputs.body(body or "", body_file) if body is not None or body_file else None
        pull = asyncio.run(github.pr.edit(number, title=title, body=text, base=base))
    output.success(f"Updated {github.slug}#{number}")
    output.pull_request(pull, github.slug)


@app.command()
def comment(
    number: Annotated[int, typer.Argument(help="Pull request number.")],
    body: Annotated[str, typer.Argument(help="The comment.")],
) -> None:
    """Comment on a pull request."""
    output = Output()
    with output.guard():
        github = Github()
        added = asyncio.run(github.pr.comment(number, body))
    output.success(f"Commented on {github.slug}#{number}")
    output.comment(added)


@app.command()
def close(number: Annotated[int, typer.Argument(help="Pull request number.")]) -> None:
    """Close a pull request without merging it."""
    output = Output()
    with output.guard():
        github = Github()
        pull = asyncio.run(github.pr.close(number))
    output.success(f"Closed {github.slug}#{pull.number}")


@app.command()
def reopen(number: Annotated[int, typer.Argument(help="Pull request number.")]) -> None:
    """Reopen a closed pull request."""
    output = Output()
    with output.guard():
        github = Github()
        pull = asyncio.run(github.pr.reopen(number))
    output.success(f"Reopened {github.slug}#{pull.number}")


@app.command()
def checks(number: Annotated[int, typer.Argument(help="Pull request number.")]) -> None:
    """Show the checks on a pull request's latest commit."""
    output = Output()
    with output.guard():
        github = Github()
        found = asyncio.run(github.pr.checks(number))
    output.checks(found, f"Checks on {github.slug}#{number}")


@app.command()
def merge(
    number: Annotated[int, typer.Argument(help="Pull request number.")],
    method: Annotated[MergeMethod, typer.Option("--method", "-m", help="How to merge.")] = (
        MergeMethod.merge
    ),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Merge a pull request. This cannot be undone."""
    output = Output()
    with output.guard():
        github = Github()
        pull = asyncio.run(github.pr.get(number))
        output.pull_request(pull, github.slug)
        output.confirm(f"Merge {github.slug}#{number} into {pull.base} ({method.value})?", yes=yes)
        merged = asyncio.run(github.pr.merge(number, method=method.value))
    output.success(f"Merged {github.slug}#{merged.number} into {merged.base}")
