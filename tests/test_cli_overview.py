"""The overview command end to end with fake GitHub data and a fake analyst."""

from pathlib import Path
from typing import Any

import pytest
from test_overview import SELECTED, FakePort, pr, ticket
from test_overview_summary import adapter, result
from typer.testing import CliRunner

from fugue.agents import AdapterConfigError, UnknownProvider, UsageRecord
from fugue.cli import app as cli_app
from fugue.cli import overview as overview_cli
from fugue.config import FugueConfig
from fugue.github.client import GitHubError
from fugue.models import IssueType, Size, Status
from fugue.overview import OverviewPort
from fugue.store import Store

CONFIG = """[github]
repo = "o/r"
project_owner = "o"
project_number = 1
"""


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    return tmp_path / "state-home" / "fugue"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "checkout"
    path.mkdir()
    (path / "fugue.toml").write_text(CONFIG)
    return path


def install(
    monkeypatch: pytest.MonkeyPatch,
    port: OverviewPort | None = None,
    analyst: object | None = None,
) -> dict[str, int]:
    calls = {"github": 0, "adapter": 0}

    def make_github(config: FugueConfig, repo: str) -> OverviewPort:
        calls["github"] += 1
        assert repo == "o/r"
        return port if port is not None else FakePort([])

    def make_adapter(config: FugueConfig) -> object:
        calls["adapter"] += 1
        if isinstance(analyst, Exception):
            raise analyst
        return analyst

    monkeypatch.setattr(overview_cli, "github_factory", make_github)
    monkeypatch.setattr(overview_cli, "adapter_factory", make_adapter)
    return calls


def invoke(root: Path, *args: str, columns: str = "200") -> Any:
    runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": columns})
    return runner.invoke(cli_app.app, ["overview", "--repo-root", str(root), *args])


def full_port() -> FakePort:
    return FakePort(
        [
            ticket(12345),
            ticket(2, blocked_by=[20, 21]),
            ticket(3, size=Size.M, status=Status.IN_ANALYSIS, analysis="Options"),
            ticket(
                4,
                size=Size.L,
                status=Status.IN_ANALYSIS,
                issue_type=IssueType.QUESTION,
                analysis="Options",
            ),
            ticket(
                5,
                size=Size.M,
                status=Status.READY_FOR_IMPLEMENTATION,
                solution=SELECTED.format(n=5),
            ),
        ],
        prs=[pr(7, "a" * 40)],
        progress={4: (1, 3)},
    )


def seed_runs(state_dir: Path) -> None:
    state_dir.mkdir(parents=True)
    with Store(state_dir / "fugue.db") as store:
        done = store.create_run(1, "o/r")
        store.set_run_state(done.id, "completed")
        waiting = store.create_run(9, "o/r")
        store.set_run_state(waiting.id, "waiting_for_decision")
        interrupted = store.create_run(10, "o/r")
        store.set_run_state(interrupted.id, "interrupted")
        other = store.create_run(11, "x/y")
        store.set_run_state(other.id, "running")


