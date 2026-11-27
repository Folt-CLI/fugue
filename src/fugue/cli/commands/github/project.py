"""`fugue github project`: GitHub projects and the items on them."""

import asyncio
from typing import Annotated

import typer

from fugue.cli.output import Output
from fugue.core.github import Github

app = typer.Typer(help="Work with GitHub projects.", no_args_is_help=True)


@app.command("list")
def list_() -> None:
    """List the projects of the repository's owner."""
    output = Output()
    with output.guard():
        github = Github()
        projects = asyncio.run(github.project.list())
    output.projects(projects, github.repository.owner)


@app.command()
def fields(
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
) -> None:
    """Show a project's fields and their options."""
    output = Output()
    with output.guard():
        github = Github()
        view = asyncio.run(github.project.overview(project))
    output.project_fields(
        view.fields, f"Fields of project {view.project.number}: {view.project.title}"
    )


@app.command()
def items(
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help='GitHub project filter, e.g. status:"S0: To Do".'),
    ] = None,
    column: Annotated[
        list[str] | None,
        typer.Option(
            "--column", "-c", help="Field to show. Repeatable. Default: Status, Priority, Size."
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many to show.")] = 50,
) -> None:
    """List the items on a project."""
    output = Output()
    with output.guard():
        github = Github()
        view = asyncio.run(github.project.table(project, query=query, columns=column, limit=limit))
    output.project_items(
        view.items, view.columns, f"Project {view.project.number}: {view.project.title}"
    )


@app.command()
def item(
    number: Annotated[int, typer.Argument(help="Issue or pull request number.")],
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
) -> None:
    """Show one item with all of its field values."""
    output = Output()
    with output.guard():
        github = Github()
        found = asyncio.run(github.project.details(number, project))
    output.project_item(found, f"{github.slug}#{number}")


@app.command()
def add(
    number: Annotated[int, typer.Argument(help="Issue number.")],
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
    pull_request: Annotated[
        bool, typer.Option("--pr", help="The number is a pull request.")
    ] = False,
) -> None:
    """Put an issue or pull request on a project."""
    output = Output()
    with output.guard():
        github = Github()
        found = asyncio.run(github.project.add(number, project, pull_request=pull_request))
    output.success(f"Added {github.slug}#{number} to the project")
    output.project_item(found, f"{github.slug}#{number}")


@app.command("set")
def set_(
    number: Annotated[int, typer.Argument(help="Issue or pull request number.")],
    field: Annotated[str, typer.Argument(help="Field name, e.g. Status.")],
    value: Annotated[str, typer.Argument(help="New value. A select field takes an option name.")],
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
) -> None:
    """Set a field of an item."""
    output = Output()
    with output.guard():
        github = Github()
        change = asyncio.run(github.project.set(number, field, value, project))
    output.success(f"{github.slug}#{number}: {change.field} = {change.value}")


@app.command()
def clear(
    number: Annotated[int, typer.Argument(help="Issue or pull request number.")],
    field: Annotated[str, typer.Argument(help="Field name.")],
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
) -> None:
    """Empty a field of an item."""
    output = Output()
    with output.guard():
        github = Github()
        asyncio.run(github.project.clear(number, field, project))
    output.success(f"{github.slug}#{number}: {field} cleared")


@app.command()
def remove(
    number: Annotated[int, typer.Argument(help="Issue or pull request number.")],
    project: Annotated[
        int | None, typer.Option("--project", "-p", help="Project number. Default: GITHUB_PROJECT.")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Take an item off a project. The issue itself is not touched."""
    output = Output()
    with output.guard():
        github = Github()
        output.confirm(f"Remove {github.slug}#{number} from the project?", yes=yes)
        asyncio.run(github.project.remove(number, project))
    output.success(f"Removed {github.slug}#{number} from the project")
