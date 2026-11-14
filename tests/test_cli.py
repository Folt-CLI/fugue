"""CLI behaviour with a fake engine; no network, providers or real GitHub."""

import asyncio
import json
import os
import sqlite3
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner, Result

from fugue import __version__
from fugue.cli import app as cli_app
from fugue.cli import render, wiring
from fugue.config import FugueConfig, RoleConfig
from fugue.engine import RunOutcome
from fugue.engine.locks import TicketLock
from fugue.github import GitHubClient
from fugue.models import AuthenticationMode, IssueType, Priority, Role, Size, Status, Ticket
from fugue.store import Store
from fugue.workflow import RunState

runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"})
TOKEN = "ghp_secret_token_value"


Outcomes = RunOutcome | BaseException
Hook = Callable[["FakeEngine", int], Awaitable[None]]


def make_ticket(number: int, *, blocked_by: list[int] | None = None) -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        blocked_by=blocked_by or [],
    )


class FakeEngine:
    def __init__(
        self,
        outcome: Outcomes | Mapping[int, Outcomes],
        progress: Callable[[str], None],
        state_dir: Path,
        catalog: Mapping[int, Ticket | BaseException] | None = None,
        hook: Hook | None = None,
    ) -> None:
        self.outcome = outcome
        self.progress = progress
        self.catalog = catalog or {}
        self.hook = hook
        self.closed = False
        self.tickets: list[int] = []
        self.green: list[int] = []
        self.store = Store(":memory:")
        self.deps = SimpleNamespace(
            state_dir=state_dir, github=SimpleNamespace(repo="o/r"), store=self.store
        )

    async def get_ticket(self, number: int) -> Ticket:
        found = self.catalog.get(number, make_ticket(number))
        if isinstance(found, BaseException):
            raise found
        return found

    async def _run(self, ticket_number: int) -> RunOutcome:
        self.tickets.append(ticket_number)
        self.progress("load: started")
        if self.hook is not None:
            await self.hook(self, ticket_number)
        self.progress("load: completed")
        outcome = self.outcome[ticket_number] if isinstance(self.outcome, Mapping) else self.outcome
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        return await self._run(ticket_number)

    async def run_to_green(self, ticket_number: int) -> RunOutcome:
        self.green.append(ticket_number)
        return await self._run(ticket_number)

    def close(self) -> None:
        self.closed = True


def install_engine(
    monkeypatch: pytest.MonkeyPatch,
    outcome: Outcomes | Mapping[int, Outcomes],
    *,
    catalog: Mapping[int, Ticket | BaseException] | None = None,
    hook: Hook | None = None,
) -> list[FakeEngine]:
    engines: list[FakeEngine] = []

    def factory(
        config: FugueConfig, repo_root: Path, progress: Callable[[str], None]
    ) -> FakeEngine:
        state_dir = wiring.resolve_state_dir(config, os.environ, Path.home())
        engine = FakeEngine(outcome, progress, state_dir, catalog, hook)
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


def batch_command(tmp_path: Path, *numbers: str, extra: tuple[str, ...] = ()) -> Result:
    return runner.invoke(cli_app.app, ["run", *numbers, "--repo-root", str(tmp_path), *extra])


def waiting(number: int) -> RunOutcome:
    return RunOutcome(number * 10, RunState.WAITING_FOR_CI, pr_number=number + 100)


def test_single_ticket_run_prints_no_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_engine(monkeypatch, waiting(36))
    result = run_command(tmp_path)
    assert result.exit_code == 0
    assert "[#" not in result.output
    assert "ticket #" not in result.output
    assert "load: started" in result.output


def test_batch_labels_progress_and_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engines = install_engine(monkeypatch, {n: waiting(n) for n in (1, 2, 3)})
    result = batch_command(tmp_path, "1", "2", "3")
    assert result.exit_code == 0
    lines = result.output.splitlines()
    for number in (1, 2, 3):
        assert f"[#{number}] load: started" in lines
        assert f"[#{number}] load: completed" in lines
    summary = result.output[result.output.index("ticket  state") :]
    assert [line.split()[:4] for line in summary.splitlines()[1:4]] == [
        ["#1", "waiting_for_ci", "10", "pull"],
        ["#2", "waiting_for_ci", "20", "pull"],
        ["#3", "waiting_for_ci", "30", "pull"],
    ]
    assert "pull request #101" in summary
    assert "#1 next: fugue review 10" in summary
    assert all(engine.closed for engine in engines)
    assert sorted(t for engine in engines for t in engine.tickets) == [1, 2, 3]


def test_batch_respects_max_concurrent_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fugue.toml").write_text("[limits]\nmax_concurrent_runs = 2\n")
    active = 0
    maximum = 0

    async def hook(engine: FakeEngine, number: int) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.1)
        active -= 1

    install_engine(monkeypatch, {n: waiting(n) for n in (1, 2, 3)}, hook=hook)
    result = batch_command(tmp_path, "1", "2", "3")
    assert result.exit_code == 0
    assert maximum == 2