def test_report_lists_every_section_without_a_summary(
    root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    seed_runs(isolated_state)
    calls = install(monkeypatch, full_port())
    outcome = invoke(root, "--no-summary")
    text = outcome.output
    assert outcome.exit_code == 0
    assert calls == {"github": 1, "adapter": 0}
    assert "repository: o/r" in text
    assert "open tickets, S0: To Do (2)" in text and "open tickets, S1: In Analysis (2)" in text
    assert "open tickets, S3: Ready For Implementation (1)" in text
    assert "Epic and Question progress" in text and "1/3" in text
    assert "ready to start" in text and "fugue run 12345" in text and "fugue run 5" in text
    assert "blocked by #20, #21" in text
    assert "waiting for a decision" in text and "fugue question 3" in text
    assert "fugue question 4" in text
    assert "fugue decide 2 --accept INDEX --reject 'INDEX:reason text'" in text
    assert "fugue resume 3" in text
    assert "open pull requests" in text and "aaaaaaa" in text and "aaaaaaaa" not in text
    assert "recently merged pull requests" in text and "merged thirty" in text
    assert "recently closed tickets" in text and "closed thirty-one" in text
    assert "since last activity" in text
    assert "running" not in text  # the other repository's run is excluded
    assert "summary" not in text
    assert "\x1b" not in text


def test_overview_changes_nothing_and_creates_no_store(
    root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    install(monkeypatch, full_port())
    before = sorted(p.name for p in root.iterdir())
    assert invoke(root, "--no-summary").exit_code == 0
    assert not isolated_state.exists()
    assert sorted(p.name for p in root.iterdir()) == before


def test_existing_store_is_not_changed(
    root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    seed_runs(isolated_state)
    install(monkeypatch, full_port())
    with Store(isolated_state / "fugue.db") as store:
        before = [(r.id, r.state, r.updated_at) for r in store.list_runs()]
    invoke(root, "--no-summary")
    with Store(isolated_state / "fugue.db") as store:
        assert [(r.id, r.state, r.updated_at) for r in store.list_runs()] == before
    names = {p.name for p in isolated_state.iterdir()}
    assert names <= {"fugue.db", "fugue.db-shm", "fugue.db-wal"}


def test_approved_follows_the_stored_merge_approval_for_the_current_head(
    root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    isolated_state.mkdir(parents=True)
    with Store(isolated_state / "fugue.db") as store:
        run = store.create_run(5, "o/r")
        store.record_approval(run.id, "merge", "7", "a" * 40, "approved", "dev", "ok")
    port = FakePort([], prs=[pr(7, "a" * 40)])
    install(monkeypatch, port)
    assert " yes " in invoke(root, "--no-summary").output
    port.prs = [pr(7, "b" * 40)]
    output = invoke(root, "--no-summary").output
    assert " no " in output and " yes " not in output


def test_empty_repository_prints_every_section_as_none(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch)
    outcome = invoke(root, "--no-summary")
    assert outcome.exit_code == 0
    for title in (
        "open tickets by Status",
        "Epic and Question progress (closed/total)",
        "ready to start",
        "blocked tickets",
        "waiting for a decision",
        "open pull requests",
        "unfinished runs",
    ):
        assert f"{title}: none" in outcome.output


@pytest.mark.parametrize(
    "failure", [GitHubError(("gh", "api"), "x\ndial tcp: no network"), FileNotFoundError("gh")]
)
def test_unreachable_github_shows_local_runs_and_exits_zero(
    failure: Exception, root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    seed_runs(isolated_state)

    class Down(FakePort):
        async def list_open_issue_numbers(self, limit: int) -> tuple[list[int], bool]:
            raise failure

    calls = install(monkeypatch, Down([]), adapter(result("#1 x.")))
    outcome = invoke(root)
    text = outcome.output
    assert outcome.exit_code == 0
    assert "note: GitHub unreachable (" in text and "); showing local runs only" in text
    assert "fugue decide 2" in text and "fugue resume 3" in text
    assert calls["adapter"] == 0


def test_truncation_and_unreadable_ticket_notes_keep_the_rest(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root.joinpath("fugue.toml").write_text(CONFIG + "[limits]\noverview_max_tickets = 3\n")
    port = FakePort([ticket(1), ticket(2)], bad={3: "Issue 3 is absent from project 1"})
    port.truncated = True
    install(monkeypatch, port)
    text = invoke(root, "--no-summary").output
    assert "note: showing 3 of more than 3 open tickets" in text
    assert "note: could not read #3: Issue 3 is absent from project 1" in text
    assert "fugue run 1" in text and "fugue run 2" in text


def test_summary_is_shown_cached_and_the_only_file_written(
    root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    usage = UsageRecord(10, None, 5, None, None, 900)
    fake = adapter(
        result("#12345 is ready to start.", usage=usage), result("#12345 again.", usage=usage)
    )
    calls = install(monkeypatch, full_port(), fake)
    text = invoke(root).output
    assert calls["adapter"] == 1
    assert "summary" in text and "#12345 is ready to start." in text
    assert "usage (reported values, not a bill)" in text and "input 10" in text
    assert fake.requests[0].cwd != root
    assert "fugue.toml" not in fake.requests[0].prompt
    assert [p.name for p in isolated_state.iterdir()] == ["overview-o-r.json"]
    again = invoke(root).output
    assert "summary (cached)" in again and "#12345 is ready to start." in again
    assert len(fake.requests) == 1
    refreshed = invoke(root, "--refresh").output
    assert len(fake.requests) == 2 and "#12345 again." in refreshed


@pytest.mark.parametrize(
    "analyst",
    [UnknownProvider("Unknown provider: x"), AdapterConfigError("mode mismatch"), None],
)
def test_adapter_build_problem_skips_the_summary_only(
    analyst: Exception | None, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, full_port(), analyst)
    outcome = invoke(root)
    assert outcome.exit_code == 0
    assert "note: summary skipped (" in outcome.output
    assert "fugue run 12345" in outcome.output


def test_ungrounded_summary_is_dropped_with_a_reason(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, full_port(), adapter(result("#999 is on fire.")))
    text = invoke(root).output
    assert "note: summary skipped (summary cited items not in the gathered facts)" in text
    assert "#999 is on fire" not in text


def test_narrow_plain_output_keeps_ticket_and_run_columns_whole(
    root: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
) -> None:
    seed_runs(isolated_state)
    install(monkeypatch, full_port())
    text = invoke(root, "--no-summary", columns="60").output
    assert "\x1b" not in text
    lines = text.splitlines()
    assert any("#12345" in line for line in lines)
    assert any("run" in line.split() and "ticket" in line.split() for line in lines)


def test_missing_project_settings_fail_before_any_call(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root.joinpath("fugue.toml").write_text('[github]\nrepo = "o/r"\n')
    calls = install(monkeypatch, full_port(), adapter(result("#1 x.")))
    outcome = invoke(root)
    assert outcome.exit_code == 2
    assert "error:" in outcome.output
    assert calls == {"github": 0, "adapter": 0}


def test_outside_a_git_repository_fails_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install(monkeypatch, full_port(), adapter(result("#1 x.")))
    monkeypatch.chdir(tmp_path)
    outcome = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"}).invoke(cli_app.app, ["overview"])
    assert outcome.exit_code == 2
    assert "error:" in outcome.output
    assert calls == {"github": 0, "adapter": 0}


def test_no_origin_remote_fails_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "bare"
    checkout.mkdir()
    (checkout / "fugue.toml").write_text('[github]\nproject_owner = "o"\nproject_number = 1\n')
    calls = install(monkeypatch, full_port(), adapter(result("#1 x.")))
    outcome = invoke(checkout)
    assert outcome.exit_code == 2
    assert "error:" in outcome.output
    assert calls == {"github": 0, "adapter": 0}
