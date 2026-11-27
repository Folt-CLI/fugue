"""`fugue work`: take one ticket to a pull request."""

from typing import Annotated

import typer

from fugue.context import ContextError, resolve


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
) -> None:
    """Work one ticket."""
    try:
        context = resolve()
    except ContextError as error:
        typer.echo(f"fugue: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"repository: {context.slug}")
    typer.echo(f"root:       {context.root}")
    typer.echo(f"token:      set ({len(context.token)} characters)")
    typer.echo(f"ticket:     #{ticket}")
