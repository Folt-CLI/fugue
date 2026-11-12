"""CLI behaviour with a fake engine; no network, providers or real GitHub."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from fugue import __version__
from fugue.cli import app as cli_app
from fugue.cli import render, wiring
from fugue.config import FugueConfig, RoleConfig
from fugue.engine import RunOutcome
from fugue.models import AuthenticationMode, Role
from fugue.store import Store
from fugue.workflow import RunState

runner = CliRunner()
TOKEN = "ghp_secret_token_value"


class FakeEngine:
    def __init__(
        self,
        outcome: RunOutcome | BaseException,
        progress: Callable[[str], None],
    ) -> None:
        self.outcome = outcome
        self.progress = progress
        self.closed = False
        self.tickets: list[int] = []

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        self.tickets.append(ticket_number)
        self.progress("load: started")
        self.progress("load: completed")
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        self.closed = True


def install_engine(
    monkeypatch: pytest.MonkeyPatch, outcome: RunOutcome | BaseException
) -> list[FakeEngine]:
    engines: list[FakeEngine] = []

    def factory(
        config: FugueConfig, repo_root: Path, progress: Callable[[str], None]
    ) -> FakeEngine:
        engine = FakeEngine(outcome, progress)
        engines.append(engine)
        return engine

    monkeypatch.setattr(cli_app, "engine_factory", factory)
    return engines


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_home = tmp_path / "state-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    return state_home / "fugue"


def seed_store(state_dir: Path) -> None:
    state_dir.mkdir(parents=True)
    with Store(state_dir / "fugue.db") as store:
        first = store.create_run(5, "owner/repo")
        store.set_run_state(first.id, RunState.COMPLETED)
        second = store.create_run(6, "owner/repo")
        store.set_run_state(second.id, RunState.WAITING_FOR_CI)
        attempt = store.begin_attempt(second.id, "load")
        store.finish_attempt(attempt.id, "completed")
        operation = store.begin_operation(second.id, "push", "k1", {"branch": "b"})
        store.complete_operation(operation.id, {"done": True})
        store.record_approval(second.id, "merge", "pr-9", "abc123", "approved", "me", "ok")
        store.record_usage(second.id, "implementer", "codex", "subscription_login", input_tokens=42)


def run_command(tmp_path: Path, *extra: str) -> Result:
    return runner.invoke(cli_app.app, ["run", "36", "--repo-root", str(tmp_path), *extra])


def test_version_option() -> None:
    result = runner.invoke(cli_app.app, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"fugue {__version__}\n"


def test_config_show_defaults(tmp_path: Path) -> None:
    result = runner.invoke(cli_app.app, ["config", "show", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "[roles.implementer]" in result.output
    assert 'provider = "codex"' in result.output
    assert "max_repair_attempts = 3" in result.output
    assert TOKEN not in result.output


def test_config_show_reads_discovered_file(tmp_path: Path) -> None:
    (tmp_path / "fugue.toml").write_text('[github]\nrepo = "o/n"\nproject_number = 3\n')
    result = runner.invoke(cli_app.app, ["config", "show", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "[github]" in result.output
    assert 'repo = "o/n"' in result.output
    assert "project_number = 3" in result.output


def test_config_show_invalid_file_names_key_path(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[limits]\nmax_repair_attempts = 0\n")
    result = runner.invoke(cli_app.app, ["config", "show", "--config", str(path)])
    assert result.exit_code == 2
    assert "limits.max_repair_attempts" in result.output


def test_run_ready_ticket_prints_roles_progress_and_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fugue.toml").write_text(
        '[roles.reviewer]\nprovider = "claude"\nauthentication_mode = "api_key"\n'
    )
    engines = install_engine(
        monkeypatch, RunOutcome(1, RunState.WAITING_FOR_CI, pr_number=12, head_sha="deadbeef")
    )
    result = run_command(tmp_path)
    output = result.output
    assert result.exit_code == 0
    assert "analyst" in output
    assert "subscription_login" in output
    assert "warning: role reviewer uses api_key authentication" in output
    assert output.index("load: started") < output.index("load: completed")
    assert "pull request: #12" in output
    assert "head SHA: deadbeef" in output
    assert engines[0].tickets == [36]
    assert engines[0].closed
    assert TOKEN not in output


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (RunState.BLOCKED, 3),
        (RunState.FAILED, 4),
        (RunState.PROVIDER_LIMITED, 5),
        (RunState.INTERRUPTED, 6),
    ],
)
def test_run_outcome_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: RunState, code: int
) -> None:
    install_engine(monkeypatch, RunOutcome(2, state, problems=("something is wrong",)))
    result = run_command(tmp_path)
    assert result.exit_code == code
    assert "problem: something is wrong" in result.output


def test_run_keyboard_interrupt_exits_six(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engines = install_engine(monkeypatch, KeyboardInterrupt())
    result = run_command(tmp_path)
    assert result.exit_code == 6
    assert engines[0].closed


def test_run_engine_exception_exits_four(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_engine(monkeypatch, RuntimeError("boom"))
    result = run_command(tmp_path)
    assert result.exit_code == 4
    assert "boom" in result.output


def test_run_missing_project_is_usage_error(tmp_path: Path) -> None:
    (tmp_path / "fugue.toml").write_text('[github]\nrepo = "o/n"\n')
    result = run_command(tmp_path)
    assert result.exit_code == 2
    assert "project_number" in result.output


def test_run_outside_git_repository_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    result = runner.invoke(cli_app.app, ["run", "36"])
    assert result.exit_code == 2
    assert "Not in a Git repository" in result.output


def test_status_lists_recent_runs(isolated_state: Path, tmp_path: Path) -> None:
    seed_store(isolated_state)
    result = runner.invoke(cli_app.app, ["status", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "#5" in result.output
    assert "#6" in result.output
    assert result.output.index("#6") < result.output.index("#5")
    assert "waiting_for_ci" in result.output


def test_status_ticket_filter(isolated_state: Path, tmp_path: Path) -> None:
    seed_store(isolated_state)
    result = runner.invoke(cli_app.app, ["status", "--ticket", "5", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "#5" in result.output
    assert "#6" not in result.output


def test_status_without_store_is_empty(tmp_path: Path, isolated_state: Path) -> None:
    result = runner.invoke(cli_app.app, ["status", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "No runs." in result.output
    assert not isolated_state.exists()


def test_status_run_detail(isolated_state: Path, tmp_path: Path) -> None:
    seed_store(isolated_state)
    result = runner.invoke(cli_app.app, ["status", "2", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0
    output = result.output
    assert "stage attempts:" in output
    assert "load" in output
    assert "push" in output
    assert "k1" in output
    assert "abc123" in output
    assert "approved" in output
    assert "input_tokens: 42" in output
    assert "output_tokens: n/a" in output


def test_status_unknown_run_exits_two(isolated_state: Path, tmp_path: Path) -> None:
    seed_store(isolated_state)
    result = runner.invoke(cli_app.app, ["status", "99", "--repo-root", str(tmp_path)])
    assert result.exit_code == 2
    assert "Unknown run id 99" in result.output


def test_status_run_with_mismatched_ticket_exits_two(isolated_state: Path, tmp_path: Path) -> None:
    seed_store(isolated_state)
    result = runner.invoke(
        cli_app.app, ["status", "2", "--ticket", "5", "--repo-root", str(tmp_path)]
    )
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Folt-CLI/fugue.git",
        "https://github.com/Folt-CLI/fugue",
        "git@github.com:Folt-CLI/fugue.git",
        "ssh://git@github.com/Folt-CLI/fugue.git",
    ],
)
def test_parse_remote_forms(url: str) -> None:
    assert wiring.parse_remote(url) == "Folt-CLI/fugue"


def test_parse_remote_rejects_other_hosts() -> None:
    with pytest.raises(wiring.WiringError):
        wiring.parse_remote("https://example.com/o/n.git")


def test_resolve_repo_from_origin(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:o/name.git"],
        check=True,
    )
    assert wiring.resolve_repo(FugueConfig(), tmp_path) == "o/name"
    assert wiring.resolve_repo_root(None, tmp_path) == tmp_path.resolve()


def test_default_worktrees_root_and_state_dir(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    assert wiring.default_worktrees_root(root, "o/fugue") == tmp_path / "fugue-worktrees"
    config = FugueConfig()
    home = Path("/home/u")
    assert wiring.resolve_state_dir(config, {"XDG_STATE_HOME": "/xdg"}, home) == Path("/xdg/fugue")
    assert wiring.resolve_state_dir(config, {}, home) == Path("/home/u/.local/state/fugue")
    configured = FugueConfig(state_dir=Path("/custom"))
    assert wiring.resolve_state_dir(configured, {"XDG_STATE_HOME": "/xdg"}, home) == Path("/custom")


def test_discover_config_prefers_option(tmp_path: Path) -> None:
    assert wiring.discover_config(None, tmp_path) is None
    (tmp_path / "fugue.toml").write_text("")
    assert wiring.discover_config(None, tmp_path) == tmp_path / "fugue.toml"
    other = tmp_path / "other.toml"
    assert wiring.discover_config(other, tmp_path) == other


def test_build_engine_requires_project(tmp_path: Path) -> None:
    with pytest.raises(wiring.WiringError):
        wiring.build_engine(FugueConfig(), tmp_path, env={}, home=tmp_path)


def test_render_roles_without_api_key_has_no_warning() -> None:
    config = FugueConfig(
        roles={Role.ANALYST: RoleConfig(provider="claude")},
    )
    assert "warning" not in render.render_roles(config)
    assert AuthenticationMode.SUBSCRIPTION_LOGIN.value in render.render_roles(config)


def test_render_config_has_no_secret_fields() -> None:
    text = render.render_config(FugueConfig())
    assert "token" not in text.replace("tokens", "").lower()
