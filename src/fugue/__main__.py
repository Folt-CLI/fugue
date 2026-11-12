import argparse

from fugue import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fugue")
    parser.add_argument("--version", action="version", version=f"fugue {__version__}")
    parser.parse_args(argv)
    parser.print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
