"""`fugue github`: the Github class as commands."""

import typer

from fugue.cli.commands.github import issue

app = typer.Typer(help="Work with GitHub directly.", no_args_is_help=True)
app.add_typer(issue.app, name="issue")
