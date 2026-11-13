"""CI monitoring and repair against local Git repositories, scripted providers and a fake clock."""

import asyncio
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from fugue.agents import (
    AdapterRegistry,
    AgentRequest,
    AgentResult,
    ScriptedAdapter,
    ScriptStep,
    UsageRecord,
)
from fugue.artifacts import ArtifactStore
from fugue.config import FugueConfig
from fugue.config.schema import Limits
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.ci import (
    FailureKind,
    ci_repair_prompt,
    classify_failure,
    summarize_checks,
)
from fugue.engine.facts import branch_name, worktree_path
from fugue.github import CheckResult, GitHubError, PullRequest
from fugue.gitops import Git
from fugue.models import (
    AuthenticationMode,
    Component,
    IssueType,
    Priority,
    Size,
    Status,
    Ticket,
)
from fugue.process import run_command
from fugue.store import Store
from fugue.workflow import RunState

SCOPE = "You may touch only these files:\n- `allowed.txt`\n\nImplement the change."
LINK = "https://github.com/owner/repo/actions/runs/123/job/1"
OTHER_LINK = "https://github.com/owner/repo/actions/runs/456/job/2"
SAME_RUN_LINK = "https://github.com/owner/repo/actions/runs/123/job/9"
RUN_ID = "123"


def check(bucket: str, link: str = LINK, name: str = "test") -> CheckResult:
    return CheckResult(name, bucket, link)


class FakeCiGitHub:
    def __init__(self, ticket: Ticket) -> None:
        self.ticket = ticket
        self.repo = "owner/repo"
        self.pr: PullRequest | None = None
        self.script: list[list[CheckResult]] = [[check("pass")]]
        self.polls = 0
        self.logs: dict[str, str] = {}
        self.log_error: Exception | None = None
        self.reruns: list[str] = []
        self.rerun_error: Exception | None = None

    async def get_ticket(self, number: int) -> Ticket:
        return self.ticket

    async def set_status(self, number: int, status: Status) -> None:
        self.ticket = self.ticket.model_copy(update={"status": status})

    async def comment(self, number: int, body: str) -> None:
        return None

    async def download_attachment(self, url: str, dest: Path) -> Path:
        dest.write_text(SCOPE)
        return dest

    async def find_pr(self, branch: str) -> PullRequest | None:
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
        self.pr = PullRequest(71, "https://example.test/pr/71")
        return self.pr

    async def checks(self, pr: PullRequest) -> list[CheckResult]:
        index = min(self.polls, len(self.script) - 1)
        self.polls += 1
        return self.script[index]

    async def failed_log_tail(self, run_link: str, max_lines: int = 200) -> str:
        if self.log_error is not None:
            raise self.log_error
        return self.logs.get(run_link, "")

    async def rerun_failed(self, run_link: str) -> None:
        if self.rerun_error is not None:
            raise self.rerun_error
        self.reruns.append(run_link)