def test_batch_to_green_uses_run_to_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engines = install_engine(monkeypatch, {n: waiting(n) for n in (1, 2)})
    result = batch_command(tmp_path, "1", "2", extra=("--to-green",))
    assert result.exit_code == 0
    assert sorted(t for engine in engines for t in engine.green) == [1, 2]


def test_batch_skips_ticket_with_open_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = {1: make_ticket(1, blocked_by=[7, 8])}
    engines = install_engine(monkeypatch, {2: waiting(2)}, catalog=catalog)
    result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 3
    assert "skipped: blocked by #7, #8" in result.output
    assert sorted(t for engine in engines for t in engine.tickets) == [2]
    assert "pull request #102" in result.output


def test_batch_without_blockers_does_not_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_engine(monkeypatch, {1: waiting(1), 2: waiting(2)}, catalog={1: make_ticket(1)})
    result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 0
    assert "skipped" not in result.output


def test_batch_error_does_not_stop_other_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = RunOutcome(30, RunState.BLOCKED, problems=("x",))
    install_engine(monkeypatch, {1: RuntimeError("boom"), 2: waiting(2), 3: blocked})
    result = batch_command(tmp_path, "1", "2", "3")
    assert result.exit_code == 4
    assert "error: boom" in result.output
    summary = result.output[result.output.index("ticket  state") :]
    assert "#1" in summary and "failed" in summary and "error: boom" in summary
    assert "#2" in summary and "pull request #102" in summary
    assert "#3" in summary and "blocked" in summary
    assert "Traceback" not in result.output


def test_batch_ticket_fetch_failure_marks_only_that_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engines = install_engine(
        monkeypatch, {2: waiting(2)}, catalog={1: RuntimeError("no such ticket")}
    )
    result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 4
    assert "error: no such ticket" in result.output
    assert sorted(t for engine in engines for t in engine.tickets) == [2]


@pytest.mark.parametrize(
    ("states", "code"),
    [
        ([RunState.WAITING_FOR_CI, RunState.WAITING_FOR_CI], 0),
        ([RunState.COMPLETED, RunState.BLOCKED], 3),
        ([RunState.BLOCKED, RunState.PROVIDER_LIMITED], 5),
        ([RunState.PROVIDER_LIMITED, RunState.FAILED], 4),
        ([RunState.FAILED, RunState.INTERRUPTED], 6),
        ([RunState.BLOCKED, RunState.INTERRUPTED, RunState.FAILED], 6),
    ],
)
def test_batch_exit_code_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, states: list[RunState], code: int
) -> None:
    outcomes = {n: RunOutcome(n * 10, state) for n, state in enumerate(states, start=1)}
    install_engine(monkeypatch, outcomes)
    result = batch_command(tmp_path, *(str(n) for n in outcomes))
    assert result.exit_code == code


def test_batch_provider_limited_reports_resume_and_others_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_engine(monkeypatch, {1: RunOutcome(10, RunState.PROVIDER_LIMITED), 2: waiting(2)})
    result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 5
    assert "next: fugue resume 10" in result.output
    assert "#1 next: fugue resume 10" in result.output
    assert "pull request #102" in result.output


def test_batch_lock_conflict_is_reported_and_exits_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    engines = install_engine(monkeypatch, {1: waiting(1), 2: waiting(2)})
    with TicketLock(isolated_state, "o/r", 2):
        result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 3
    assert f"ticket #2 is already being run by process {os.getpid()}" in result.output
    assert sorted(t for engine in engines for t in engine.tickets) == [1]


@pytest.mark.parametrize("numbers", [["5", "5"], ["5", "6", "5"], ["5", "0"]])
def test_run_rejects_duplicate_or_nonpositive_before_any_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_state: Path,
    numbers: list[str],
) -> None:
    engines = install_engine(monkeypatch, waiting(5))
    result = batch_command(tmp_path, *numbers)
    assert result.exit_code == 2
    assert engines == []
    assert not isolated_state.exists()


def test_single_run_lock_conflict_exits_three_with_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    engines = install_engine(monkeypatch, waiting(36))
    with TicketLock(isolated_state, "o/r", 36):
        result = run_command(tmp_path)
    assert result.exit_code == 3
    assert f"error: ticket #36 is already being run by process {os.getpid()} (" in result.output
    assert "lock file" in result.output
    assert engines[0].tickets == []
    assert engines[0].closed


