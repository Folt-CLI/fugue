"""Reusable old-name compatibility assertions and isolated harness self-tests."""

from collections.abc import Sequence

import pytest
import typer
from typer.testing import CliRunner

from fugue.cli import app as cli_app
from fugue.cli import terminal

runner = CliRunner()


def assert_same_behaviour(old_args: Sequence[str], new_args: Sequence[str]) -> None:
    """Require equal stdout and exit codes, with a notice only for the old name."""
    old = runner.invoke(cli_app.app, list(old_args))
    new = runner.invoke(cli_app.app, list(new_args))
    assert old.stdout == new.stdout, "stdout differs"
    assert old.exit_code == new.exit_code, "exit code differs"
    old_notices = [
        line
        for line in old.stderr.splitlines()
        if line.startswith("warning:") and " is deprecated;" in line
    ]
    new_notices = [
        line
        for line in new.stderr.splitlines()
        if line.startswith("warning:") and " is deprecated;" in line
    ]
    assert len(old_notices) == 1, "old name must print exactly one deprecation notice"
    assert not new_notices, "new name must not print a deprecation notice"


@pytest.fixture
def compatibility_app(monkeypatch: pytest.MonkeyPatch) -> None:
    app = typer.Typer()

    @app.command("old")
    def old(output: str = "same", code: int = 0, notice: bool = True) -> None:
        if notice:
            terminal.deprecated_name("old", "new", "0.4.0")
        terminal.print_text(output)
        raise typer.Exit(code)

    @app.command("new")
    def new(output: str = "same", code: int = 0, notice: bool = False) -> None:
        if notice:
            terminal.deprecated_name("old", "new", "0.4.0")
        terminal.print_text(output)
        raise typer.Exit(code)

    monkeypatch.setattr(cli_app, "app", app)


@pytest.mark.usefixtures("compatibility_app")
@pytest.mark.parametrize("code", [0, 2])
def test_same_behaviour_accepts_matching_commands(code: int) -> None:
    assert_same_behaviour(["old", "--code", str(code)], ["new", "--code", str(code)])


@pytest.mark.usefixtures("compatibility_app")
@pytest.mark.parametrize(
    ("old_args", "new_args", "message"),
    [
        (["old", "--output", "different"], ["new"], "stdout differs"),
        (["old", "--code", "2"], ["new"], "exit code differs"),
        (["old", "--no-notice"], ["new"], "old name must print exactly one deprecation notice"),
        (["old"], ["new", "--notice"], "new name must not print a deprecation notice"),
    ],
)
def test_same_behaviour_rejects_incompatible_commands(
    old_args: list[str], new_args: list[str], message: str
) -> None:
    with pytest.raises(AssertionError, match=message):
        assert_same_behaviour(old_args, new_args)
