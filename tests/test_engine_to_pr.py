"""Engine behaviour against local Git repositories and scripted providers."""

import asyncio
import json
import sys
from collections.abc import Iterator, Sequence
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
from fugue.config import FugueConfig, RoleConfig
from fugue.engine import Engine, EngineDeps, GitHubPort, RunOutcome
from fugue.engine.facts import branch_name, worktree_path
from fugue.github import PullRequest
from fugue.gitops import Git, GitError
from fugue.models import (
    AuthenticationMode,
    Component,
    IssueType,
    Priority,
    Role,
    Size,
    Status,
    Ticket,
)
from fugue.process import run_command
from fugue.store import StageAttempt, Store
from fugue.workflow import RunState

SCOPE = "You may touch only these files:\n- `allowed.txt`\n\nImplement the change."


class FakeGitHub:
    def __init__(self, ticket: Ticket) -> None:
        self.ticket = ticket
        self.repo = "owner/repo"
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
    ("error", "flag", "state"),
    [
        ("Provider rate limited (429)", True, RunState.PROVIDER_LIMITED),
        ("Provider rate limited (429)", False, RunState.FAILED),
        ("Rate limiter configuration missing", False, RunState.FAILED),
        ("Usage limit configuration missing", False, RunState.FAILED),
        ("Claude provider-limited: rejected", False, RunState.FAILED),
        ("Provider crashed", False, RunState.FAILED),
    ],
)
def test_provider_failure(harness: Harness, error: str, flag: bool, state: RunState) -> None:
    adapter = ScriptedAdapter(
        [ScriptStep(result=replace(result(), ok=False, error=error, provider_limited=flag))],
        name="codex",
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


@pytest.mark.parametrize(("recorded", "disallowed"), [(True, True), (False, True), (True, False)])
def test_restart_rejects_amended_commit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, recorded: bool, disallowed: bool
) -> None:
    async def interrupt_push(self: Git, branch: str, remote: str = "origin") -> None:
        raise RuntimeError("stop before push")

    with monkeypatch.context() as patch:
        patch.setattr(Git, "push", interrupt_push)
        if not recorded:
            complete = harness.deps.store.complete_operation

            def interrupt_record(id: int, result: dict[str, object]) -> None:
                if "head_sha" in result:
                    raise RuntimeError("stop before push")
                complete(id, result)

            patch.setattr(harness.deps.store, "complete_operation", interrupt_record)
        with pytest.raises(RuntimeError, match="stop before push"):
            harness.run()

    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    name = "disallowed\tfile\n.txt" if disallowed else "allowed.txt"
    (path / name).write_text("Amended content")

    async def amend() -> str:
        await git_command(path, "add", "--", name)
        await git_command(path, "commit", "--amend", "--no-edit")
        return await git_command(path, "log", "-1", "--format=%s")

    assert asyncio.run(amend()) == "Feature[#33]: Add the engine"

    async def unexpected_push(self: Git, branch: str, remote: str = "origin") -> None:
        pytest.fail("A changed reconciled commit must not be pushed")

    monkeypatch.setattr(Git, "push", unexpected_push)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    if disallowed:
        assert f"Outside allowed files: {name}" in outcome.problems
    if recorded:
        assert "Commit head SHA differs from the recorded operation result" in outcome.problems
    assert not harness.github.created
    assert "find_pr" not in harness.github.calls
    assert asyncio.run(git_command(harness.origin, "branch", "--list", "feature/*")) == ""


def test_replaced_scope_attachment_is_downloaded(harness: Harness) -> None:
    harness.github.scope = "No allowed paths"
    first = harness.run()
    assert first.state == RunState.BLOCKED
    assert harness.github.calls.count("download") == 1
    harness.github.scope = SCOPE
    sections = dict(harness.github.ticket.body_sections)
    sections["Solution"] = (
        "[33-solution-1.md](https://github.com/user-attachments/files/replacement)"
    )
    harness.github.ticket = harness.github.ticket.model_copy(update={"body_sections": sections})
    second = harness.run()
    assert second.run_id == first.run_id
    assert second.state == RunState.WAITING_FOR_CI
    assert harness.github.calls.count("download") == 2
    assert harness.adapter.requests[0].prompt.endswith(SCOPE)
    scopes = [
        a for a in harness.deps.store.list_artifacts(first.run_id) if a.name.startswith("scope-")
    ]
    assert len(scopes) == 2
    assert scopes[0].sha256 != scopes[1].sha256
    assert scopes[0].name != scopes[1].name
    # An unchanged URL reuses the cached scope without another download.
    assert harness.run().pr_number == second.pr_number
    assert harness.github.calls.count("download") == 2


