"""`fugue github`: the Github class as commands."""

import typer

from fugue.cli.commands.github import branch, issue, label, pr, project, repo

app = typer.Typer(help="Work with GitHub directly.", no_args_is_help=True)
app.add_typer(issue.app, name="issue")
app.add_typer(pr.app, name="pr")
app.add_typer(label.app, name="label")
app.add_typer(branch.app, name="branch")
app.add_typer(project.app, name="project")
app.add_typer(repo.app, name="repo")
