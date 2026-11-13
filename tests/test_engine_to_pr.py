"""Engine behaviour against local Git repositories and scripted providers."""

import asyncio
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
from fugue.store import Store
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
        self.create_error: Exception | None = None

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
        if self.create_error is not None:
            error, self.create_error = self.create_error, None
            raise error
        self.created.append((branch, base, title, body, assignee, milestone, project_title))
        self.pr = PullRequest(71, "https://example.test/pr/71")
        return self.pr


def result(
    *, ok: bool = True, error: str | None = None, final_text: str = "Implemented"
) -> AgentResult:
    return AgentResult(
        ok=ok,
        final_text=final_text,
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


def interrupt_push(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stop(self: Git, branch: str, remote: str = "origin") -> None:
        raise RuntimeError("stop before push")

    monkeypatch.setattr(Git, "push", stop)


def forbid_push(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected(self: Git, branch: str, remote: str = "origin") -> None:
        pytest.fail("The branch must not be pushed again")

    monkeypatch.setattr(Git, "push", unexpected)


def stop_after_commit(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run until the commit exists locally, then stop before the push."""
    with monkeypatch.context() as patch:
        interrupt_push(patch)
        with pytest.raises(RuntimeError, match="stop before push"):
            harness.run()
    return worktree_path(harness.deps.worktrees_root, harness.github.ticket)


def stop_during_implementation(harness: Harness, content: str) -> None:
    """Leave a dirty tree with no commit, as an interrupted implementer would."""

    def write_then_crash(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text(content)
        raise RuntimeError("stop during implementation")

    adapter = ScriptedAdapter([ScriptStep(side_effect=write_then_crash)], name="codex")
    harness.deps.registry.register("codex", lambda config: adapter)
    with pytest.raises(RuntimeError, match="stop during implementation"):
        harness.run()


def agent_sessions(store: Store, run_id: int) -> list[str]:
    rows = store._connection.execute(
        "SELECT session_id FROM agent_sessions WHERE run_id = ?", (run_id,)
    ).fetchall()
    return [row[0] for row in rows]


def commit_count(path: Path, base: str) -> int:
    return int(asyncio.run(git_command(path, "rev-list", "--count", f"{base}..HEAD")))


def gate_requires_done() -> list[list[str]]:
    return [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('allowed.txt').read_text() == 'done'",
        ]
    ]


# Scenario 1
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
    assert agent_sessions(harness.deps.store, outcome.run_id) == ["session-1"]
    assert harness.adapter.requests[0].access == AccessMode.WORKSPACE_WRITE
    assert harness.github.created[0][2] == subject
    assert harness.github.created[0][4:] == ("developer", "0.1.0", "Fugue")
    assert "Closes #33" in harness.github.created[0][3]


# Scenario 2
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
    assert harness.deps.store.get_run(outcome.run_id).state == RunState.BLOCKED
    assert not harness.adapter.requests
    assert not harness.deps.worktrees_root.exists()
    assert set(harness.github.calls) <= {"download"}
    assert asyncio.run(Git(harness.deps.repo_root).head_sha()) == harness.initial_head


# Scenario 3
@pytest.mark.parametrize("session_id", ["session-1", None])
def test_gate_repair_resumes_session(harness: Harness, session_id: str | None) -> None:
    harness.deps.config.policy.gate_commands = gate_requires_done()

    def broken(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text("broken")

    adapter = ScriptedAdapter(
        [
            ScriptStep(result=replace(result(), session_id=session_id), side_effect=broken),
            ScriptStep(result=result(), side_effect=write_allowed),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert len(adapter.requests) == 2
    assert adapter.requests[1].session_id == session_id
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket).resolve()
    branch = branch_name(harness.github.ticket)
    assert adapter.requests[1].prompt.startswith(
        f"Your working directory is {path} on branch {branch}. Work only there. "
        "If the scope below names a different worktree path or branch, treat that as a "
        "placeholder and use this directory and branch.\n\n"
    )
    if session_id is None:
        assert SCOPE in adapter.requests[1].prompt
    assert "Gate failed:" in adapter.requests[1].prompt
    assert harness.deps.store.usage_summary(outcome.run_id)["input_tokens"] == 20


# Scenario 3
def test_repair_limit(harness: Harness) -> None:
    harness.deps.config.limits.max_repair_attempts = 1
    harness.deps.config.policy.gate_commands = [
        [sys.executable, "-c", "import sys; print('failure evidence'); sys.exit(1)"]
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
    assert len({a.name for a in failures}) == 2
    assert "failure evidence" in Path(failures[-1].path).read_text()
    assert "Return code: 1" in outcome.problems[0]
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert asyncio.run(Git(path).head_sha()) == harness.initial_head
    assert not harness.github.created


# Scenario 4
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


# Scenario 4
@pytest.mark.parametrize("how", ["amended", "extra-commit"])
def test_commits_ahead_outside_allowed_files_block(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    path = stop_after_commit(harness, monkeypatch)

    async def tamper() -> None:
        (path / "disallowed.txt").write_text("outside scope")
        await git_command(path, "add", "--", "disallowed.txt")
        if how == "amended":
            await git_command(path, "commit", "--amend", "--no-edit")
        else:
            await git_command(path, "commit", "-m", "Extra")

    asyncio.run(tamper())
    forbid_push(monkeypatch)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("Outside allowed files: disallowed.txt",)
    assert not harness.github.created
    assert asyncio.run(git_command(harness.origin, "branch", "--list", "feature/*")) == ""


# Scenario 5
def test_resume_after_worktree_creation_implements(harness: Harness) -> None:
    def crash(request: AgentRequest) -> None:
        raise RuntimeError("stop before implementation")

    adapter = ScriptedAdapter(
        [
            ScriptStep(side_effect=crash),
            ScriptStep(result=result(), side_effect=write_allowed),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    with pytest.raises(RuntimeError, match="stop before implementation"):
        harness.run()
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert path.exists()
    assert asyncio.run(Git(path).status_porcelain()) == []
    first_run = harness.deps.store.list_runs(33)[0]
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.run_id == first_run.id
    assert len(adapter.requests) == 2
    assert harness.github.calls.count("status") == 1
    assert len(harness.github.created) == 1


# Scenario 6
@pytest.mark.parametrize("gate_passes", [True, False])
def test_resume_with_dirty_tree_runs_the_gate(harness: Harness, gate_passes: bool) -> None:
    harness.deps.config.policy.gate_commands = gate_requires_done()
    stop_during_implementation(harness, "done" if gate_passes else "broken")
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert asyncio.run(Git(path).status_porcelain()) == ["?? allowed.txt"]
    repair = ScriptedAdapter([ScriptStep(result=result(), side_effect=write_allowed)], name="codex")
    harness.deps.registry.register("codex", lambda config: repair)
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert commit_count(path, "develop") == 1
    if gate_passes:
        assert not repair.requests
    else:
        assert len(repair.requests) == 1
        assert "Gate failed:" in repair.requests[0].prompt
        row = harness.deps.store._connection.execute(
            "SELECT COUNT(*) FROM stage_attempts WHERE run_id = ? AND stage = 'repair' "
            "AND status = 'completed'",
            (outcome.run_id,),
        ).fetchone()
        assert row[0] == 1


# Scenario 7
def test_resume_after_commit_pushes(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    path = stop_after_commit(harness, monkeypatch)
    head = asyncio.run(Git(path).head_sha())
    assert asyncio.run(git_command(harness.origin, "branch", "--list", "feature/*")) == ""
    monkeypatch.undo()
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.head_sha == head
    assert asyncio.run(Git(harness.origin).head_sha(branch_name(harness.github.ticket))) == head
    assert commit_count(path, "develop") == 1
    assert len(harness.adapter.requests) == 1
    assert len(harness.github.created) == 1


# Scenario 7
def test_resume_after_push_opens_the_pull_request(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.github.create_error = RuntimeError("stop before the pull request")
    with pytest.raises(RuntimeError, match="stop before the pull request"):
        harness.run()
    branch = branch_name(harness.github.ticket)
    pushed = asyncio.run(Git(harness.origin).head_sha(branch))
    forbid_push(monkeypatch)
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.head_sha == pushed
    assert outcome.pr_number == 71
    assert len(harness.github.created) == 1
    assert len(harness.adapter.requests) == 1
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert commit_count(path, "develop") == 1


# Scenario 7
def test_existing_pull_request_is_done_without_new_work(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = harness.run()
    forbid_push(monkeypatch)
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert (second.run_id, second.pr_number, second.head_sha) == (
        first.run_id,
        first.pr_number,
        first.head_sha,
    )
    assert len(harness.adapter.requests) == 1
    assert len(harness.github.created) == 1
    assert harness.github.calls.count("status") == 1
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    assert commit_count(path, "develop") == 1


# Scenario 8
def test_base_advance_is_measured_from_the_merge_base(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = stop_after_commit(harness, monkeypatch)
    clone = harness.deps.repo_root

    async def advance_base() -> None:
        (clone / "other.txt").write_text("Merged by another ticket")
        await git_command(clone, "add", "other.txt")
        await git_command(clone, "commit", "-m", "Another ticket")
        await git_command(clone, "push", "origin", "develop")

    asyncio.run(advance_base())
    monkeypatch.undo()
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert outcome.problems == ()
    assert commit_count(path, "refs/heads/develop~1") == 1
    assert len(harness.github.created) == 1


# Scenario 9
def test_replaced_scope_implements_again(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    new_scope = SCOPE + "\n\nAlso update the content."

    def write_revised(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text("revised")

    adapter = ScriptedAdapter(
        [
            ScriptStep(result=result(), side_effect=write_allowed),
            ScriptStep(result=result(), side_effect=write_revised),
        ],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    path = stop_after_commit(harness, monkeypatch)
    monkeypatch.undo()
    harness.github.scope = new_scope
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert len(adapter.requests) == 2
    assert adapter.requests[1].prompt.endswith(new_scope)
    assert (path / "allowed.txt").read_text() == "revised"
    assert commit_count(path, "develop") == 2
    assert harness.github.calls.count("download") == 2
    scopes = [
        a for a in harness.deps.store.list_artifacts(outcome.run_id) if a.name.startswith("scope-")
    ]
    assert len({a.sha256 for a in scopes}) == 2


# Scenario 10
def test_cancellation(harness: Harness) -> None:
    def cancel(request: AgentRequest) -> None:
        raise asyncio.CancelledError

    adapter = ScriptedAdapter([ScriptStep(side_effect=cancel)], name="codex")
    harness.deps.registry.register("codex", lambda config: adapter)
    with pytest.raises(asyncio.CancelledError):
        harness.run()
    assert harness.deps.store.list_runs(33)[0].state == RunState.INTERRUPTED
    assert not harness.github.created


# Scenario 10
@pytest.mark.parametrize(
    ("error", "flag", "state"),
    [
        ("Provider rate limited (429)", True, RunState.PROVIDER_LIMITED),
        ("Provider rate limited (429)", False, RunState.FAILED),
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
    assert harness.deps.store.get_run(outcome.run_id).state == state
    assert outcome.problems == (error,)
    errors = [
        a
        for a in harness.deps.store.list_artifacts(outcome.run_id)
        if a.name.startswith("agent-error-")
    ]
    assert Path(errors[0].path).read_text() == error
    assert not harness.github.created


# Scenario 10
def test_provider_limited_run_resumes_session(harness: Harness) -> None:
    limited = replace(result(), ok=False, error="Limited", provider_limited=True)
    adapter = ScriptedAdapter(
        [ScriptStep(result=limited), ScriptStep(result=result(), side_effect=write_allowed)],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    first = harness.run()
    assert first.state == RunState.PROVIDER_LIMITED
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id == first.run_id
    assert len(harness.deps.store.list_runs(33)) == 1
    assert adapter.requests[1].session_id == "session-1"
    assert adapter.requests[1].cwd == adapter.requests[0].cwd
    assert harness.deps.store.usage_summary(first.run_id)["input_tokens"] == 20


# Scenario 10
@pytest.mark.parametrize("change", ["provider", "authentication"])
def test_session_reuse_requires_matching_provider_and_authentication(
    harness: Harness, change: str
) -> None:
    limited = replace(
        result(), ok=False, error="Rate limit", provider_limited=True, session_id="codex-session"
    )
    original = ScriptedAdapter([ScriptStep(result=limited)], name="codex")
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
        [ScriptStep(result=success, side_effect=write_allowed)],
        name=config.provider,
        authentication_mode=config.authentication_mode,
    )
    harness.deps.registry.register(config.provider, lambda role_config: replacement)
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id == first.run_id
    assert replacement.requests[0].session_id is None
    assert SCOPE in replacement.requests[0].prompt


# Scenario 11
@pytest.mark.parametrize("separation", ["repository", "checkout", "both"])
def test_another_repository_or_checkout_starts_a_new_run(
    harness: Harness, tmp_path: Path, separation: str
) -> None:
    limited = replace(result(), ok=False, provider_limited=True, error="Limited")
    first_adapter = ScriptedAdapter([ScriptStep(result=limited)], name="codex")
    harness.deps.registry.register("codex", lambda config: first_adapter)
    first = harness.run()
    assert first.state == RunState.PROVIDER_LIMITED
    first_path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)

    if separation in ("repository", "both"):
        harness.github.repo = "owner/another-repository"
    if separation in ("checkout", "both"):
        clone = tmp_path / "another-clone"
        asyncio.run(git_command(tmp_path, "clone", str(harness.origin), str(clone)))
        asyncio.run(git_command(clone, "config", "user.name", "Test Developer"))
        asyncio.run(git_command(clone, "config", "user.email", "test@example.test"))
        harness.deps.repo_root = clone
    harness.github.ticket = harness.github.ticket.model_copy(
        update={"title": "Implement another task"}
    )
    harness.deps.worktrees_root = tmp_path / "another-worktrees"
    second_adapter = ScriptedAdapter(
        [ScriptStep(result=result(), side_effect=write_allowed)], name="codex"
    )
    harness.deps.registry.register("codex", lambda config: second_adapter)
    second = harness.run()
    assert second.state == RunState.WAITING_FOR_CI
    assert second.run_id != first.run_id
    assert len(harness.deps.store.list_runs(33)) == 2
    assert harness.deps.store.get_run(first.run_id).state == RunState.PROVIDER_LIMITED
    assert harness.deps.store.get_run(second.run_id).repo == harness.github.repo
    assert second_adapter.requests[0].session_id is None
    assert second_adapter.requests[0].cwd != first_path


# Scenario 11
def test_run_reuses_resolved_checkout_identity(harness: Harness, tmp_path: Path) -> None:
    limited = replace(result(), ok=False, provider_limited=True, error="Limited")
    adapter = ScriptedAdapter(
        [ScriptStep(result=limited), ScriptStep(result=result(), side_effect=write_allowed)],
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


# Scenario 11
def test_ticket_rename_keeps_the_first_identity(harness: Harness) -> None:
    limited = replace(result(), ok=False, provider_limited=True, error="Limited")
    adapter = ScriptedAdapter(
        [ScriptStep(result=limited), ScriptStep(result=result(), side_effect=write_allowed)],
        name="codex",
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    first = harness.run()
    original = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    harness.github.ticket = harness.github.ticket.model_copy(update={"title": "Renamed"})
    second = harness.run()
    assert second.run_id == first.run_id
    assert adapter.requests[1].cwd == original.resolve()
    assert harness.github.created[0][0] == "feature/33-add-the-engine"


# Scenario 12
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


# Scenario 12
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
    assert not harness.github.created


# Scenario 12
@pytest.mark.parametrize("stage", ["load", "validate", "prepare", "implement", "verify", "commit"])
def test_unexpected_stage_failure_blocks_before_propagating(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Unexpected stage failure")

    if stage == "load":
        monkeypatch.setattr(harness.github, "get_ticket", fail)
    elif stage == "verify":
        monkeypatch.setattr(harness.deps, "gate_runner", fail)
    else:
        method = {
            "validate": "_validate",
            "prepare": "_prepare",
            "implement": "_agent",
            "commit": "_commit",
        }[stage]
        monkeypatch.setattr(Engine, method, fail)
    with pytest.raises(RuntimeError, match="Unexpected stage failure"):
        harness.run()
    run = harness.deps.store.list_runs(33)[0]
    assert harness.deps.store.get_run(run.id).state == RunState.BLOCKED
    assert not harness.github.created


@pytest.mark.parametrize("title", ["", " \n\t", "... . "])
def test_invalid_title_blocks_before_anything_is_persisted(harness: Harness, title: str) -> None:
    harness.github.ticket = harness.github.ticket.model_copy(update={"title": title})
    first = harness.run()
    assert first.state == RunState.BLOCKED
    assert "Explanation must be capitalised" in first.problems
    assert not harness.deps.worktrees_root.exists()
    assert "status" not in harness.github.calls
    harness.github.ticket = harness.github.ticket.model_copy(update={"title": "Add the engine"})
    second = harness.run()
    assert second.run_id == first.run_id
    assert second.state == RunState.WAITING_FOR_CI


def delivered_changes(harness: Harness) -> tuple[str, str]:
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket)
    commit_body = asyncio.run(git_command(path, "log", "-1", "--format=%b"))
    pr_text = harness.github.created[0][3]
    return commit_body, pr_text


def changes_block(text: str) -> str:
    return text.split("## Changes\n\n", 1)[1].split("\n\n## ", 1)[0]


def use_messages(harness: Harness, *steps: ScriptStep) -> None:
    adapter = ScriptedAdapter(list(steps), name="codex")
    harness.deps.registry.register("codex", lambda config: adapter)


def broken(request: AgentRequest) -> None:
    (request.cwd / "allowed.txt").write_text("broken")


# Scenario: Changes section
def test_changes_come_from_the_implementer_message(harness: Harness) -> None:
    final = "Done.\n\n## Changes\n\n- allowed.txt: hold the result\n- notes: explain why"
    use_messages(harness, ScriptStep(result=result(final_text=final), side_effect=write_allowed))
    harness.run()
    commit_body, pr_text = delivered_changes(harness)
    bullets = "- allowed.txt: hold the result\n- notes: explain why"
    assert f"**Changes**\n{bullets}" in commit_body
    assert changes_block(pr_text) == bullets
    assert "implement the selected scope" not in commit_body + pr_text
    assert "gate passed" not in commit_body
    assert sys.executable in pr_text.split("## Verification")[1]


# Scenario: no, malformed or empty Changes section
@pytest.mark.parametrize(
    "final",
    [
        "Implemented",
        "## Changes\n\n",
        "## Changes\n- vague bullet",
        "## Changes\n- No files changed: nothing to report",
        "## Changes\n- allowed.txt: NO FILES CHANGED",
    ],
)
def test_changes_fall_back_to_changed_paths(harness: Harness, final: str) -> None:
    use_messages(harness, ScriptStep(result=result(final_text=final), side_effect=write_allowed))
    harness.run()
    commit_body, pr_text = delivered_changes(harness)
    assert "**Changes**\n- allowed.txt: updated" in commit_body
    assert changes_block(pr_text) == "- allowed.txt: updated"
    assert sys.executable not in commit_body + changes_block(pr_text)


# Scenario: gate command as a change
def test_gate_commands_never_appear_as_changes(harness: Harness) -> None:
    command = " ".join(harness.deps.config.policy.gate_commands[0])
    final = f"## Changes\n- {command}: it passed"
    use_messages(harness, ScriptStep(result=result(final_text=final), side_effect=write_allowed))
    harness.run()
    _, pr_text = delivered_changes(harness)
    assert changes_block(pr_text) == "- allowed.txt: updated"
    assert command in pr_text.split("## Verification")[1]


@pytest.mark.parametrize(
    "first_text",
    ["## Changes\n- No files changed: nothing to report", "## Changes\n- allowed.txt: stale"],
)
@pytest.mark.parametrize("second_text", ["## Changes\n- allowed.txt: delivered", "Implemented"])
def test_changes_only_use_the_latest_implement_attempt(
    harness: Harness, first_text: str, second_text: str
) -> None:
    use_messages(
        harness,
        ScriptStep(result=result(final_text=first_text)),
        ScriptStep(result=result(final_text=second_text), side_effect=write_allowed),
    )
    first = harness.run()
    assert first.state == RunState.BLOCKED
    assert first.problems == ("Implementer made no changes",)
    second = harness.run()
    assert second.run_id == first.run_id
    assert second.state == RunState.WAITING_FOR_CI
    expected = (
        "- allowed.txt: delivered" if "## Changes" in second_text else "- allowed.txt: updated"
    )
    commit_body, pr_text = delivered_changes(harness)
    assert changes_block(pr_text) == expected
    assert f"**Changes**\n{expected}" in commit_body


# Scenario: repaired run
@pytest.mark.parametrize(
    "repair_text",
    ["Fixed it", "## Changes\n\n", "## Changes\n- allowed.txt: No FiLeS ChAnGeD"],
)
def test_repaired_run_uses_the_last_message_with_changes(
    harness: Harness, repair_text: str
) -> None:
    harness.deps.config.policy.gate_commands = gate_requires_done()
    use_messages(
        harness,
        ScriptStep(
            result=result(final_text="## Changes\n- allowed.txt: first attempt"),
            side_effect=broken,
        ),
        ScriptStep(result=result(final_text=repair_text), side_effect=write_allowed),
    )
    harness.run()
    commit_body, pr_text = delivered_changes(harness)
    assert changes_block(pr_text) == "- allowed.txt: first attempt"
    assert "**Changes**\n- allowed.txt: first attempt" in commit_body


def test_repaired_run_prefers_a_later_changes_section(harness: Harness) -> None:
    harness.deps.config.policy.gate_commands = gate_requires_done()
    use_messages(
        harness,
        ScriptStep(
            result=result(final_text="## Changes\n- allowed.txt: first attempt"),
            side_effect=broken,
        ),
        ScriptStep(
            result=result(final_text="## Changes\n- allowed.txt: repaired value"),
            side_effect=write_allowed,
        ),
    )
    harness.run()
    _, pr_text = delivered_changes(harness)
    assert changes_block(pr_text) == "- allowed.txt: repaired value"


def test_delivery_prompt_uses_prepared_identity_over_scope(harness: Harness) -> None:
    harness.github.scope = "Worktree: /placeholder/worktree. Branch: bug/placeholder.\n\n" + SCOPE
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    request = harness.adapter.requests[0]
    path = worktree_path(harness.deps.worktrees_root, harness.github.ticket).resolve()
    branch = branch_name(harness.github.ticket)
    assert request.cwd == path
    assert request.prompt.startswith(
        f"Your working directory is {path} on branch {branch}. Work only there. "
        "If the scope below names a different worktree path or branch, treat that as a "
        "placeholder and use this directory and branch.\n\n"
    )
    assert request.prompt.endswith(harness.github.scope)
