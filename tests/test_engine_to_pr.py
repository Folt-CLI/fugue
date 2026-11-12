"""Engine behaviour against local Git repositories and scripted providers."""

import asyncio
import sys
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentRequest,
    AgentResult,
    ScriptedAdapter,
    ScriptStep,
    UsageRecord,
)
from fugue.artifacts import ArtifactStore
from fugue.config import FugueConfig
from fugue.engine import Engine, EngineDeps, GitHubPort, RunOutcome
from fugue.engine.facts import branch_name, worktree_path
from fugue.github import PullRequest
from fugue.gitops import Git
from fugue.models import AuthenticationMode, Component, IssueType, Priority, Size, Status, Ticket
from fugue.process import run_command
from fugue.store import Store
from fugue.workflow import RunState

SCOPE = "You may touch only these files:\n- `allowed.txt`\n\nImplement the change."


class FakeGitHub:
    def __init__(self, ticket: Ticket) -> None:
        self.ticket = ticket
        self.scope = SCOPE
        self.calls: list[str] = []
        self.pr: PullRequest | None = None
        self.created: list[tuple[str, ...]] = []
        self.crash = False

    async def get_ticket(self, number: int) -> Ticket:
        assert number == self.ticket.number
        return self.ticket

    async def set_status(self, number: int, status: Status) -> None:
        self.calls.append("status")
        self.ticket = self.ticket.model_copy(update={"status": status})

    async def comment(self, number: int, body: str) -> None:
        self.calls.append("comment")

    async def download_attachment(self, url: str, dest: Path) -> Path:
        self.calls.append("download")
        dest.write_text(self.scope)
        return dest

    async def find_pr(self, branch: str) -> PullRequest | None:
        self.calls.append("find_pr")
        if self.crash:
            self.crash = False
            self.pr = PullRequest(71, "https://example.test/pr/71")
            raise RuntimeError("crash after push")
        return self.pr

    async def create_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        assignee: str,
        milestone: str,
        project_title: str,
    ) -> PullRequest:
        assert self.pr is None
        self.calls.append("create_pr")
        self.created.append((branch, base, title, body, assignee, milestone, project_title))
        self.pr = PullRequest(71, "https://example.test/pr/71")
        return self.pr


def result(*, ok: bool = True, error: str | None = None) -> AgentResult:
    return AgentResult(
        ok=ok,
        final_text="Implemented",
        structured_output=None,
        session_id="session-1",
        usage=UsageRecord(10, None, 4, None, None, 20),
        provider="codex",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=error,
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=None,
    )


def write_allowed(request: AgentRequest) -> None:
    (request.cwd / "allowed.txt").write_text("done")


async def git_command(path: Path, *args: str) -> str:
    output = await run_command(["git", *args], cwd=path)
    assert output.returncode == 0, output.stderr
    return output.stdout.strip()


@dataclass
class Harness:
    deps: EngineDeps
    github: FakeGitHub
    adapter: ScriptedAdapter
    origin: Path
    initial_head: str

    def run(self) -> RunOutcome:
        return asyncio.run(Engine(self.deps).run_to_pr(33))


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    async def setup() -> tuple[Path, Path, str]:
        origin = tmp_path / "origin.git"
        clone = tmp_path / "clone"
        await git_command(tmp_path, "init", "--bare", "--initial-branch=develop", str(origin))
        await git_command(tmp_path, "clone", str(origin), str(clone))
        await git_command(clone, "config", "user.name", "Test Developer")
        await git_command(clone, "config", "user.email", "test@example.test")
        (clone / "README").write_text("Initial")
        await git_command(clone, "add", "README")
        await git_command(clone, "commit", "-m", "Initial")
        await git_command(clone, "push", "origin", "develop")
        return origin, clone, await Git(clone).head_sha()

    origin, clone, head = asyncio.run(setup())
    ticket = Ticket(
        number=33,
        title="Add the engine",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        milestone="0.1.0",
        assignees=["developer"],
        components=[Component.SOURCE_CODE],
        body_sections={
            "Summary": "Execute the selected scope",
            "Scope": "Engine",
            "Solution": "[33-solution-1.md](https://github.com/user-attachments/files/scope)",
        },
    )
    github = FakeGitHub(ticket)
    port: GitHubPort = github
    adapter = ScriptedAdapter(
        [ScriptStep(result=result(), side_effect=write_allowed)], name="codex"
    )
    registry = AdapterRegistry()
    registry.register("codex", lambda config: adapter)
    config = FugueConfig()
    config.policy.gate_commands = [[sys.executable, "-c", "print('gate passed')"]]
    with Store(":memory:") as store:
        deps = EngineDeps(
            config,
            store,
            ArtifactStore(tmp_path / "artifacts"),
            port,
            registry,
            clone,
            tmp_path / "worktrees",
            tmp_path / "state",
        )
        yield Harness(deps, github, adapter, origin, head)


def test_happy_path(harness: Harness) -> None:
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.pr_number == 71
    assert outcome.head_sha != harness.initial_head
    branch = branch_name(harness.github.ticket)
    assert asyncio.run(Git(harness.origin).head_sha(branch)) == outcome.head_sha
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    subject = asyncio.run(git_command(path, "log", "-1", "--format=%s"))
    assert subject == "Feature[#33]: Add the engine"
    body = asyncio.run(git_command(path, "log", "-1", "--format=%b"))
    assert "**Summary**\nExecute the selected scope" in body
    assert harness.github.calls.count("status") == 1
    assert harness.github.ticket.status == Status.IN_IMPLEMENTATION
    assert harness.deps.store.usage_summary(outcome.run_id)["input_tokens"] == 10
    assert any(
        a.name.startswith("session-") for a in harness.deps.store.list_artifacts(outcome.run_id)
    )
    assert harness.adapter.requests[0].access == AccessMode.WORKSPACE_WRITE
    assert harness.github.created[0][4:] == ("developer", "0.1.0", "Fugue")
    assert "Closes #33" in harness.github.created[0][3]


