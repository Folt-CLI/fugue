"""Native stack scenarios using fake stack commands and local Git repositories."""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from test_engine_to_pr import Harness, git_command
from test_engine_to_pr import harness as harness

from fugue.engine import Engine, RunOutcome
from fugue.engine.facts import branch_name, worktree_path
from fugue.github import GitHubError, PullRequest
from fugue.github.stack import StackClient, StackProbe, StackReason
from fugue.gitops import Git
from fugue.models import Ticket
from fugue.process import CommandResult
from fugue.workflow import RunState


def command_result(argv: Sequence[str], stdout: str = "", code: int = 0) -> CommandResult:
    return CommandResult(
        tuple(argv),
        code,
        False,
        False,
        stdout,
        "failure" if code else "",
        False,
        False,
        False,
        None,
        0,
    )


@pytest.mark.parametrize(
    "gh,git,extension,code,reason",
    [
        ("2.90.0", "2.36.0", True, 0, None),
        ("2.91.1", "2.36", True, 0, None),
        ("2.89.9", "2.36.0", True, 0, "gh_too_old"),
        ("2.90.0", "2.35.9", True, 0, "git_too_old"),
        ("2.90.0", "2.36.0", False, 0, "extension_missing"),
        ("2.90.0", "2.36.0", True, 9, "feature_disabled"),
    ],
)
def test_probe_outcomes(
    tmp_path: Path, gh: str, git: str, extension: bool, code: int, reason: StackReason | None
) -> None:
    calls = []

    async def runner(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        calls.append(tuple(argv))
        assert kwargs == {"cwd": tmp_path, "timeout": 60}
        responses = {
            ("gh", "--version"): (f"gh version {gh}", 0),
            ("git", "--version"): (f"git version {git}", 0),
            ("gh", "extension", "list"): ("gh stack github/gh-stack" if extension else "", 0),
            ("gh", "stack", "view"): ("", code),
            ("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"): (
                "owner/repo\n",
                0,
            ),
            ("gh", "api", "repos/owner/repo/stacks?per_page=1", "--include"): (
                "HTTP/2.0 200 OK\r\n\r\n[]",
                0,
            ),
        }
        output, status = responses[tuple(argv)]
        return command_result(argv, output, status)

    assert asyncio.run(StackClient(tmp_path, runner=runner).probe()) == StackProbe(
        reason is None, reason
    )
    assert all(call[:2] != ("gh", "stack") or call[2] == "view" for call in calls)


@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("init", ("develop", ["lower", "upper"]), ("init", "--base", "develop", "lower", "upper")),
        ("add", ("upper",), ("add", "upper")),
        ("submit", (), ("submit",)),
        ("view", (), ("view",)),
        ("rebase", (), ("rebase",)),
        ("push", (), ("push",)),
    ],
)
def test_wrappers_use_bounded_fake_runner(
    tmp_path: Path, method: str, args: tuple[Any, ...], expected: tuple[str, ...]
) -> None:
    async def runner(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        assert tuple(argv) == ("gh", "stack", *expected)
        assert kwargs == {"cwd": tmp_path, "timeout": 60}
        return command_result(argv, "ok")

    assert asyncio.run(getattr(StackClient(tmp_path, runner=runner), method)(*args)) == "ok"


def test_probe_unknown_failure_stops(tmp_path: Path) -> None:
    async def runner(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        return command_result(argv, "2.90.0 github/gh-stack", 1 if argv[-1] == "view" else 0)

    with pytest.raises(GitHubError):
        asyncio.run(StackClient(tmp_path, runner=runner).probe())


class FakeStack(StackClient):
    def __init__(self, harness: Harness) -> None:
        super().__init__(harness.deps.repo_root)
        self.harness = harness
        self.available = StackProbe(True)
        self.branches: list[str] = []
        self.submits = 0
        self.metadata: list[tuple[str, ...]] = []
        self.interrupt = False
        self.initializations = 0
        self.view_codes: list[int] = []
        self.repository_status = 200

    async def _result(self, *argv: str) -> CommandResult:
        if argv == ("gh", "stack", "view"):
            code = 0 if self.branches else 2
            self.view_codes.append(code)
            return replace(
                command_result(argv, " ".join(self.branches), code),
                stderr="current branch is not part of a stack" if code == 2 else "",
            )
        responses = {
            ("gh", "--version"): "gh version 2.90.0",
            ("git", "--version"): "git version 2.36.0",
            ("gh", "extension", "list"): "gh stack github/gh-stack",
            (
                "gh",
                "repo",
                "view",
                "--json",
                "nameWithOwner",
                "--jq",
                ".nameWithOwner",
            ): self.harness.github.repo,
            (
                "gh",
                "api",
                f"repos/{self.harness.github.repo}/stacks?per_page=1",
                "--include",
            ): f"HTTP/2.0 {self.repository_status}\r\n\r\n[]",
        }
        return command_result(
            argv,
            responses[tuple(argv)],
            int(self.repository_status != 200) if argv[1] == "api" else 0,
        )

    async def probe(self, repo: str | None = None) -> StackProbe:
        if not self.available.available:
            return self.available
        return await super().probe() if repo is None else await super().probe(repo)

    async def init(self, base: str, branches: Sequence[str]) -> str:
        assert base == "develop"
        self.initializations += 1
        self.branches = list(branches)
        return ""

    async def submit(self) -> str:
        if self.interrupt:
            self.interrupt = False
            raise RuntimeError("interrupted before submit")
        self.submits += 1
        ticket = self.harness.github.ticket
        path = worktree_path(self.harness.deps.worktrees_root, ticket)
        await git_command(path, "push", "origin", branch_name(ticket))
        self.harness.github.pr = PullRequest(71, "https://example.test/pr/71")
        return ""

    async def edit_pr(
        self, repo: str, branch: str, title: str, body: str, assignee: str, milestone: str
    ) -> None:
        self.metadata.append((repo, branch, title, body, assignee, milestone))


@pytest.fixture
def stacked(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> tuple[Harness, FakeStack, str]:
    lower = harness.github.ticket.model_copy(update={"number": 32, "title": "Foundation"})
    lower_branch = branch_name(lower)

    async def setup() -> None:
        root = harness.deps.repo_root
        await git_command(root, "switch", "-c", lower_branch)
        (root / "foundation.txt").write_text("lower layer")
        await git_command(root, "add", "foundation.txt")
        await git_command(root, "commit", "-m", "Feature[#32]: foundation")
        await git_command(root, "push", "origin", lower_branch)
        await git_command(root, "switch", "develop")

    asyncio.run(setup())
    harness.github.ticket = harness.github.ticket.model_copy(update={"blocked_by": [32]})

    async def get_ticket(number: int) -> Ticket:
        return lower if number == 32 else harness.github.ticket

    async def find_pr(branch: str) -> PullRequest | None:
        if branch == lower_branch:
            return PullRequest(70, "https://example.test/pr/70")
        return harness.github.pr

    monkeypatch.setattr(harness.github, "get_ticket", get_ticket)
    monkeypatch.setattr(harness.github, "find_pr", find_pr)
    fake = FakeStack(harness)
    harness.deps.stack_factory = lambda path: fake
    return harness, fake, lower_branch


def test_scenario_1_lower_open_pr_selects_base_and_native_submit(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, lower = stacked
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert (path / "foundation.txt").read_text() == "lower layer"
    assert fake.branches == [lower, branch_name(harness.github.ticket)]
    assert fake.submits == 1
    assert fake.initializations == 1
    assert 2 in fake.view_codes
    assert "Stack position: above https://example.test/pr/70" in fake.metadata[0][3]
    assert harness.github.created == []
    assert asyncio.run(git_command(path, "rev-list", "--count", f"{lower}..HEAD")) == "1"


def assert_unavailable_blocks_before_worktree(
    stacked: tuple[Harness, FakeStack, str], reason: StackReason
) -> None:
    harness, fake, _ = stacked
    fake.available = StackProbe(False, reason)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert reason in outcome.problems[0]
    assert not worktree_path(harness.deps.worktrees_root, harness.github.ticket).exists()
    assert fake.submits == 0
    assert harness.github.pr is None
    assert harness.github.created == []


def test_scenario_2_extension_missing_blocks_before_worktree(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    assert_unavailable_blocks_before_worktree(stacked, "extension_missing")


def test_scenario_3_feature_disabled_blocks_before_worktree(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    assert_unavailable_blocks_before_worktree(stacked, "feature_disabled")


@pytest.mark.parametrize("reason", ["gh_too_old", "git_too_old"])
def test_scenario_4_old_versions_block_before_worktree(
    stacked: tuple[Harness, FakeStack, str], reason: StackReason
) -> None:
    assert_unavailable_blocks_before_worktree(stacked, reason)


def test_scenario_5_restart_reuses_worktree_branch_and_submits_once(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, _ = stacked
    fake.interrupt = True
    with pytest.raises(RuntimeError, match="interrupted before submit"):
        harness.run()
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    head = asyncio.run(Git(path).head_sha())
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert asyncio.run(Git(path).head_sha()) == head
    assert fake.submits == 1
    operations = harness.deps.store._connection.execute(
        "SELECT * FROM operations WHERE kind = 'stack_submit'"
    ).fetchall()
    assert len(operations) == 1
    assert len(fake.metadata) == 1
    assert fake.initializations == 1


def test_scenario_6_divergent_update_stops_without_push(
    stacked: tuple[Harness, FakeStack, str], tmp_path: Path
) -> None:
    harness, fake, _ = stacked
    assert harness.run().state == RunState.WAITING_FOR_CI
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    branch = branch_name(harness.github.ticket)

    async def diverge() -> str:
        other = tmp_path / "other"
        await git_command(tmp_path, "clone", str(harness.origin), str(other))
        await git_command(other, "config", "user.name", "Test Developer")
        await git_command(other, "config", "user.email", "test@example.test")
        await git_command(other, "switch", branch)
        (other / "remote.txt").write_text("remote work")
        await git_command(other, "add", "remote.txt")
        await git_command(other, "commit", "-m", "Feature[#33]: remote work")
        await git_command(other, "push", "origin", branch)
        (path / "allowed.txt").write_text("local work")
        await git_command(path, "add", "allowed.txt")
        await git_command(path, "commit", "-m", "Feature[#33]: local work")
        return await Git(other).head_sha()

    remote_head = asyncio.run(diverge())
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "diverged" in " ".join(outcome.problems)
    assert fake.submits == 1
    assert (
        asyncio.run(git_command(path, "ls-remote", "--heads", "origin", branch)).split()[0]
        == remote_head
    )


def test_scenario_7_independent_ticket_keeps_develop(harness: Harness) -> None:
    def forbidden(path: Path) -> StackClient:
        pytest.fail("Independent tickets must not probe stacks")

    harness.deps.stack_factory = forbidden
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert harness.github.created[0][1] == "develop"


def test_restart_after_submit_reconciles_existing_pr(
    stacked: tuple[Harness, FakeStack, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, fake, _ = stacked
    original = fake.submit

    async def submit_then_interrupt() -> str:
        await original()
        raise RuntimeError("interrupted after submit")

    with monkeypatch.context() as patch:
        patch.setattr(fake, "submit", submit_then_interrupt)
        with pytest.raises(RuntimeError, match="interrupted after submit"):
            harness.run()
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert fake.submits == 1
    assert len(fake.metadata) == 1


def test_stack_update_submits_new_head_and_reuses_pr(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, _ = stacked
    assert harness.run().state == RunState.WAITING_FOR_CI
    pr = harness.github.pr
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)

    async def update() -> None:
        (path / "allowed.txt").write_text("updated")
        await git_command(path, "add", "allowed.txt")
        await git_command(path, "commit", "-m", "Feature[#33]: update layer")

    asyncio.run(update())
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert harness.github.pr == pr
    assert fake.submits == 2
    assert len(fake.metadata) == 2
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert fake.submits == 2


def test_probe_is_required_again_after_restart(stacked: tuple[Harness, FakeStack, str]) -> None:
    harness, fake, _ = stacked
    fake.interrupt = True
    with pytest.raises(RuntimeError):
        harness.run()
    fake.available = StackProbe(False, "feature_disabled")
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "feature_disabled" in outcome.problems[0]
    assert fake.submits == 0


def test_stack_base_survives_changed_lower_ticket_title(
    stacked: tuple[Harness, FakeStack, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, fake, lower = stacked
    fake.interrupt = True
    with pytest.raises(RuntimeError):
        harness.run()
    original = harness.github.get_ticket

    async def renamed(number: int) -> Ticket:
        ticket = await original(number)
        return ticket.model_copy(update={"title": "Renamed foundation"}) if number == 32 else ticket

    monkeypatch.setattr(harness.github, "get_ticket", renamed)
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert fake.branches[0] == lower


def test_metadata_restart_does_not_resubmit(
    stacked: tuple[Harness, FakeStack, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, fake, _ = stacked

    async def fail(*args: str) -> None:
        raise RuntimeError("interrupted metadata")

    with monkeypatch.context() as patch:
        patch.setattr(fake, "edit_pr", fail)
        with pytest.raises(RuntimeError, match="interrupted metadata"):
            harness.run()
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert fake.submits == 1
    assert len(fake.metadata) == 1


@pytest.mark.parametrize("available", [True, False])
def test_batch_stack_admission_keeps_skip_reason(
    stacked: tuple[Harness, FakeStack, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    available: bool,
) -> None:
    from fugue.cli import app as cli_app

    harness, fake, _ = stacked
    fake.available = StackProbe(True) if available else StackProbe(False, "extension_missing")

    class BatchEngine:
        deps = harness.deps

        async def get_ticket(self, number: int) -> Ticket:
            return await harness.github.get_ticket(number)

        async def run_to_pr(self, number: int) -> RunOutcome:
            return await Engine(self.deps).run_to_pr(number)

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_app, "engine_factory", lambda *args: BatchEngine())
    monkeypatch.setattr(cli_app, "StackClient", lambda path: fake)
    result = cli_app._run_batch(
        harness.deps.config,
        harness.deps.repo_root,
        cast(cli_app.RunEngine, BatchEngine()),
        [33],
        False,
    )
    output = capsys.readouterr().out
    if available:
        assert result.exit_code == 0
        assert fake.submits == 1
    else:
        assert result.exit_code == 3
        assert "skipped: blocked by #32" in output
        assert "extension_missing" in output
        assert fake.submits == 0


def test_restart_reuses_branch_left_before_worktree_creation(
    stacked: tuple[Harness, FakeStack, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, fake, _ = stacked

    async def create_branch_then_interrupt(self: Git, path: Path, branch: str, base: str) -> None:
        await git_command(self.repo, "branch", branch, base)
        raise RuntimeError("interrupted before worktree creation")

    with monkeypatch.context() as patch:
        patch.setattr(Git, "create_worktree", create_branch_then_interrupt)
        with pytest.raises(RuntimeError, match="interrupted before worktree creation"):
            harness.run()
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert fake.submits == 1


def test_lower_remote_branch_is_fetched_without_local_branch(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, lower = stacked
    asyncio.run(git_command(harness.deps.repo_root, "branch", "-D", lower))
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert fake.branches[0] == lower


def test_lower_stack_divergence_blocks_before_worktree(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, lower = stacked

    async def change_lower() -> None:
        root = harness.deps.repo_root
        await git_command(root, "switch", lower)
        (root / "foundation.txt").write_text("unpublished lower work")
        await git_command(root, "add", "foundation.txt")
        await git_command(root, "commit", "-m", "Feature[#32]: unpublished lower work")
        await git_command(root, "switch", "develop")

    asyncio.run(change_lower())
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "lower local and remote stack branches differ" in outcome.problems[0]
    assert not worktree_path(harness.deps.worktrees_root, harness.github.ticket).exists()
    assert fake.submits == 0


def test_stacked_pr_update_rejects_committed_file_outside_scope(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, _ = stacked
    delivered = harness.run()
    assert delivered.state == RunState.WAITING_FOR_CI
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    branch = branch_name(harness.github.ticket)

    async def update() -> None:
        (path / "outside.txt").write_text("outside the selected scope")
        await git_command(path, "add", "outside.txt")
        await git_command(path, "commit", "-m", "Feature[#33]: outside scope")

    asyncio.run(update())
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("Outside allowed files: outside.txt",)
    assert fake.submits == 1
    assert len(fake.metadata) == 1
    remote = asyncio.run(git_command(path, "ls-remote", "--heads", "origin", branch))
    assert remote.split()[0] == delivered.head_sha


def test_stacked_review_prompt_and_inline_comments_use_recorded_base(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    from test_engine_review import FakeReviewGitHub, agent_result

    from fugue.agents import AccessMode, ScriptedAdapter, ScriptStep
    from fugue.models import Role

    harness, _, _ = stacked
    delivered = harness.run()
    assert delivered.state == RunState.WAITING_FOR_CI
    github = FakeReviewGitHub(harness.github.ticket)
    github.pr = harness.github.pr
    harness.deps.github = github
    role = harness.deps.config.roles[Role.REVIEWER]
    reviewer = ScriptedAdapter(
        [
            ScriptStep(
                result=agent_result(
                    "## Blocking\n"
                    "1. High: Dependent change needs repair. `allowed.txt:1`\n"
                    "2. Medium: Lower layer needs repair. `foundation.txt:1`\n"
                )
            )
        ],
        name=role.provider,
    )
    harness.deps.registry.register(role.provider, lambda config: reviewer)

    outcome = asyncio.run(Engine(harness.deps).run_review(delivered.run_id))
    assert outcome.state == RunState.WAITING_FOR_DECISION
    [request] = reviewer.requests
    assert request.access == AccessMode.READ_ONLY
    assert "diff --git a/allowed.txt b/allowed.txt" in request.prompt
    assert "foundation.txt" not in request.prompt
    [posted] = github.posted
    assert [comment.path for comment in posted.comments] == ["allowed.txt"]
    assert "foundation.txt:1" in posted.body


@pytest.mark.parametrize("status", [200, 404, 403, 500])
def test_fresh_clone_probe_checks_repository_support(tmp_path: Path, status: int) -> None:
    calls: list[tuple[str, ...]] = []

    async def runner(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        calls.append(tuple(argv))
        if tuple(argv) == ("gh", "stack", "view"):
            return replace(
                command_result(argv, code=2),
                stderr='current branch "develop" is not part of a stack',
            )
        responses = {
            ("gh", "--version"): "gh version 2.90.0",
            ("git", "--version"): "git version 2.36.0",
            ("gh", "extension", "list"): "gh stack github/gh-stack",
            (
                "gh",
                "repo",
                "view",
                "--json",
                "nameWithOwner",
                "--jq",
                ".nameWithOwner",
            ): "owner/repo\n",
            (
                "gh",
                "api",
                "repos/owner/repo/stacks?per_page=1",
                "--include",
            ): f"HTTP/2.0 {status}\r\n\r\n[]",
        }
        return command_result(
            argv, responses[tuple(argv)], int(status != 200) if argv[1] == "api" else 0
        )

    client = StackClient(tmp_path, runner=runner)
    if status in (403, 500):
        with pytest.raises(GitHubError):
            asyncio.run(client.probe())
    else:
        expected = StackProbe(True) if status == 200 else StackProbe(False, "feature_disabled")
        assert asyncio.run(client.probe()) == expected
    assert ("gh", "api", "repos/owner/repo/stacks?per_page=1", "--include") in calls


@pytest.mark.parametrize("code", [1, 2, 4, 8, 9])
def test_view_only_missing_local_tracking_allows_initialization(tmp_path: Path, code: int) -> None:
    async def runner(argv: Sequence[str], **kwargs: Any) -> CommandResult:
        return command_result(argv, code=code)

    client = StackClient(tmp_path, runner=runner)
    if code == 2:
        assert asyncio.run(client.view()) is None
    else:
        with pytest.raises(GitHubError):
            asyncio.run(client.view())


def test_fresh_clone_disabled_repository_blocks_before_worktree(
    stacked: tuple[Harness, FakeStack, str],
) -> None:
    harness, fake, _ = stacked
    fake.repository_status = 404
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("gh stack unavailable: feature_disabled",)
    assert not worktree_path(harness.deps.worktrees_root, harness.github.ticket).exists()
    assert fake.initializations == fake.submits == 0
    assert harness.github.pr is None
