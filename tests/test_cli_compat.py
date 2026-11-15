"""Reusable old-name compatibility assertions and isolated harness self-tests."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from fugue.cli import app as cli_app
from fugue.cli import overview as overview_cli
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


@pytest.mark.parametrize(
    ("old", "new", "args", "code"),
    [
        ("status", "runs", [], 0),
        ("status", "runs", ["1"], 0),
        ("status", "runs", ["9"], 2),
        ("usage", "usage", ["--by", "ticket", "--ticket", "5"], 0),
        ("timing", "timing", ["1"], 0),
        ("timing", "timing", ["9"], 2),
        ("overview", "overview", ["--no-summary"], 0),
    ],
)
def test_report_old_names_match_new_names(
    old: str,
    new: str,
    args: list[str],
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_cli_overview import CONFIG, full_port, install

    from fugue.store import Store

    state_home = tmp_path / "state"
    state_dir = state_home / "fugue"
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "fugue.toml").write_text(CONFIG)
    with Store(state_dir / "fugue.db") as store:
        run = store.create_run(5, "o/r")
        store.record_usage(run.id, "implementer", "fake", "subscription_login", input_tokens=7)
        store.finish_attempt(store.begin_attempt(run.id, "load").id, "completed")
    install(monkeypatch, full_port())

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "FixedDatetime":
            return cls(2026, 1, 1, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(overview_cli, "datetime", FixedDatetime)
    options = [*args, "--repo-root", str(root)]
    assert_same_behaviour([old, *options], ["report", new, *options])
    legacy = runner.invoke(cli_app.app, [old, *options])
    current = runner.invoke(cli_app.app, ["report", new, *options])
    assert current.exit_code == code, current.output
    notice = (
        f"warning: fugue {old} is deprecated; use fugue report {new} instead. "
        "It will be removed in 0.4.0.\n"
    )
    assert legacy.stderr == notice + current.stderr
    assert not current.stderr.startswith("warning:")


@pytest.mark.parametrize(
    ("old", "new", "args", "action"),
    [
        (["review"], ["review", "start"], [], "review"),
        (["decide"], ["review", "decide"], ["--accept", "1"], "decide"),
        (["request-approval"], ["approval", "request"], [], "request-approval"),
        (["approve"], ["approval", "record"], ["--reason", "ready", "--by", "user"], "approve"),
    ],
)
def test_review_approval_old_names_match_new_names(
    old: list[str],
    new: list[str],
    args: list[str],
    action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_cli_flow import FakeEngine

    fake = FakeEngine()
    monkeypatch.setattr(cli_app, "engine_factory", lambda *args: fake)
    options = ["3", *args, "--repo-root", str(tmp_path)]
    assert_same_behaviour([*old, *options], [*new, *options])
    assert [name for name, _ in fake.calls] == [action, action]
    if action == "review":
        assert fake.calls[0] == ("review", 3)
    legacy = runner.invoke(cli_app.app, [*old, *options])
    current = runner.invoke(cli_app.app, [*new, *options])
    old_name = " ".join(old) + (" RUN" if action == "review" else "")
    new_name = " ".join(new) + (" RUN" if action == "review" else "")
    assert legacy.stderr == (
        f"warning: fugue {old_name} is deprecated; use fugue {new_name} instead. "
        "It will be removed in 0.4.0.\n" + current.stderr
    )
    assert current.exit_code == 0


def test_review_approval_help_hides_old_names() -> None:
    result = runner.invoke(cli_app.app, ["--help"])
    assert result.exit_code == 0
    for old in ["decide", "request-approval", "approve"]:
        assert re.search(rf"^\s*│?\s*{old}\s", result.stdout, re.MULTILINE) is None
    for group, commands in [("review", ["start", "decide"]), ("approval", ["request", "record"])]:
        help_result = runner.invoke(cli_app.app, [group, "--help"])
        assert help_result.exit_code == 0
        for command in commands:
            assert command in help_result.stdout


@pytest.mark.parametrize("invalid", [False, True])
def test_setup_config_compatibility(tmp_path: Path, invalid: bool) -> None:
    config = tmp_path / "fugue.toml"
    config.write_text("invalid = [" if invalid else '[github]\nrepo = "o/r"\n')
    options = ["--config", str(config), "--repo-root", str(tmp_path)]
    assert_same_behaviour(["config", "show", *options], ["setup", "config", *options])
    legacy = runner.invoke(cli_app.app, ["config", "show", *options])
    current = runner.invoke(cli_app.app, ["setup", "config", *options])
    assert current.exit_code == (2 if invalid else 0)
    assert legacy.stderr == (
        "warning: fugue config show is deprecated; use fugue setup config instead. "
        "It will be removed in 0.4.0.\n" + current.stderr
    )


@pytest.mark.parametrize("code", [0, 3, 4])
def test_setup_protection_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    from test_cli_protection import FakeClient, failure, rules

    (tmp_path / "fugue.toml").write_text('[github]\nrepo = "o/r"\n')
    response = failure("HTTP 403") if code == 4 else rules(count=0 if code == 3 else 1)
    client = FakeClient({"custom": response})
    monkeypatch.setattr(cli_app, "GitHubClient", lambda *args: client)
    options = ["--repo-root", str(tmp_path), "--branch", "custom"]
    assert_same_behaviour(["protection", *options], ["setup", "protection", *options])
    legacy = runner.invoke(cli_app.app, ["protection", *options])
    current = runner.invoke(cli_app.app, ["setup", "protection", *options])
    assert current.exit_code == code
    assert legacy.stderr == (
        "warning: fugue protection is deprecated; use fugue setup protection instead. "
        "It will be removed in 0.4.0.\n" + current.stderr
    )


@pytest.mark.parametrize("code", [0, 2, 4, 6])
def test_setup_reviewer_app_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    from fugue.cli import reviewer_app as reviewer_command
    from fugue.config import FugueConfig

    calls: list[tuple[str | None, float]] = []

    def setup(config: FugueConfig, root: Path, *, org: str | None, timeout: float) -> int:
        assert root == tmp_path
        calls.append((org, timeout))
        terminal.print_text("reviewer app setup")
        if code == 2:
            raise ValueError("invalid setup")
        if code == 4:
            raise RuntimeError("setup failed")
        if code == 6:
            raise KeyboardInterrupt
        return 0

    monkeypatch.setattr(reviewer_command, "run_setup", setup)
    options = ["--repo-root", str(tmp_path), "--org", "X", "--timeout", "5"]
    assert_same_behaviour(["reviewer-app", "setup", *options], ["setup", "reviewer-app", *options])
    legacy = runner.invoke(cli_app.app, ["reviewer-app", "setup", *options])
    current = runner.invoke(cli_app.app, ["setup", "reviewer-app", *options])
    assert current.exit_code == code
    assert calls == [("X", 5.0)] * 4
    assert legacy.stderr == (
        "warning: fugue reviewer-app setup is deprecated; use fugue setup reviewer-app instead. "
        "It will be removed in 0.4.0.\n" + current.stderr
    )