class FakeClock:
    """Sleeping advances time without waiting."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.sleeps: list[float] = []
        self.fail_on_sleep: Exception | None = None

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if self.fail_on_sleep is not None:
            error, self.fail_on_sleep = self.fail_on_sleep, None
            raise error
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def result() -> AgentResult:
    return AgentResult(
        ok=True,
        final_text="Implemented",
        structured_output=None,
        session_id="session-1",
        usage=UsageRecord(10, None, 4, None, None, 20),
        provider="codex",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=None,
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=None,
    )


def write_done(request: AgentRequest) -> None:
    (request.cwd / "allowed.txt").write_text("done")


def write_fixed(request: AgentRequest) -> None:
    (request.cwd / "allowed.txt").write_text("fixed")


def implement_then_repair() -> ScriptedAdapter:
    return ScriptedAdapter(
        [
            ScriptStep(result=result(), side_effect=write_done),
            ScriptStep(result=result(), side_effect=write_fixed),
        ],
        name="codex",
    )


async def git_command(path: Path, *args: str) -> str:
    output = await run_command(["git", *args], cwd=path)
    assert output.returncode == 0, output.stderr
    return output.stdout.strip()


@dataclass
class Harness:
    deps: EngineDeps
    github: FakeCiGitHub
    clock: FakeClock
    origin: Path

    def run(self) -> RunOutcome:
        return asyncio.run(Engine(self.deps).run_to_green(38))

    def use(self, adapter: ScriptedAdapter) -> None:
        self.deps.registry.register("codex", lambda config: adapter)

    def operation_status(self, run_id: int, key: str = RUN_ID) -> str:
        return self.deps.store.begin_operation(run_id, "ci_rerun", key, {}).status


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    async def setup() -> tuple[Path, Path]:
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
        return origin, clone

    origin, clone = asyncio.run(setup())
    ticket = Ticket(
        number=38,
        title="Add CI repair",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        milestone="0.1.0",
        assignees=["developer"],
        components=[Component.SOURCE_CODE],
        body_sections={
            "Summary": "Repair CI failures",
            "Scope": "Engine",
            "Solution": "[38-solution-1.md](https://github.com/user-attachments/files/scope)",
        },
    )
    github = FakeCiGitHub(ticket)
    clock = FakeClock()
    registry = AdapterRegistry()
    adapter = ScriptedAdapter([ScriptStep(result=result(), side_effect=write_done)], name="codex")
    registry.register("codex", lambda config: adapter)
    config = FugueConfig()
    config.policy.gate_commands = [[sys.executable, "-c", "print('gate passed')"]]
    config.limits.ci_poll_interval_seconds = 20
    config.limits.ci_timeout_seconds = 3600
    with Store(":memory:") as store:
        deps = EngineDeps(
            config,
            store,
            ArtifactStore(tmp_path / "artifacts"),
            github,
            registry,
            clone,
            tmp_path / "worktrees",
            tmp_path / "state",
            clock=clock,
            sleep=clock.sleep,
        )
        yield Harness(deps, github, clock, origin)


def worktree(harness: Harness) -> Path:
    return worktree_path(harness.deps.worktrees_root, harness.github.ticket)


# Scenario 1
def test_green_path(harness: Harness) -> None:
    harness.github.script = [[check("pass"), check("skipping", OTHER_LINK, "docs")]]
    outcome = harness.run()
    assert outcome.ci_green
    assert outcome.state == RunState.RUNNING
    assert outcome.pr_number == 71
    assert harness.clock.sleeps == []
    assert harness.github.reruns == []
    run = harness.deps.store.list_runs(38)[0]
    assert run.state == RunState.RUNNING
    branch = branch_name(harness.github.ticket)
    assert asyncio.run(Git(harness.origin).head_sha(branch)) == outcome.head_sha


# Scenario 2
def test_pending_then_green(harness: Harness) -> None:
    harness.github.script = [
        [check("pending")],
        [check("queued"), check("pass", OTHER_LINK, "lint")],
        [check("pass"), check("pass", OTHER_LINK, "lint")],
    ]
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.github.polls == 3
    assert harness.clock.sleeps == [20, 20]


# Scenario 2
def test_no_checks_yet_keeps_polling(harness: Harness) -> None:
    harness.github.script = [[], [check("pass")]]
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.clock.sleeps == [20]


# Scenario 3
def test_infrastructure_failure_is_rerun_once_then_green(harness: Harness) -> None:
    harness.github.script = [[check("fail")], [check("pending")], [check("pass")]]
    harness.github.logs[LINK] = "step 4\nThe Runner has received a shutdown signal\n"
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.github.reruns == [LINK]
    assert harness.operation_status(outcome.run_id) == "done"
    assert harness.clock.sleeps == [20, 20]
    # Only the first implementation ran; no repair was needed.
    assert harness.deps.store.usage_summary(outcome.run_id)["input_tokens"] == 10


# Scenario 4
def test_second_infrastructure_failure_is_repaired(harness: Harness) -> None:
    adapter = implement_then_repair()
    harness.use(adapter)
    harness.github.script = [[check("fail")], [check("fail")], [check("pass")]]
    harness.github.logs[LINK] = "ECONNRESET while fetching"
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.github.reruns == [LINK]
    assert len(adapter.requests) == 2
    assert adapter.requests[1].session_id == "session-1"
    assert "ECONNRESET while fetching" in adapter.requests[1].prompt
    path = worktree(harness)
    assert (path / "allowed.txt").read_text() == "fixed"
    assert asyncio.run(git_command(path, "rev-list", "--count", "develop..HEAD")) == "2"
    branch = branch_name(harness.github.ticket)
    assert asyncio.run(Git(harness.origin).head_sha(branch)) == outcome.head_sha
    assert outcome.head_sha == asyncio.run(Git(path).head_sha())
    artifacts = harness.deps.store.list_artifacts(outcome.run_id)
    failures = [a for a in artifacts if a.name.startswith("ci-failure-")]
    assert len(failures) == 1
    assert "ECONNRESET while fetching" in Path(failures[0].path).read_text()


# Scenario 4
def test_substantive_failure_is_repaired_without_rerun(harness: Harness) -> None:
    adapter = implement_then_repair()
    harness.use(adapter)
    harness.github.script = [[check("fail")], [check("pass")]]
    harness.github.logs[LINK] = "FAILED tests/test_a.py::test_x - AssertionError"
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.github.reruns == []
    assert "AssertionError" in adapter.requests[1].prompt
    assert "No git commit, push or gh" in adapter.requests[1].prompt
    # The earlier commit is kept and a new one added on top of it.
    path = worktree(harness)
    parents = asyncio.run(git_command(path, "rev-list", "--parents", "-n", "1", "HEAD")).split()
    assert len(parents) == 2
    assert asyncio.run(git_command(path, "log", "-1", "--format=%s")) == (
        "Feature[#38]: Add CI repair"
    )
    assert harness.clock.sleeps == [20]


# Scenario 4
def test_repair_gate_failure_does_not_commit(harness: Harness) -> None:
    harness.deps.config.policy.gate_commands = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('allowed.txt').read_text() == 'done'",
        ]
    ]
    harness.use(implement_then_repair())
    harness.deps.config.limits.max_repair_attempts = 1
    harness.github.script = [[check("fail")]]
    harness.github.logs[LINK] = "AssertionError"
    outcome = harness.run()
    # The CI repair broke the gate and the single repair attempt is spent.
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems
    commits = asyncio.run(git_command(worktree(harness), "rev-list", "--count", "develop..HEAD"))
    assert commits == "1"


# Scenario 5
def test_repair_limit_blocks(harness: Harness) -> None:
    harness.deps.config.limits.max_repair_attempts = 1
    adapter = implement_then_repair()
    harness.use(adapter)
    harness.github.script = [[check("fail")]]
    harness.github.logs[LINK] = "AssertionError: boom"
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("CI repair limit reached",)
    assert len(adapter.requests) == 2
    assert not outcome.ci_green
    assert harness.deps.store.list_runs(38)[0].state == RunState.BLOCKED
    commits = asyncio.run(git_command(worktree(harness), "rev-list", "--count", "develop..HEAD"))
    assert commits == "2"


# Scenario 6
def test_timeout_blocks(harness: Harness) -> None:
    harness.deps.config.limits.ci_timeout_seconds = 100
    harness.deps.config.limits.ci_poll_interval_seconds = 30
    harness.github.script = [[check("pending")]]
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("CI did not finish within 100 seconds",)
    assert harness.clock.sleeps == [30, 30, 30, 10]
    assert harness.deps.store.list_runs(38)[0].state == RunState.BLOCKED


# Scenario 6
def test_unreadable_failure_log_blocks(harness: Harness) -> None:
    harness.github.script = [[check("fail", "https://example.test/status")]]
    harness.github.log_error = ValueError("Expected an Actions run link")
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "Cannot read the failure log for test" in outcome.problems[0]
    assert harness.github.reruns == []


# Scenario 7
def test_restart_mid_poll_reuses_operations(harness: Harness) -> None:
    adapter = implement_then_repair()
    harness.use(adapter)
    harness.github.script = [[check("fail")], [check("fail")], [check("pass")]]
    harness.github.logs[LINK] = "Runner lost"
    harness.clock.fail_on_sleep = RuntimeError("stop mid poll")
    with pytest.raises(RuntimeError, match="stop mid poll"):
        harness.run()
    run = harness.deps.store.list_runs(38)[0]
    assert harness.github.reruns == [LINK]
    assert harness.operation_status(run.id) == "done"

    outcome = harness.run()
    assert outcome.run_id == run.id
    assert outcome.ci_green
    # The recorded rerun is not repeated; the second failure is repaired.
    assert harness.github.reruns == [LINK]
    assert len(adapter.requests) == 2
    assert len(harness.deps.store.list_runs(38)) == 1


# Scenario 7
def test_distinct_run_links_are_rerun_separately(harness: Harness) -> None:
    harness.github.script = [
        [check("fail"), check("cancel", OTHER_LINK, "lint"), check("fail", LINK, "unit")],
        [check("pass")],
    ]
    harness.github.logs[LINK] = "503 Service Unavailable"
    harness.github.logs[OTHER_LINK] = "no space left on device"
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.github.reruns == [LINK, OTHER_LINK]


def test_failed_jobs_of_one_run_are_rerun_once(harness: Harness) -> None:
    harness.github.script = [
        [check("fail", LINK, "unit"), check("fail", SAME_RUN_LINK, "lint")],
        [check("pass")],
    ]
    harness.github.logs[LINK] = "503 Service Unavailable"
    harness.github.logs[SAME_RUN_LINK] = "no space left on device"
    outcome = harness.run()
    assert outcome.ci_green
    assert len(harness.github.reruns) == 1
    assert harness.operation_status(outcome.run_id) == "done"


def test_rerun_run_failing_again_with_new_job_is_repaired(harness: Harness) -> None:
    adapter = implement_then_repair()
    harness.use(adapter)
    harness.github.script = [[check("fail")], [check("fail", SAME_RUN_LINK)], [check("pass")]]
    harness.github.logs[LINK] = "Runner lost"
    harness.github.logs[SAME_RUN_LINK] = "Runner lost"
    outcome = harness.run()
    assert outcome.ci_green
    assert harness.github.reruns == [LINK]
    assert len(adapter.requests) == 2
    assert "Runner lost" in adapter.requests[1].prompt


def test_rerun_failure_blocks_without_raising(harness: Harness) -> None:
    harness.github.script = [[check("fail")]]
    harness.github.logs[LINK] = "Runner lost"
    harness.github.rerun_error = GitHubError(["run", "rerun"], "run already in progress")
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "run already in progress" in outcome.problems[0]
    assert harness.github.reruns == []
    assert harness.deps.store.list_runs(38)[0].state == RunState.BLOCKED


def test_port_without_ci_methods_is_rejected(harness: Harness) -> None:
    class Minimal:
        repo = "owner/repo"

    harness.deps.github = Minimal()  # type: ignore[assignment]
    with pytest.raises(TypeError, match="does not support checks"):
        harness.run()


def test_summarize_checks_buckets() -> None:
    summary = summarize_checks(
        [
            check("pass"),
            check("skipping"),
            check("fail"),
            check("cancel"),
            check("pending"),
            check("queued"),
            check("surprise"),
        ]
    )
    assert [c.bucket for c in summary.green] == ["pass", "skipping"]
    assert [c.bucket for c in summary.failed] == ["fail", "cancel"]
    assert [c.bucket for c in summary.pending] == ["pending", "queued", "surprise"]
    assert not summary.is_green


def test_summary_is_green_requires_checks() -> None:
    assert not summarize_checks([]).is_green
    assert summarize_checks([check("pass"), check("skipping")]).is_green
    assert not summarize_checks([check("pass"), check("pending")]).is_green
    assert not summarize_checks([check("pass"), check("fail")]).is_green


def test_classify_failure() -> None:
    markers = Limits().infrastructure_markers
    assert classify_failure("Error: Runner Lost contact", markers) == FailureKind.INFRASTRUCTURE
    assert classify_failure("assert 1 == 2", markers) == FailureKind.SUBSTANTIVE
    assert classify_failure("", markers) == FailureKind.SUBSTANTIVE
    assert classify_failure("anything", []) == FailureKind.SUBSTANTIVE
    assert classify_failure("anything", [" "]) == FailureKind.SUBSTANTIVE


def test_ci_repair_prompt_lists_every_check() -> None:
    prompt = ci_repair_prompt([("unit", "boom"), ("lint", "bad style")])
    assert "Check: unit\nboom" in prompt
    assert "Check: lint\nbad style" in prompt
    assert "No git commit, push or gh" in prompt


def test_limits_defaults_and_validation() -> None:
    limits = Limits()
    assert limits.ci_poll_interval_seconds == 20
    assert limits.ci_timeout_seconds == 3600
    assert "runner lost" in limits.infrastructure_markers
    assert Limits(infrastructure_markers=[" ECONNRESET "]).infrastructure_markers == ["econnreset"]
    with pytest.raises(ValidationError):
        Limits(ci_poll_interval_seconds=0)
    with pytest.raises(ValidationError):
        Limits(ci_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Limits(infrastructure_markers=["ok", " "])
