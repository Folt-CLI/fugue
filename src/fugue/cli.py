"""Command line entry point."""

import argparse
from collections.abc import Sequence

from fugue import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fugue", description=__doc__)
    parser.add_argument("--version", action="version", version=f"fugue {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
