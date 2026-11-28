"""Command-line interface."""

from fugue.cli.app import app


def main() -> None:
    app()


__all__ = ["app", "main"]
