"""`fugue work`: take one ticket to a pull request."""

import asyncio
import sys
from typing import Annotated

import typer

from fugue.cli.reporter import RichReporter
from fugue.cli.tui import WorkApp
from fugue.cli.views import Messages, WorkView
from fugue.config import GithubConfig
from fugue.core.github import Github
from fugue.core.work import Work, WorkConfig, WorkResult


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless",
            envvar="FUGUE_HEADLESS",
            help="Run without the full screen: print progress as lines.",
        ),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            envvar="FUGUE_WORK_AUTO",
            help="Answer the permission questions (continue with each step) by itself.",
        ),
    ] = False,
    project: Annotated[
        int | None,
        typer.Option(
            "--project",
            "-p",
            help="Project number for the ticket's status. Default: GITHUB_PROJECT, or fugue.toml.",
        ),
    ] = None,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Show no progress, only the final line.")
    ] = False,
) -> None:
    """Work one ticket."""
    messages = Messages()
    with messages.guard():
        config = WorkConfig.load()
        if auto:
            config = config.model_copy(update={"auto": True})
        github_config = GithubConfig.load()
        if project is not None:
            github_config = github_config.model_copy(update={"project": project})
        flow = Work(Github(github_config), config)
        if headless or quiet or not (sys.stdin.isatty() and sys.stdout.isatty()):
            result = asyncio.run(flow.run(ticket, RichReporter(quiet=quiet)))
        else:
            result = _in_screen(flow, ticket)
    WorkView().result(result.repository, result.ticket.number, result.status)


def _in_screen(flow: Work, ticket: int) -> WorkResult:
    """Run the flow in the Textual app. Its error is raised once the screen is closed, so
    it is shown in the terminal like any other."""
    app = WorkApp(flow, ticket)
    app.run()
    if app.error is not None:
        raise app.error
    if app.result is None:
        raise typer.Exit(1)
    return app.result
