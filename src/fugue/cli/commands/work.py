"""`fugue work`: take one ticket to a pull request."""

from typing import Annotated

import typer


def work(
    ticket: Annotated[int, typer.Argument(help="Ticket number.")],
) -> None:
    """Work one ticket."""
    typer.echo(f"work #{ticket}: not implemented yet")
