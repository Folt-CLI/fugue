"""Command line entry point."""

from typing import Annotated

import typer

from fugue import __version__

app = typer.Typer(
    help="Take GitHub tickets to reviewed pull requests.",
    no_args_is_help=True,
    add_completion=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"fugue {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """Fugue."""


def main() -> None:
    app()