def test_restart_performs_pending_repair(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.deps.config.limits.max_repair_attempts = 1
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
    write_artifact = Engine._artifact

    def interrupt_after_failure(self: Engine, run_id: int, name: str, text: str) -> None:
        write_artifact(self, run_id, name, text)
        if name.startswith("gate-failure-"):
            raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(Engine, "_artifact", interrupt_after_failure)
        with pytest.raises(asyncio.CancelledError):
            harness.run()
    run = harness.deps.store.list_runs(33)[0]
    assert run.state == RunState.INTERRUPTED
    assert len(adapter.requests) == 1
    assert any(op.kind == "repair" for op in harness.deps.store.pending_operations(run.id))
    outcome = harness.run()
    assert outcome.run_id == run.id
    assert outcome.state == RunState.WAITING_FOR_CI
    assert len(adapter.requests) == 2
    assert adapter.requests[1].session_id == "session-1"
    assert "Gate failed:" in adapter.requests[1].prompt
    assert not harness.deps.store.pending_operations(run.id)
    failures = [
        a for a in harness.deps.store.list_artifacts(run.id) if a.name.startswith("gate-failure-")
    ]
    # Resuming checks the still-failing gate before using the pending repair.
    assert len(failures) == 2


def test_implementation_completion_tracks_current_scope(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ScriptedAdapter(
        [ScriptStep(result=result(), side_effect=write_allowed) for _ in range(3)], name="codex"
    )
    harness.deps.registry.register("codex", lambda config: adapter)

    async def interrupt_verification(self: Engine, run_id: int, path: Path) -> None:
        raise RuntimeError("stop after implementation")

    replacement = SCOPE + "\nApply the revised behaviour."
    with monkeypatch.context() as patch:
        patch.setattr(Engine, "_verify", interrupt_verification)
        for index, scope in enumerate((SCOPE, replacement, SCOPE)):
            harness.github.scope = scope
            sections = dict(harness.github.ticket.body_sections)
            sections["Solution"] = (
                f"[33-solution-1.md](https://github.com/user-attachments/files/scope-{index})"
            )
            harness.github.ticket = harness.github.ticket.model_copy(
                update={"body_sections": sections}
            )
            with pytest.raises(RuntimeError, match="stop after implementation"):
                harness.run()
            assert len(adapter.requests) == index + 1
            assert adapter.requests[-1].prompt.endswith(scope)
            assert adapter.requests[-1].session_id == (None if index == 0 else "session-1")
    # Reusing the current scope skips implementation; returning to an earlier
    # scope above required implementation because it was no longer the current one.
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert len(adapter.requests) == 3
    assert len(harness.deps.store.list_runs(33)) == 1


@pytest.mark.parametrize("interruption", ["after-implementation", "after-worktree", "legacy"])
def test_base_advance_does_not_change_preparation_base(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    branch = branch_name(harness.github.ticket)
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)

    async def interrupt_verification(self: Engine, run_id: int, path: Path) -> None:
        raise RuntimeError("stop after preparation")

    if interruption == "legacy":
        path.parent.mkdir(parents=True)
        asyncio.run(Git(harness.deps.repo_root).create_worktree(path, branch, "develop"))
    else:
        with monkeypatch.context() as patch:
            if interruption == "after-implementation":
                patch.setattr(Engine, "_verify", interrupt_verification)
            else:
                complete = harness.deps.store.complete_operation

                def interrupt_record(id: int, result: dict[str, object]) -> None:
                    if "base_sha" in result:
                        raise RuntimeError("stop after preparation")
                    complete(id, result)

                patch.setattr(harness.deps.store, "complete_operation", interrupt_record)
            with pytest.raises(RuntimeError, match="stop after preparation"):
                harness.run()

    async def advance_base() -> str:
        root = harness.deps.repo_root
        (root / "unrelated.txt").write_text("Another ticket's change")
        await git_command(root, "add", "unrelated.txt")
        await git_command(root, "commit", "-m", "Advance develop")
        await git_command(root, "push", "origin", "develop")
        return await Git(root).head_sha()

    advanced = asyncio.run(advance_base())
    assert advanced != harness.initial_head
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    preparation = harness.deps.store.begin_operation(outcome.run_id, "worktree", branch, {})
    assert preparation.result_json is not None
    assert json.loads(preparation.result_json)["base_sha"] == harness.initial_head
    assert not (path / "unrelated.txt").exists()
    assert len(harness.adapter.requests) == 1
    # Reconciliation must preserve the preparation base on later resumptions too.
    assert harness.run().head_sha == outcome.head_sha
    preparation = harness.deps.store.begin_operation(outcome.run_id, "worktree", branch, {})
    assert preparation.result_json is not None
    assert json.loads(preparation.result_json)["base_sha"] == harness.initial_head


@pytest.mark.parametrize("exhausted", [False, True])
def test_passing_gate_satisfies_pending_repair_without_attempt(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, exhausted: bool
) -> None:
    harness.deps.config.limits.max_repair_attempts = 1
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
            ScriptStep(result=result()),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    if exhausted:
        assert harness.run().state == RunState.BLOCKED
    else:
        write_artifact = Engine._artifact

        def interrupt_after_failure(self: Engine, run_id: int, name: str, text: str) -> None:
            write_artifact(self, run_id, name, text)
            if name.startswith("gate-failure-"):
                raise asyncio.CancelledError

        with monkeypatch.context() as patch:
            patch.setattr(Engine, "_artifact", interrupt_after_failure)
            with pytest.raises(asyncio.CancelledError):
                harness.run()
    run = harness.deps.store.list_runs(33)[0]
    pending = [op for op in harness.deps.store.pending_operations(run.id) if op.kind == "repair"]
    assert len(pending) == 1
    calls_before = len(adapter.requests)
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    (path / "allowed.txt").write_text("done")
    begin_attempt = harness.deps.store.begin_attempt
    stages: list[str] = []

    def record_attempt(run_id: int, stage: str) -> StageAttempt:
        stages.append(stage)
        return begin_attempt(run_id, stage)

    monkeypatch.setattr(harness.deps.store, "begin_attempt", record_attempt)
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.run_id == run.id
    assert len(adapter.requests) == calls_before
    assert "repair" not in stages
    assert not harness.deps.store.pending_operations(run.id)
    satisfied = harness.deps.store.begin_operation(run.id, "repair", pending[0].key, {})
    assert satisfied.result_json is not None
    assert json.loads(satisfied.result_json) == {"satisfied_by_gate": True}


@pytest.mark.parametrize("interruption", ["after-implementation", "after-commit", "after-push"])
def test_title_change_preserves_run_identity(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    ticket = harness.github.ticket
    branch = branch_name(ticket)
    path = worktree_path(harness.deps.worktrees_root, ticket)
    subject = "Feature[#33]: Add the engine"

    async def interrupt_verification(self: Engine, run_id: int, path: Path) -> None:
        raise RuntimeError("interrupt delivery")

    async def interrupt_push(self: Git, branch: str, remote: str = "origin") -> None:
        raise RuntimeError("interrupt delivery")

    async def interrupt_find(branch: str) -> PullRequest | None:
        raise RuntimeError("interrupt delivery")

    with monkeypatch.context() as patch:
        if interruption == "after-implementation":
            patch.setattr(Engine, "_verify", interrupt_verification)
        elif interruption == "after-commit":
            patch.setattr(Git, "push", interrupt_push)
        else:
            patch.setattr(harness.github, "find_pr", interrupt_find)
        with pytest.raises(RuntimeError, match="interrupt delivery"):
            harness.run()
    run = harness.deps.store.list_runs(33)[0]
    preparation = harness.deps.store.begin_operation(run.id, "worktree", branch, {})
    assert preparation.result_json is not None
    prepared = json.loads(preparation.result_json)
    assert prepared["branch"] == branch
    assert prepared["path"] == str(path)
    assert prepared["subject"] == subject
    markers = [
        a for a in harness.deps.store.list_artifacts(run.id) if a.name.startswith("implemented-")
    ]
    marker = json.loads(Path(markers[-1].path).read_text())
    assert marker["branch"] == branch
    assert marker["path"] == str(path)
    harness.github.ticket = harness.github.ticket.model_copy(
        update={"title": "renamed ticket after interruption"}
    )
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.run_id == run.id
    assert len(harness.adapter.requests) == 1
    assert harness.adapter.requests[0].cwd == path
    assert list(harness.deps.worktrees_root.iterdir()) == [path]
    assert asyncio.run(Git(path).current_branch()) == branch
    assert asyncio.run(git_command(path, "log", "-1", "--format=%s")) == subject
    assert harness.github.created[0][0] == branch
    assert harness.github.created[0][2] == subject
    # A second resume must use the same recorded commit and PR after the rename.
    assert harness.run().head_sha == outcome.head_sha
    assert len(harness.github.created) == 1


@pytest.mark.parametrize("title", ["add the engine", "Add the engine."])
def test_normalized_title_is_persisted_and_delivered(harness: Harness, title: str) -> None:
    harness.github.ticket = harness.github.ticket.model_copy(update={"title": title})
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    identity = harness.deps.store.begin_operation(outcome.run_id, "prepare", "identity", {})
    assert identity.result_json is not None
    saved = json.loads(identity.result_json)
    assert saved["explanation"] == "Add the engine"
    assert saved["subject"] == "Feature[#33]: Add the engine"
    path = Path(saved["path"])
    assert asyncio.run(git_command(path, "log", "-1", "--format=%s")) == saved["subject"]
    assert harness.github.created[0][2] == saved["subject"]


@pytest.mark.parametrize("title", ["", " \n\t", "... . "])
def test_invalid_title_does_not_persist_identity_and_can_be_corrected(
    harness: Harness, title: str
) -> None:
    harness.github.ticket = harness.github.ticket.model_copy(update={"title": title})
    first = harness.run()
    assert first.state == RunState.BLOCKED
    assert "Explanation must be capitalised" in first.problems
    identity = harness.deps.store.begin_operation(first.run_id, "prepare", "identity", {})
    assert identity.result_json is None
    assert json.loads(identity.intent_json) == {"repo_root": str(harness.deps.repo_root.resolve())}
    assert not harness.deps.worktrees_root.exists()
    assert not harness.adapter.requests
    assert "status" not in harness.github.calls
    harness.github.ticket = harness.github.ticket.model_copy(update={"title": "Add the engine"})
    second = harness.run()
    assert second.run_id == first.run_id
    assert second.state == RunState.WAITING_FOR_CI
    identity = harness.deps.store.begin_operation(first.run_id, "prepare", "identity", {})
    assert identity.result_json is not None
    assert json.loads(identity.result_json)["subject"] == "Feature[#33]: Add the engine"
    assert len(harness.adapter.requests) == 1


@pytest.mark.parametrize(
    ("provider", "error"),
    [
        ("claude", "Claude provider-limited: rejected"),
        ("claude", "Capacity unavailable"),
        ("codex", None),
        ("codex", "Usage limit reached"),
    ],
)
def test_provider_limited_run_resumes_session(
    harness: Harness, provider: str, error: str | None
) -> None:
    harness.deps.config.roles[Role.IMPLEMENTER] = (
        RoleConfig(provider="claude") if provider == "claude" else RoleConfig(provider="codex")
    )
    limited = replace(
        result(),
        ok=False,
        provider=provider,
        error=error,
        provider_limited=True,
    )
    adapter = ScriptedAdapter(
        [
            ScriptStep(result=limited),
            ScriptStep(result=replace(result(), provider=provider), side_effect=write_allowed),
        ],
        name=provider,
    )
    harness.deps.registry.register(provider, lambda config: adapter)
    first = harness.run()
    assert first.state == RunState.PROVIDER_LIMITED
    assert harness.deps.store.get_run(first.run_id).state == RunState.PROVIDER_LIMITED
    assert len(adapter.requests) == 1
    assert not harness.github.created
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id == first.run_id
    assert len(harness.deps.store.list_runs(33)) == 1
    assert len(adapter.requests) == 2
    assert adapter.requests[1].session_id == "session-1"
    assert adapter.requests[1].cwd == adapter.requests[0].cwd
    assert harness.deps.store.usage_summary(first.run_id)["input_tokens"] == 20


@pytest.mark.parametrize("separation", ["repository", "checkout", "both"])
@pytest.mark.parametrize("blocked_before_prepare", [False, True])
def test_shared_store_isolates_repository_and_checkout(
    harness: Harness, tmp_path: Path, separation: str, blocked_before_prepare: bool
) -> None:
    first_adapter = ScriptedAdapter(
        [
            ScriptStep(result=replace(result(), ok=False, provider_limited=True, error="Limited")),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: first_adapter)
    if blocked_before_prepare:
        harness.github.scope = "No allowed files"
    first = harness.run()
    assert first.state == (
        RunState.BLOCKED if blocked_before_prepare else RunState.PROVIDER_LIMITED
    )
    assert harness.deps.store.get_run(first.run_id).repo == "owner/repo"
    first_identity = harness.deps.store.begin_operation(first.run_id, "prepare", "identity", {})
    first_saved = json.loads(first_identity.result_json or first_identity.intent_json)
    assert first_saved["repo_root"] == str(harness.deps.repo_root.resolve())
    first_state = harness.deps.store.get_run(first.run_id).state

    if separation in ("repository", "both"):
        harness.github.repo = "owner/another-repository"
    if separation in ("checkout", "both"):
        clone = tmp_path / "another-clone"
        asyncio.run(git_command(tmp_path, "clone", str(harness.origin), str(clone)))
        asyncio.run(git_command(clone, "config", "user.name", "Test Developer"))
        asyncio.run(git_command(clone, "config", "user.email", "test@example.test"))
        harness.deps.repo_root = clone
    # Separate branches also allow the same checkout to be repointed to another
    # GitHub repository without colliding with the old run's worktree.
    harness.github.ticket = harness.github.ticket.model_copy(
        update={"title": "Implement another task"}
    )
    harness.github.scope = SCOPE
    harness.deps.worktrees_root = tmp_path / "another-worktrees"
    second_adapter = ScriptedAdapter(
        [
            ScriptStep(result=result(), side_effect=write_allowed),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: second_adapter)
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id != first.run_id
    assert len(harness.deps.store.list_runs(33)) == 2
    assert harness.deps.store.get_run(first.run_id).state == first_state
    assert harness.deps.store.get_run(second.run_id).repo == harness.github.repo
    assert second_adapter.requests[0].session_id is None
    second_identity = harness.deps.store.begin_operation(second.run_id, "prepare", "identity", {})
    assert second_identity.result_json is not None
    assert json.loads(second_identity.result_json)["repo_root"] == str(
        harness.deps.repo_root.resolve()
    )


def test_run_reuses_resolved_checkout_identity(harness: Harness, tmp_path: Path) -> None:
    adapter = ScriptedAdapter(
        [
            ScriptStep(result=replace(result(), ok=False, provider_limited=True, error="Limited")),
            ScriptStep(result=result(), side_effect=write_allowed),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    first = harness.run()
    alias = tmp_path / "clone-alias"
    alias.symlink_to(harness.deps.repo_root, target_is_directory=True)
    harness.deps.repo_root = alias
    harness.github.repo = "OWNER/REPO"
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id == first.run_id
    assert len(harness.deps.store.list_runs(33)) == 1
    assert adapter.requests[1].session_id == "session-1"


@pytest.mark.parametrize("change", ["provider", "authentication"])
@pytest.mark.parametrize("stage", ["implement", "repair"])
def test_session_reuse_requires_matching_provider_and_authentication(
    harness: Harness, change: str, stage: str
) -> None:
    limited = replace(
        result(),
        ok=False,
        error="Codex rate limit",
        provider_limited=True,
        session_id="codex-session",
    )

    def broken(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text("broken")

    steps = [ScriptStep(result=limited)]
    if stage == "repair":
        harness.deps.config.policy.gate_commands = [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('allowed.txt').read_text() == 'done'",
            ]
        ]
        steps.insert(0, ScriptStep(result=result(), side_effect=broken))
    original = ScriptedAdapter(steps, name="codex")
    harness.deps.registry.register("codex", lambda config: original)
    first = harness.run()
    assert first.state == RunState.PROVIDER_LIMITED
    if change == "provider":
        config = RoleConfig(provider="claude")
    else:
        config = RoleConfig.model_validate(
            {"provider": "codex", "authentication_mode": "api_key"},
            context={"from_toml": True},
        )
    harness.deps.config.roles[Role.IMPLEMENTER] = config
    success = replace(
        result(),
        provider=config.provider,
        authentication_mode=config.authentication_mode,
        session_id="new-session",
    )
    replacement = ScriptedAdapter(
        [
            ScriptStep(result=success, side_effect=write_allowed),
        ],
        name=config.provider,
        authentication_mode=config.authentication_mode,
    )
    harness.deps.registry.register(config.provider, lambda role_config: replacement)
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id == first.run_id
    assert len(replacement.requests) == 1
    assert replacement.requests[0].session_id is None
    assert SCOPE in replacement.requests[0].prompt
    if stage == "repair":
        assert "Gate failed:" in replacement.requests[0].prompt
    assert len(harness.deps.store.list_runs(33)) == 1


def test_switching_back_reuses_latest_compatible_session(harness: Harness) -> None:
    codex = ScriptedAdapter(
        [
            ScriptStep(
                result=replace(
                    result(),
                    ok=False,
                    provider_limited=True,
                    error="Limited",
                    session_id="codex-session",
                )
            ),
            ScriptStep(result=result(), side_effect=write_allowed),
        ],
        name="codex",
    )
    claude = ScriptedAdapter(
        [
            ScriptStep(
                result=replace(
                    result(),
                    ok=False,
                    provider="claude",
                    provider_limited=True,
                    error="Limited",
                    session_id="claude-session",
                )
            ),
        ],
        name="claude",
    )
    harness.deps.registry.register("codex", lambda config: codex)
    harness.deps.registry.register("claude", lambda config: claude)
    first = harness.run()
    assert first.state == RunState.PROVIDER_LIMITED
    harness.deps.config.roles[Role.IMPLEMENTER] = RoleConfig(provider="claude")
    assert harness.run().state == RunState.PROVIDER_LIMITED
    assert claude.requests[0].session_id is None
    harness.deps.config.roles[Role.IMPLEMENTER] = RoleConfig(provider="codex")
    last = harness.run()
    assert last.state == RunState.WAITING_FOR_CI
    assert last.run_id == first.run_id
    assert codex.requests[-1].session_id == "codex-session"


@pytest.mark.parametrize("name", ["-outside.txt", "outside[1].txt"])
@pytest.mark.parametrize("allowed", [False, True])
def test_special_changed_paths_block_without_delivery(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, name: str, allowed: bool
) -> None:
    if allowed:
        harness.github.scope = "You may touch only these files:\n- `*`\n\nImplement the change."

    def write_files(request: AgentRequest) -> None:
        write_allowed(request)
        (request.cwd / name).write_text("Special filename")

    adapter = ScriptedAdapter([ScriptStep(result=result(), side_effect=write_files)], name="codex")
    harness.deps.registry.register("codex", lambda config: adapter)
    stage_paths: list[str] = []
    original_stage = Git.stage

    async def record_stage(self: Git, paths: Sequence[str]) -> None:
        stage_paths.extend(paths)
        await original_stage(self, paths)

    monkeypatch.setattr(Git, "stage", record_stage)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert harness.deps.store.get_run(outcome.run_id).state == RunState.BLOCKED
    if allowed:
        assert name in stage_paths
        assert any(f"Cannot stage path {name!r}" in problem for problem in outcome.problems)
    else:
        assert not stage_paths
        assert f"Outside allowed files: {name}" in outcome.problems
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert asyncio.run(Git(path).head_sha()) == harness.initial_head
    assert not harness.github.created
    assert asyncio.run(git_command(harness.origin, "branch", "--list", "feature/*")) == ""


def test_git_stage_error_blocks_and_identifies_path(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def reject_stage(self: Git, paths: Sequence[str]) -> None:
        raise GitError(("git", "add", "--", *paths), 1, "Index is locked")

    monkeypatch.setattr(Git, "stage", reject_stage)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert harness.deps.store.get_run(outcome.run_id).state == RunState.BLOCKED
    assert "Cannot stage path 'allowed.txt'" in outcome.problems[0]
    assert "Index is locked" in outcome.problems[0]
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert asyncio.run(Git(path).head_sha()) == harness.initial_head
    assert not harness.github.created


@pytest.mark.parametrize(
    "stage",
    ["load", "validate", "prepare", "implement", "verify", "repair", "deliver", "checkpoint"],
)
def test_unexpected_stage_failure_blocks_before_propagating(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Unexpected stage failure")

    if stage == "load":
        monkeypatch.setattr(harness.github, "get_ticket", fail)
    elif stage == "verify":
        monkeypatch.setattr(harness.deps, "gate_runner", fail)
    elif stage == "repair":

        def fail_repair(request: AgentRequest) -> None:
            raise RuntimeError("Unexpected stage failure")

        harness.deps.config.policy.gate_commands = [[sys.executable, "-c", "raise SystemExit(1)"]]
        adapter = ScriptedAdapter(
            [
                ScriptStep(result=result(), side_effect=write_allowed),
                ScriptStep(side_effect=fail_repair),
            ],
            name="codex",
        )
        harness.deps.registry.register("codex", lambda config: adapter)
    elif stage == "checkpoint":
        write_artifact = Engine._artifact

        def fail_checkpoint(self: Engine, run_id: int, name: str, text: str) -> None:
            if name.startswith("implemented-"):
                raise RuntimeError("Unexpected stage failure")
            write_artifact(self, run_id, name, text)

        monkeypatch.setattr(Engine, "_artifact", fail_checkpoint)
    else:
        method = {
            "validate": "_validate",
            "prepare": "_prepare",
            "implement": "_agent",
            "deliver": "_deliver",
        }[stage]
        monkeypatch.setattr(Engine, method, fail)
    with pytest.raises(RuntimeError, match="Unexpected stage failure"):
        harness.run()
    run = harness.deps.store.list_runs(33)[0]
    assert harness.deps.store.get_run(run.id).state == RunState.BLOCKED
    assert not harness.github.created


@pytest.mark.parametrize("matching_local_branch", [False, True])
def test_shared_worktrees_root_cannot_adopt_another_clones_worktree(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matching_local_branch: bool
) -> None:
    original_adapter = ScriptedAdapter(
        [
            ScriptStep(
                result=replace(result(), ok=False, provider_limited=True, error="Limited"),
                side_effect=write_allowed,
            ),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: original_adapter)
    first = harness.run()
    assert first.state == RunState.PROVIDER_LIMITED
    branch = branch_name(harness.github.ticket)
    shared_path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    before_status = asyncio.run(Git(shared_path).status_porcelain())
    before_content = (shared_path / "allowed.txt").read_bytes()
    second_root = tmp_path / "second-clone"
    asyncio.run(git_command(tmp_path, "clone", str(harness.origin), str(second_root)))
    if matching_local_branch:
        asyncio.run(git_command(second_root, "branch", branch, "develop"))
    github = FakeGitHub(harness.github.ticket.model_copy(update={"status": Status.TO_DO}))
    adapter = ScriptedAdapter([], name="codex")
    registry = AdapterRegistry()
    registry.register("codex", lambda config: adapter)
    deps = replace(harness.deps, repo_root=second_root, github=github, registry=registry)

    async def unexpected_mutation(*args: object, **kwargs: object) -> None:
        pytest.fail("A foreign worktree must block before external mutations")

    for method in ("fetch", "fast_forward", "create_worktree", "stage", "commit", "push"):
        monkeypatch.setattr(Git, method, unexpected_mutation)
    monkeypatch.setattr(github, "set_status", unexpected_mutation)
    monkeypatch.setattr(github, "create_pr", unexpected_mutation)
    second = asyncio.run(Engine(deps).run_to_pr(33))
    assert second.state == RunState.BLOCKED
    assert second.run_id != first.run_id
    assert "belongs to a different repository" in second.problems[0]
    assert str(shared_path) in second.problems[0]
    assert str((second_root / ".git").resolve()) in second.problems[0]
    assert not adapter.requests
    assert github.calls == ["download"]
    assert asyncio.run(Git(shared_path).head_sha()) == harness.initial_head
    assert asyncio.run(Git(shared_path).status_porcelain()) == before_status
    assert (shared_path / "allowed.txt").read_bytes() == before_content
    assert asyncio.run(Git(second_root).head_sha()) == harness.initial_head
    assert harness.deps.store.get_run(first.run_id).state == RunState.PROVIDER_LIMITED
    assert harness.deps.store.get_run(second.run_id).state == RunState.BLOCKED