def test_single_run_prints_recovered_lock_note_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    install_engine(monkeypatch, waiting(36))
    lock = TicketLock(isolated_state, "o/r", 36)
    lock.path.parent.mkdir(parents=True)
    stale = datetime.now(UTC) - timedelta(seconds=75)
    lock.path.write_text(json.dumps({"pid": 2**22 + 99, "created_at": stale.isoformat()}))
    first = run_command(tmp_path)
    assert first.exit_code == 0
    assert f"note: recovered stale lock left by process {2**22 + 99} (1m 1" in first.output
    assert "; resuming" in first.output
    assert lock.path.read_text() == ""
    second = run_command(tmp_path)
    assert "recovered" not in second.output


def test_batch_interrupt_cancels_runs_and_prints_resume_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    started = 0

    async def hook(engine: FakeEngine, number: int) -> None:
        nonlocal started
        run = engine.store.create_run(number, "o/r")
        engine.store.set_run_state(run.id, RunState.RUNNING)
        started += 1
        try:
            if started == 2:
                raise asyncio.CancelledError
            await asyncio.sleep(30)
        finally:
            engine.store.set_run_state(run.id, RunState.INTERRUPTED)

    engines = install_engine(monkeypatch, {1: waiting(1), 2: waiting(2)}, hook=hook)
    (tmp_path / "fugue.toml").write_text("[limits]\nmax_concurrent_runs = 2\n")
    result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 6
    assert "ticket #1: fugue resume 1" in result.output
    assert "ticket #2: fugue resume 1" in result.output
    assert all(engine.closed for engine in engines)
    # Locks were released: both tickets can be locked again.
    with TicketLock(isolated_state, "o/r", 1), TicketLock(isolated_state, "o/r", 2):
        pass


def test_database_lock_error_names_wait_and_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fugue.toml").write_text("[limits]\ndatabase_wait_seconds = 7\n")
    install_engine(monkeypatch, sqlite3.OperationalError("database is locked"))
    result = run_command(tmp_path)
    assert result.exit_code == 4
    assert "run database is locked by another fugue process; waited 7 s" in result.output
    assert "limits.database_wait_seconds" in result.output
    assert "Traceback" not in result.output


def test_batch_database_lock_error_marks_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_engine(monkeypatch, {1: sqlite3.OperationalError("database is locked"), 2: waiting(2)})
    result = batch_command(tmp_path, "1", "2")
    assert result.exit_code == 4
    assert "waited 30 s (limits.database_wait_seconds)" in result.output
    assert "pull request #102" in result.output


def test_explain_leaves_other_errors_unchanged() -> None:
    assert cli_app._explain(RuntimeError("plain"), 30) == "plain"
    assert cli_app._explain(sqlite3.OperationalError("no such table"), 30) == "no such table"


def test_config_show_lists_database_wait_seconds(tmp_path: Path) -> None:
    result = runner.invoke(cli_app.app, ["config", "show", "--repo-root", str(tmp_path)])
    assert "database_wait_seconds = 30" in result.output


def test_render_skip_lists_blockers() -> None:
    assert render.render_skip(3, [1, 2]) == "skipped: blocked by #1, #2"


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


def test_status_lists_repository_and_filters_by_repo(isolated_state: Path, tmp_path: Path) -> None:
    seed_store(isolated_state)
    with Store(isolated_state / "fugue.db") as store:
        store.create_run(5, "other/repo")
    base = ["--repo-root", str(tmp_path)]
    everything = runner.invoke(cli_app.app, ["status", *base])
    assert everything.output.splitlines()[0].split()[:3] == ["id", "repo", "ticket"]
    assert "owner/repo" in everything.output and "other/repo" in everything.output
    only = runner.invoke(cli_app.app, ["status", "--repo", "other/repo", *base])
    assert "other/repo" in only.output and "owner/repo" not in only.output
    unknown = runner.invoke(cli_app.app, ["status", "--repo", "no/such", *base])
    assert unknown.exit_code == 0
    assert unknown.output.strip() == "No runs."


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


def test_build_engine_passes_resolve_token_env(tmp_path: Path) -> None:
    config = FugueConfig.model_validate(
        {
            "github": {
                "repo": "owner/repo",
                "project_owner": "owner",
                "project_number": 2,
                "resolve_token_env": "RESOLVE_TOKEN",
            }
        }
    )
    engine = wiring.build_engine(config, tmp_path, env={}, home=tmp_path)
    try:
        client = engine.deps.github
        assert isinstance(client, GitHubClient)
        assert client.resolve_token_env == "RESOLVE_TOKEN"
    finally:
        engine.deps.store.close()


def test_render_roles_without_api_key_has_no_warning() -> None:
    config = FugueConfig(
        roles={Role.ANALYST: RoleConfig(provider="claude")},
    )
    assert "warning" not in render.render_roles(config)
    assert AuthenticationMode.SUBSCRIPTION_LOGIN.value in render.render_roles(config)


def test_render_config_has_no_secret_fields() -> None:
    text = render.render_config(FugueConfig())
    assert "token" not in text.replace("tokens", "").lower()