@pytest.mark.parametrize("problem", ["missing", "unselected", "xl", "empty-allowlist"])
def test_scope_problems_block(harness: Harness, problem: str) -> None:
    ticket = harness.github.ticket
    if problem == "missing":
        ticket = ticket.model_copy(update={"body_sections": {"Summary": "Why", "Scope": "Engine"}})
    elif problem == "unselected":
        ticket = ticket.model_copy(
            update={"size": Size.M, "status": Status.READY_FOR_IMPLEMENTATION}
        )
    elif problem == "xl":
        ticket = ticket.model_copy(update={"size": Size.XL})
    else:
        harness.github.scope = "No allowed paths"
    harness.github.ticket = ticket
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems
    assert not harness.adapter.requests
    assert not harness.deps.worktrees_root.exists()
    assert set(harness.github.calls) <= {"download"}
    assert asyncio.run(Git(harness.deps.repo_root).head_sha()) == harness.initial_head


def test_gate_repair_resumes_session(harness: Harness) -> None:
    harness.deps.config.policy.gate_commands = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('allowed.txt').read_text() == 'done'",
        ]
    ]

    def broken(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text("broken")

    adapter = ScriptedAdapter(
        [
            ScriptStep(result=result(), side_effect=broken),
            ScriptStep(result=result(), side_effect=write_allowed),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert len(adapter.requests) == 2
    assert adapter.requests[1].session_id == "session-1"
    assert "Gate failed:" in adapter.requests[1].prompt
    assert harness.deps.store.usage_summary(outcome.run_id)["input_tokens"] == 20


def test_repair_limit(harness: Harness) -> None:
    harness.deps.config.limits.max_repair_attempts = 1
    harness.deps.config.policy.gate_commands = [
        [
            sys.executable,
            "-c",
            "import sys; print('failure evidence'); sys.exit(1)",
        ]
    ]
    adapter = ScriptedAdapter(
        [
            ScriptStep(result=result(), side_effect=write_allowed),
            ScriptStep(result=result()),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert len(adapter.requests) == 2
    artifacts = harness.deps.store.list_artifacts(outcome.run_id)
    failures = [a for a in artifacts if a.name.startswith("gate-failure-")]
    assert len(failures) == 2
    assert "failure evidence" in Path(failures[-1].path).read_text()
    assert "Return code: 1" in outcome.problems[0]
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert asyncio.run(Git(path).head_sha()) == harness.initial_head
    assert not harness.github.created


def test_staging_violation(harness: Harness) -> None:
    def write_disallowed(request: AgentRequest) -> None:
        write_allowed(request)
        (request.cwd / "disallowed.txt").write_text("outside scope")

    adapter = ScriptedAdapter(
        [ScriptStep(result=result(), side_effect=write_disallowed)], name="codex"
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "disallowed.txt" in outcome.problems[0]
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert asyncio.run(Git(path).head_sha()) == harness.initial_head
    assert not harness.github.created


def test_restart_after_push_reconciles(harness: Harness) -> None:
    harness.github.crash = True
    with pytest.raises(RuntimeError, match="crash after push"):
        harness.run()
    run = harness.deps.store.list_runs(33)[0]
    branch = branch_name(harness.github.ticket)
    push = harness.deps.store.begin_operation(run.id, "push", branch, {})
    assert push.status == "done"
    head = asyncio.run(Git(harness.origin).head_sha(branch))
    outcome = harness.run()
    assert outcome.run_id == run.id
    assert outcome.head_sha == head
    assert outcome.pr_number == 71
    assert not harness.github.created
    assert len(harness.adapter.requests) == 1
    assert harness.github.calls.count("status") == 1
    push = harness.deps.store.begin_operation(run.id, "push", branch, {})
    assert push.status == "reconciled"


def test_cancellation(harness: Harness) -> None:
    def cancel(request: AgentRequest) -> None:
        raise asyncio.CancelledError

    adapter = ScriptedAdapter([ScriptStep(side_effect=cancel)], name="codex")
    harness.deps.registry.register("codex", lambda config: adapter)
    with pytest.raises(asyncio.CancelledError):
        harness.run()
    assert harness.deps.store.list_runs(33)[0].state == RunState.INTERRUPTED
    assert not harness.github.created


@pytest.mark.parametrize(
    ("error", "state"),
    [
        ("Provider rate limited (429)", RunState.PROVIDER_LIMITED),
        ("Provider crashed", RunState.FAILED),
    ],
)
def test_provider_failure(harness: Harness, error: str, state: RunState) -> None:
    adapter = ScriptedAdapter(
        [ScriptStep(result=replace(result(), ok=False, error=error))], name="codex"
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    outcome = harness.run()
    assert outcome.state == state
    assert outcome.problems == (error,)
    errors = [
        a
        for a in harness.deps.store.list_artifacts(outcome.run_id)
        if a.name.startswith("agent-error-")
    ]
    assert Path(errors[0].path).read_text() == error
    assert not harness.github.created
