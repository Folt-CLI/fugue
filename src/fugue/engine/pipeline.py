"""Execute the ticket-to-pull-request slice by observing live state and deciding."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import partial
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol, TypedDict, runtime_checkable
from urllib.parse import quote

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentRequest,
    ModelChoice,
    ProviderAdapter,
    ProviderSwitch,
    resolve_models,
)
from fugue.analysis.render import render_scope_text
from fugue.analysis.schema import ScopingResult, scoping_schema
from fugue.analysis.scoping import resize_comment, scoping_prompt
from fugue.artifacts import ArtifactConflict, ArtifactRef, ArtifactStore
from fugue.config import FugueConfig, ProviderEntry, RoleConfig
from fugue.engine.ci import (
    FailureKind,
    actions_run_key,
    ci_repair_prompt,
    classify_failure,
    read_checks,
    summarize_checks,
    without_ignored,
)
from fugue.engine.closure import close_comment, close_step, gate_passed, review_clean
from fugue.engine.decision import (
    Block,
    Commit,
    Done,
    Implement,
    NextAction,
    OpenPr,
    Prepare,
    Push,
    Snapshot,
    Verify,
    decide,
)
from fugue.engine.facts import (
    allowed_files,
    branch_name,
    read_test_change_reason,
    scope_url,
    selected_solution,
    worktree_path,
)
from fugue.engine.locks import RepoLock
from fugue.engine.ports import (
    CheckRestartPort,
    CiGitHubPort,
    GitHubPort,
    IssueClosePort,
    MergeGitHubPort,
    ReviewGitHubPort,
    SizePort,
)
from fugue.engine.prompts import (
    commit_message,
    fallback_changes,
    implementation_prompt,
    parse_changes,
    pr_body,
    repair_prompt,
)
from fugue.engine.review import (
    Clean,
    Decision,
    Finding,
    RoundEntry,
    build_review,
    diff_ranges,
    marked_finding,
    parse_findings,
    review_marker,
    review_prompt,
    review_repair_prompt,
    validate_decisions,
)
from fugue.gate import GateResult, format_failure, run_gate
from fugue.github import CheckResult, GitHubError, PullRequest
from fugue.github.client import HeadChanged, ResolvePermissionError
from fugue.gitops import (
    CommitMessage,
    Git,
    GitError,
    protected_paths,
    validate_message,
    validate_staging,
)
from fugue.models import AnalysisDepth, Disposition, IssueType, Role, Size, Status, Ticket
from fugue.process import run_command
from fugue.store import Approval, Operation, Run, Store
from fugue.store.sizes import RESIZE
from fugue.workflow import RunState, Stage, ready_for_implementation
from fugue.workflow.guards import can_close, merge_allowed, review_required

# Polls with no checks at all before a pull request is reopened to start its workflows.
EMPTY_POLLS_BEFORE_RESTART = 3


class GateRunner(Protocol):
    def __call__(
        self, commands: Sequence[Sequence[str]], cwd: Path, *, log_dir: Path
    ) -> Awaitable[GateResult]: ...


@runtime_checkable
class _BranchCleanupGitHubPort(Protocol):
    """Paginated reads needed to protect branches used by open pull requests."""

    async def _paginated(self, endpoint: str, *args: str) -> list[dict[str, Any]]: ...


@dataclass
class EngineDeps:
    config: FugueConfig
    store: Store
    artifacts: ArtifactStore
    github: GitHubPort
    registry: AdapterRegistry
    repo_root: Path
    worktrees_root: Path
    state_dir: Path
    base_branch: str = "develop"
    gate_runner: GateRunner = run_gate
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    progress: Callable[[str], None] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    repo_lock: RepoLock | None = None
    code_root: Path | None = None

    @property
    def effective_code_root(self) -> Path:
        return self.code_root if self.code_root is not None else self.repo_root


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    state: RunState
    pr_number: int | None = None
    head_sha: str | None = None
    problems: tuple[str, ...] = ()
    ci_green: bool = False
    findings: tuple[Finding, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Identity:
    """Branch and worktree path, fixed when the worktree is first prepared."""

    branch: str
    path: Path


_RESIZE_SIGNALS = ("Repair limit reached", "CI repair limit reached")


_AGENT_TEST_CHANGE_RE = re.compile(r"^\s*Test-change:\s*(\S.*?)\s*$", re.MULTILINE)


class _Stop(Exception):
    def __init__(self, state: RunState, problems: Sequence[str]) -> None:
        self.state = state
        self.problems = tuple(problems)
        super().__init__("; ".join(problems))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _implemented_sha256(store: Store, run_id: int) -> str | None:
    # The latest finished implement attempt records the scope digest it implemented.
    completed = [
        attempt
        for attempt in store.stage_attempts(run_id, Stage.IMPLEMENT)
        if attempt.status == "completed"
    ]
    return completed[-1].detail if completed else None


def _repairs_used(store: Store, run_id: int) -> int:
    # A new implement attempt (a replaced scope) starts a fresh repair budget.
    implements = store.stage_attempts(run_id, Stage.IMPLEMENT)
    latest_implement = implements[-1].id if implements else 0
    return sum(
        1
        for attempt in store.stage_attempts(run_id, Stage.REPAIR)
        if attempt.status == "completed" and attempt.id > latest_implement
    )


def _session_id(store: Store, run_id: int, provider: str, authentication_mode: str) -> str | None:
    sessions = store.agent_sessions(
        run_id, role=Role.IMPLEMENTER, provider=provider, authentication_mode=authentication_mode
    )
    return sessions[-1].session_id if sessions else None


def role_adapter(
    deps: EngineDeps, run_id: int, role: Role, role_config: RoleConfig
) -> ProviderAdapter:
    """Build a role's adapter; each provider switch is recorded and reported."""

    def on_switch(switch: ProviderSwitch) -> None:
        number = len(_operations(deps.store, run_id, "provider_switch")) + 1
        key = f"{number}:{role}:{switch.from_provider}->{switch.to_provider}:{switch.reason}"
        operation = deps.store.begin_operation(
            run_id,
            "provider_switch",
            key,
            {
                "role": str(role),
                "from": switch.from_provider,
                "to": switch.to_provider,
                "reason": switch.reason,
                "authentication_mode": str(switch.authentication_mode),
            },
        )
        deps.store.complete_operation(operation.id, {"done": True})
        if deps.progress is not None:
            deps.progress(f"{role}: {switch.describe()}")

    return deps.registry.build_for_role(
        role_config,
        clock=deps.clock,
        cooldown_seconds=deps.config.limits.provider_cooldown_seconds,
        on_switch=on_switch,
    )


def model_choice(deps: EngineDeps, run_id: int, role: Role, size: Size) -> ModelChoice:
    """Return the run's model choice for a role, recording it on first use.

    The recorded choice is reused after a restart, so a run keeps the tier it started with
    even when the ticket Size or the configuration changed in between.
    """
    operation = deps.store.begin_operation(
        run_id, "model_choice", str(role), {"role": str(role), "size": str(size)}
    )
    if operation.result_json is not None:
        return ModelChoice.from_json(role, json.loads(operation.result_json))
    choice = resolve_models(deps.config, role, size)
    deps.store.complete_operation(operation.id, choice.to_json())
    if deps.progress is not None:
        tier = "default" if choice.tier is None else str(choice.tier)
        deps.progress(f"{role}: tier {tier} ({choice.source})")
    return choice


class UsageChoice(TypedDict):
    model: str | None
    tier: str | None


def usage_choice(choice: ModelChoice | None, provider: str) -> UsageChoice:
    """The model and tier to store on a usage record for the provider that served the call."""
    if choice is None:
        return {"model": None, "tier": None}
    tier = None if choice.tier is None else str(choice.tier)
    return {"model": choice.models.get(provider), "tier": tier}


def saved_model_choice(store: Store, run_id: int, role: Role) -> ModelChoice | None:
    """The recorded model choice, or None for a run that never recorded one."""
    saved = _operation_by_key(store, run_id, "model_choice", str(role))
    if saved is None or saved[4] is None:
        return None
    return ModelChoice.from_json(role, json.loads(saved[4]))


async def _common_dir(path: Path) -> Path:
    return (path / (await Git(path)._run("rev-parse", "--git-common-dir")).strip()).resolve()


async def _is_worktree(deps: EngineDeps, identity: Identity) -> bool:
    if not identity.path.exists():
        return False
    try:
        if await _common_dir(identity.path) != await _common_dir(deps.repo_root):
            return False
        return await Git(identity.path).current_branch() == identity.branch
    except GitError:
        return False


async def observe(
    deps: EngineDeps, run_id: int, ticket: Ticket, scope: str, identity: Identity | None
) -> Snapshot:
    """Read the live state of the worktree, remote and pull request without side effects."""
    snapshot = Snapshot(
        allowed=tuple(allowed_files(scope)),
        worktree=False,
        head_sha=None,
        ahead=(),
        dirty=(),
        changed=(),
        remote_head_sha=None,
        pr=None,
        status=ticket.status,
        scope_sha256=_sha256(scope),
        implemented_sha256=_implemented_sha256(deps.store, run_id),
    )
    if identity is None:
        return snapshot
    pr = await deps.github.find_pr(identity.branch)
    if not await _is_worktree(deps, identity):
        return replace(snapshot, pr=pr)
    git = Git(identity.path)
    head = await git.head_sha()
    base = _base_ref(deps, run_id)
    merge_base = (await git._run("merge-base", base, head)).strip()
    ahead = (await git._run("log", "--format=%s", f"{merge_base}..{head}")).splitlines()
    changed = await git._run("diff", "--name-only", "--no-renames", "-z", merge_base, head, "--")
    remote = await git._run("ls-remote", "--heads", "origin", f"refs/heads/{identity.branch}")
    return Snapshot(
        allowed=snapshot.allowed,
        worktree=True,
        head_sha=head,
        ahead=tuple(ahead),
        dirty=tuple(entry[3:] for entry in await git.status_porcelain()),
        changed=tuple(changed.removesuffix("\0").split("\0")) if changed else (),
        remote_head_sha=remote.split()[0] if remote.strip() else None,
        pr=pr,
        status=ticket.status,
        scope_sha256=snapshot.scope_sha256,
        implemented_sha256=snapshot.implemented_sha256,
    )


def _ticket_base(deps: EngineDeps, run_id: int) -> str:
    saved = _operation_by_key(deps.store, run_id, "base", "base")
    if saved is not None and saved[4] is not None:
        return str(json.loads(saved[4])["branch"])
    return deps.base_branch


def feature_parent(store: Store, run_id: int) -> int | None:
    """The parent whose feature branch this run delivers to or delivers itself, if any."""
    for kind, key in (("base", "base"), ("feature", "feature")):
        saved = _operation_by_key(store, run_id, kind, key)
        if saved is not None and saved[4] is not None:
            parent = json.loads(saved[4]).get("parent")
            if parent is not None:
                return int(parent)
    return None


def is_feature_run(store: Store, run_id: int) -> bool:
    """True for the run that delivers a parent's feature branch to the base branch."""
    saved = _operation_by_key(store, run_id, "feature", "feature")
    return saved is not None and saved[4] is not None


def _base_ref(deps: EngineDeps, run_id: int) -> str:
    base = _ticket_base(deps, run_id)
    prefix = "refs/heads" if base == deps.base_branch else "refs/remotes/origin"
    return f"{prefix}/{base}"


class Engine:
    def __init__(self, deps: EngineDeps) -> None:
        self.deps = deps
        self._attempt_id: ContextVar[int | None] = ContextVar("engine_attempt_id", default=None)

    def _artifact(self, run_id: int, kind: str, text: str, suffix: str = ".txt") -> ArtifactRef:
        attempt_id = self._attempt_id.get()
        if attempt_id is None:
            raise RuntimeError("Artifacts require an active stage attempt")
        for counter in count():
            try:
                ref = self.deps.artifacts.write(
                    run_id, f"{kind}-{attempt_id}-{counter}{suffix}", text
                )
                break
            except ArtifactConflict:
                continue
        self.deps.store.register_artifact(
            run_id, ref.name, str(ref.path), ref.sha256, ref.size_bytes
        )
        return ref

    async def _stage[T](
        self,
        run_id: int,
        stage: Stage,
        action: Callable[[], Awaitable[T]],
        detail: str | None = None,
    ) -> T:
        attempt = self.deps.store.begin_attempt(run_id, stage)
        self._report(f"{stage}: started")
        token = self._attempt_id.set(attempt.id)
        try:
            result = await action()
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                status = "interrupted"
            elif isinstance(error, _Stop):
                status = error.state
            else:
                status = "failed"
            self.deps.store.finish_attempt(attempt.id, status, str(error))
            self._report(f"{stage}: {status}")
            raise
        finally:
            self._attempt_id.reset(token)
        if isinstance(result, GateResult) and not result.passed:
            self.deps.store.finish_attempt(attempt.id, "failed", format_failure(result))
            self._report(f"{stage}: failed")
        else:
            self.deps.store.finish_attempt(attempt.id, "completed", detail)
            self._report(f"{stage}: completed")
        return result

    def _report(self, message: str) -> None:
        if self.deps.progress is not None:
            self.deps.progress(message)

    async def _operation(
        self, run_id: int, kind: str, intent: dict[str, Any], action: Callable[[], Awaitable[Any]]
    ) -> None:
        key = str(self._attempt_id.get())
        operation = self.deps.store.begin_operation(run_id, kind, key, intent)
        await action()
        self.deps.store.complete_operation(operation.id, {"done": True})

    def _identity_operation(self, run_id: int, intent: dict[str, Any]) -> Operation:
        return self.deps.store.begin_operation(run_id, "identity", "identity", intent)

    def _repo_root(self) -> str:
        return str(self.deps.repo_root.resolve())

    def _find_or_create_run(self, ticket_number: int) -> Run:
        store = self.deps.store
        repo = self.deps.github.repo.strip()
        for candidate in reversed(store.list_runs(ticket_number)):
            if candidate.state in (RunState.COMPLETED, RunState.FAILED):
                continue
            if candidate.repo.casefold() != repo.casefold():
                continue
            operation = self._identity_operation(candidate.id, {})
            if json.loads(operation.intent_json).get("repo_root") == self._repo_root():
                return candidate
        run = store.create_run(ticket_number, repo)
        self._identity_operation(run.id, {"repo_root": self._repo_root()})
        return run

    def _saved_identity(self, run_id: int) -> Identity | None:
        operation = self._identity_operation(run_id, {})
        if operation.result_json is None:
            return None
        saved = json.loads(operation.result_json)
        return Identity(saved["branch"], Path(saved["path"]))

    def _new_identity(self, run_id: int, ticket: Ticket, scope: str) -> Identity:
        # Validate before persisting so an invalid title leaves nothing behind.
        self._message(ticket, scope)
        identity = Identity(
            branch_name(ticket),
            worktree_path(self.deps.worktrees_root, ticket).resolve(),
        )
        operation = self._identity_operation(run_id, {})
        self.deps.store.complete_operation(
            operation.id, {"branch": identity.branch, "path": str(identity.path)}
        )
        return identity

    def _changes(self, run_id: int, paths: Sequence[str]) -> list[str]:
        """Use the last qualifying message from the latest implement attempt and its repairs."""
        implements = self.deps.store.stage_attempts(run_id, Stage.IMPLEMENT)
        if not implements:
            return fallback_changes(paths)
        latest_implement_id = implements[-1].id
        gate = {" ".join(command) for command in self.deps.config.policy.gate_commands}
        messages: list[tuple[int, str]] = []
        for artifact in self.deps.store.list_artifacts(run_id):
            if not artifact.name.startswith("agent-") or artifact.name.startswith("agent-error-"):
                continue
            attempt_id = artifact.name.split("-", 2)[1]
            if not attempt_id.isdecimal():
                continue
            try:
                text = Path(artifact.path).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            messages.append((int(attempt_id), text))
        parsed: list[str] = []
        for message_attempt_id, text in messages:
            if message_attempt_id < latest_implement_id:
                continue
            found = parse_changes(text)
            if any("no files changed" in change.casefold() for change in found):
                continue
            found = [c for c in found if c.partition(":")[0].strip().strip("`") not in gate]
            if found:
                parsed = found
        return parsed or fallback_changes(paths)

    def _message(
        self, ticket: Ticket, scope: str, changes: Sequence[str] | None = None
    ) -> CommitMessage:
        if changes is None:
            changes = fallback_changes(allowed_files(scope))
        message = commit_message(ticket, ticket.body_sections.get("Summary", ""), changes)
        problems = validate_message(
            message, list(IssueType), self.deps.config.policy.commit_subject_max
        )
        if problems:
            raise _Stop(RunState.BLOCKED, problems)
        return message

    def _merge_port(self) -> MergeGitHubPort:
        port = self.deps.github
        if not isinstance(port, MergeGitHubPort):
            raise TypeError("The GitHub port does not support merge and closure")
        return port

    async def _merge_context(self, run_id: int) -> tuple[Ticket, Identity, PullRequest]:
        run = self.deps.store.get_run(run_id)
        if run.repo.casefold() != self.deps.github.repo.strip().casefold():
            raise _Stop(RunState.BLOCKED, ["Run belongs to another repository"])
        identity = self._saved_identity(run_id)
        if identity is None:
            raise _Stop(RunState.BLOCKED, ["No saved worktree for this run"])
        saved = _operations(self.deps.store, run_id, "merge_request")
        if saved:
            data = json.loads(saved[-1][3])
            pr = PullRequest(data["number"], data["url"])
        else:
            found = await self.deps.github.find_pr(identity.branch)
            if found is None:
                raise _Stop(RunState.BLOCKED, ["No pull request to merge"])
            pr = found
        return await self.deps.github.get_ticket(run.ticket_number), identity, pr

    async def _merge_guards(
        self,
        run_id: int,
        ticket: Ticket,
        identity: Identity,
        pr: PullRequest,
        *,
        require_approval: bool,
    ) -> tuple[str, Approval | None]:
        port = self._merge_port()
        head = await port.pr_head_sha(pr)
        subject = str(pr.number)
        self.deps.store.invalidate_approvals(run_id, subject, head)
        approval = self.deps.store.valid_approval(run_id, "merge", subject, head)
        problems = merge_allowed(
            gate_passed=gate_passed(self.deps.store, run_id, head),
            ci_green=summarize_checks(
                without_ignored(
                    await read_checks(partial(port.checks, pr)),
                    self.deps.config.limits.ignored_checks,
                )
            ).is_green,
            review_clean_or_not_required=(
                # A feature pull request needs its own review unless `policy.feature_review`
                # is false; a ticket follows its Size rule.
                not review_required(
                    ticket.size,
                    self.deps.config.policy,
                    feature=is_feature_run(self.deps.store, run_id),
                )
                or review_clean(self.deps.store, run_id, head)
            ),
            approval_head_sha=(approval.head_sha if approval else None)
            if require_approval
            else head,
            current_head_sha=head,
            unresolved_threads=await port.unresolved_thread_count(pr),
        )
        if await Git(identity.path).head_sha() != head:
            problems.append("The worktree head must match the current PR head")
        if await Git(identity.path).status_porcelain():
            problems.append("The worktree must be clean")
        if problems:
            raise _Stop(RunState.BLOCKED, problems)
        return head, approval

    async def request_approval(self, run_id: int) -> RunOutcome:
        """Check delivery guards and wait for an explicit exact-head approval."""

        async def work() -> RunOutcome:
            ticket, identity, pr = await self._merge_context(run_id)
            head = await self._merge_port().pr_head_sha(pr)
            operation = self.deps.store.begin_operation(run_id, "merge_gate", head, {"head": head})
            self.deps.store.fail_operation(operation.id, "Gate verification in progress")
            result = await self._stage(
                run_id, Stage.VERIFY, lambda: self._gate(run_id, identity.path)
            )
            if not result.passed:
                raise _Stop(RunState.BLOCKED, ["The gate must pass"])
            if await Git(identity.path).head_sha() != head:
                raise _Stop(RunState.BLOCKED, ["The worktree head must match the current PR head"])
            self.deps.store.complete_operation(operation.id, {"passed": True})
            head, _ = await self._merge_guards(run_id, ticket, identity, pr, require_approval=False)
            request = self.deps.store.begin_operation(
                run_id,
                "merge_request",
                head,
                {"number": pr.number, "url": pr.url, "head": head},
            )
            self.deps.store.complete_operation(request.id, {"ready": True})
            self.deps.store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
            return RunOutcome(
                run_id, RunState.WAITING_FOR_MERGE_APPROVAL, pr.number, head, ci_green=True
            )

        return await self._guarded(run_id, work)

    def approve(self, run_id: int, decided_by: str, reason: str) -> Approval:
        """Record a live-guarded approval; call this synchronous API outside an event loop."""
        try:
            return asyncio.run(self.record_approval(run_id, decided_by, reason))
        except _Stop as stop:
            raise ValueError("; ".join(stop.problems)) from stop

    async def record_approval(self, run_id: int, decided_by: str, reason: str) -> Approval:
        """Record approval for the requested current head; a guard problem raises _Stop."""
        if not decided_by.strip() or not reason.strip():
            raise ValueError("Approval requires decided_by and reason")
        ticket, identity, pr = await self._merge_context(run_id)
        head, _ = await self._merge_guards(run_id, ticket, identity, pr, require_approval=False)
        requests = _operations(self.deps.store, run_id, "merge_request")
        if not requests or json.loads(requests[-1][3])["head"] != head:
            raise ValueError("Request approval for the current head first")
        return self.deps.store.record_approval(
            run_id, "merge", str(pr.number), head, "approved", decided_by, reason
        )

    async def merge_and_close(self, run_id: int) -> RunOutcome:
        parent = feature_parent(self.deps.store, run_id)
        if parent is not None:

            async def refuse() -> RunOutcome:
                raise _Stop(
                    RunState.BLOCKED,
                    [
                        f"Run {run_id} belongs to the feature branch of #{parent}; "
                        f"merge it with fugue run {parent}"
                    ],
                )

            return await self._guarded(run_id, refuse)
        return await self._merge_and_close_work(run_id)

    async def merge_approved(self, run_id: int, kind: str) -> tuple[PullRequest, str, str]:
        """Merge the approved head with a merge commit and confirm it; resumable by `kind`.

        Returns the pull request, the merged head and the merge commit. A pull request
        already merged at a recorded head is confirmed without merging again.
        """
        ticket, identity, pr = await self._merge_context(run_id)
        port = self._merge_port()
        store = self.deps.store
        state = await port.pr_state(pr)
        if state.state == "MERGED":
            operation = store.begin_operation(
                run_id, kind, state.head_sha, {"number": pr.number, "head": state.head_sha}
            )
        else:
            if state.state != "OPEN":
                raise _Stop(RunState.BLOCKED, [f"Pull request #{pr.number} is not open or merged"])
            head, _ = await self._merge_guards(run_id, ticket, identity, pr, require_approval=True)
            operation = store.begin_operation(
                run_id, kind, head, {"number": pr.number, "head": head}
            )
            try:
                await self._stage(run_id, Stage.MERGE, lambda: port.merge(pr, head, "merge"))
            except HeadChanged as error:
                raise _Stop(
                    RunState.BLOCKED, [f"{error}; the approval for {head} has lapsed"]
                ) from error
            started = self.deps.clock()
            while True:
                state = await port.pr_state(pr)
                if state.state == "MERGED":
                    break
                remaining = (
                    self.deps.config.limits.ci_timeout_seconds
                    - (self.deps.clock() - started).total_seconds()
                )
                if remaining <= 0 or state.state != "OPEN":
                    raise _Stop(RunState.BLOCKED, [f"Merge of #{pr.number} has not been confirmed"])
                await self.deps.sleep(
                    min(self.deps.config.limits.ci_poll_interval_seconds, remaining)
                )
        if not state.merge_sha:
            raise _Stop(RunState.BLOCKED, [f"Merge of #{pr.number} has no merge commit"])
        store.complete_operation(operation.id, {"merge_sha": state.merge_sha})
        return pr, state.head_sha, state.merge_sha

    async def _merge_and_close_work(self, run_id: int) -> RunOutcome:
        """Merge the approved head, verify MERGED, then finish durable closure steps."""

        async def work() -> RunOutcome:
            ticket, identity, pr = await self._merge_context(run_id)
            port = self._merge_port()
            store = self.deps.store
            state = await port.pr_state(pr)
            head = state.head_sha
            approval = store.valid_approval(run_id, "merge", str(pr.number), head)
            if state.state != "MERGED":
                if state.state != "OPEN":
                    raise _Stop(RunState.BLOCKED, ["Pull request is not open or merged"])
                head, approval = await self._merge_guards(
                    run_id, ticket, identity, pr, require_approval=True
                )
                operation = store.begin_operation(
                    run_id, "merge", head, {"number": pr.number, "head": head}
                )
                await self._stage(run_id, Stage.MERGE, lambda: port.merge(pr, head))
                started = self.deps.clock()
                while True:
                    state = await port.pr_state(pr)
                    if state.state == "MERGED":
                        break
                    remaining = (
                        self.deps.config.limits.ci_timeout_seconds
                        - (self.deps.clock() - started).total_seconds()
                    )
                    if remaining <= 0 or state.state != "OPEN":
                        raise _Stop(RunState.BLOCKED, ["Merge has not been confirmed"])
                    await self.deps.sleep(
                        min(self.deps.config.limits.ci_poll_interval_seconds, remaining)
                    )
                store.complete_operation(operation.id, {"merge_sha": state.merge_sha})
            if approval is None or state.head_sha != head or not state.merge_sha:
                raise _Stop(RunState.BLOCKED, ["Verified merge and matching approval required"])
            problems = can_close(ticket, await port.children(ticket.number), disposition_set=True)
            if problems:
                raise _Stop(RunState.BLOCKED, problems)

            assert approval is not None
            body = (
                f"Merged as {state.merge_sha}. Approval #{approval.id}: "
                f"{approval.decided_by} approved {head}. "
                f"Reason: {approval.reason}. Recorded: {approval.created_at.isoformat()}."
            )
            notes = await self._stage(
                run_id,
                Stage.CLOSE,
                lambda: self._close_ticket(run_id, ticket, identity, head, body),
            )
            store.set_run_state(run_id, RunState.COMPLETED)
            return RunOutcome(
                run_id, RunState.COMPLETED, pr.number, head, ci_green=True, notes=tuple(notes)
            )

        return await self._guarded(run_id, work)

    async def _mark_merged(
        self, run_id: int, number: int, pr: PullRequest, feature: str, merge_sha: str
    ) -> None:
        """Record a child as merged as soon as it joins the feature branch; each step resumes.

        Disposition and Status share their step names with `_close_ticket`, so the final
        closure repeats neither; its own comment still names the delivery to the base branch.
        """
        store = self.deps.store
        port = self._merge_port()
        await close_step(
            store, run_id, "disposition", lambda: port.set_disposition(number, Disposition.MERGED)
        )
        await close_step(store, run_id, "status", lambda: port.set_status(number, Status.DONE))
        await close_comment(
            store,
            run_id,
            "merged_comment",
            port,
            number,
            f"Merged into `{feature}` as {merge_sha} by pull request #{pr.number}. "
            "It reaches the base branch with the feature pull request.",
        )
        if isinstance(port, IssueClosePort):
            await close_step(store, run_id, "issue_closed", lambda: port.close_issue(number))

    async def _close_ticket(
        self, run_id: int, ticket: Ticket, identity: Identity, head: str, body: str
    ) -> list[str]:
        """Set Disposition and Status, comment once, then clean up; each step resumes."""
        store = self.deps.store
        port = self._merge_port()
        await close_step(
            store,
            run_id,
            "disposition",
            lambda: port.set_disposition(ticket.number, Disposition.MERGED),
        )
        await close_step(
            store, run_id, "status", lambda: port.set_status(ticket.number, Status.DONE)
        )
        await close_comment(store, run_id, "comment", port, ticket.number, body)
        return await self._clean_up(run_id, identity, head)

    async def _clean_up(self, run_id: int, identity: Identity, head: str) -> list[str]:
        """Remove the worktree, the merged local branch and the remote branch once."""
        store = self.deps.store
        port = self._merge_port()
        notes: list[str] = []

        async def remove() -> None:
            if identity.path.exists():
                await Git(self.deps.repo_root).remove_worktree(identity.path)

        await close_step(store, run_id, "worktree", remove)
        operation = store.begin_operation(
            run_id,
            "closure",
            "local_branch",
            {"branch": identity.branch, "merged_head": head},
        )
        warning: str | None = None
        if operation.status != "done":
            deleted = False
            reason: str | None = None
            try:
                if not isinstance(port, _BranchCleanupGitHubPort):
                    reason = "Cannot check open pull requests targeting the branch"
                elif await port._paginated(
                    f"repos/{port.repo}/pulls?state=open"
                    f"&base={quote(identity.branch, safe='')}&per_page=100"
                ):
                    reason = "Branch is the base of another open pull request"
                else:
                    deleted, reason = await Git(self.deps.repo_root).delete_local_branch(
                        identity.branch, merged_head=head
                    )
            except (GitError, GitHubError, OSError) as error:
                reason = str(error)
            if not deleted and reason != "Branch is already absent":
                warning = f"Warning: local branch {identity.branch!r} kept: {reason}"
            store.complete_operation(
                operation.id, {"deleted": deleted, "reason": reason, "warning": warning}
            )
        elif operation.result_json is not None:
            warning = json.loads(operation.result_json).get("warning")
        if warning:
            notes.append(warning)
            self._report(warning)
        await close_step(
            store,
            run_id,
            "remote_branch",
            lambda: port.delete_remote_branch(identity.branch, self.deps.repo_root),
        )
        return notes

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        return await self._run(ticket_number, until_green=False)

    async def run_to_green(self, ticket_number: int) -> RunOutcome:
        """Reach a pull request, then wait for green checks, repairing CI failures."""
        self._ci_port()  # fail fast, before any work is done
        return await self._run(ticket_number, until_green=True)

    async def _run(self, ticket_number: int, *, until_green: bool) -> RunOutcome:
        store = self.deps.store
        run_id = self._find_or_create_run(ticket_number).id
        store.set_run_state(run_id, RunState.RUNNING)
        try:
            ticket = await self._stage(
                run_id, Stage.LOAD, lambda: self.deps.github.get_ticket(ticket_number)
            )
            self._choose_models(run_id, ticket)
            scope = await self._stage(
                run_id, Stage.VALIDATE, lambda: self._validate(run_id, ticket)
            )
            outcome = await self._drive(run_id, ticket, scope)
            if not until_green:
                return outcome
            return await self._stage(
                run_id, Stage.MONITOR, lambda: self._wait_for_green(run_id, ticket, scope)
            )
        except asyncio.CancelledError:
            store.set_run_state(run_id, RunState.INTERRUPTED)
            raise
        except _Stop as stop:
            return await self._stopped(run_id, stop)
        except Exception:
            store.set_run_state(run_id, RunState.BLOCKED)
            raise

    async def _stopped(self, run_id: int, stop: _Stop) -> RunOutcome:
        """The outcome of a stopped run; a scoped ticket that outgrew its scope is re-sized."""
        self.deps.store.set_run_state(run_id, stop.state)
        problems = stop.problems
        if stop.state == RunState.BLOCKED and any(
            signal in problem for problem in problems for signal in _RESIZE_SIGNALS
        ):
            note = await self._resize_scoped(run_id)
            if note is not None:
                problems = (*problems, note)
        return RunOutcome(run_id, stop.state, problems=problems)

    def _record_resize(self, run_id: int, ticket: Ticket, reason: str) -> None:
        """Record that the ticket was re-sized, so reports can count it."""
        operation = self.deps.store.begin_operation(
            run_id, RESIZE, "resize", {"from": str(ticket.size), "to": "M", "reason": reason}
        )
        self.deps.store.complete_operation(operation.id, {"done": True})

    async def _resize_scoped(self, run_id: int) -> str | None:
        """Re-size an S ticket to M after the run needed more than its scope allowed."""
        github = self.deps.github
        if not isinstance(github, SizePort):
            return None
        number = self.deps.store.get_run(run_id).ticket_number
        ticket = await github.get_ticket(number)
        if self.deps.config.policy.size_rules[ticket.size].analysis != AnalysisDepth.SCOPE:
            return None
        reason = "the run needed more than its scoped files allow or hit its repair limit"
        self._record_resize(run_id, ticket, reason)
        await github.set_size(number, Size.M)
        await github.comment(number, resize_comment(ticket, reason, by="its run"))
        return f"Re-sized to M: {reason}"

    def _choose_models(self, run_id: int, ticket: Ticket) -> None:
        for role in (Role.IMPLEMENTER, Role.REVIEWER):
            model_choice(self.deps, run_id, role, ticket.size)

    async def _drive(self, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        while True:
            ticket = await self.deps.github.get_ticket(ticket.number)
            identity = self._saved_identity(run_id)
            snapshot = await observe(self.deps, run_id, ticket, scope, identity)
            action = decide(snapshot)
            if isinstance(action, Done):
                self.deps.store.set_run_state(run_id, RunState.WAITING_FOR_CI)
                return RunOutcome(
                    run_id, RunState.WAITING_FOR_CI, action.pr.number, action.head_sha
                )
            if isinstance(action, Block):
                raise _Stop(RunState.BLOCKED, action.problems)
            await self._execute(action, run_id, ticket, scope, identity)

    async def _execute(
        self, action: NextAction, run_id: int, ticket: Ticket, scope: str, identity: Identity | None
    ) -> None:
        if isinstance(action, Prepare):
            await self._stage(
                run_id, Stage.PREPARE, lambda: self._prepare(run_id, ticket, scope, identity)
            )
            return
        assert identity is not None
        if isinstance(action, Implement):
            await self._stage(
                run_id,
                Stage.IMPLEMENT,
                lambda: self._implement(run_id, identity, scope),
                _sha256(scope),
            )
        elif isinstance(action, Verify):
            if await self._verify(run_id, identity, scope):
                await self._execute(Commit(), run_id, ticket, scope, identity)
        elif isinstance(action, Commit):
            await self._stage(
                run_id, Stage.DELIVER, lambda: self._commit(run_id, ticket, scope, identity)
            )
        elif isinstance(action, Push):
            await self._stage(run_id, Stage.DELIVER, lambda: self._push(run_id, identity))
        elif isinstance(action, OpenPr):
            await self._stage(
                run_id, Stage.DELIVER, lambda: self._open_pr(run_id, ticket, scope, identity)
            )

    async def _validate(self, run_id: int, ticket: Ticket) -> str:
        await self._base_branch(run_id, ticket)
        url = scope_url(ticket)
        problems: list[str] = []
        text: str | None = None
        if url is not None:
            try:
                self.deps.state_dir.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=self.deps.state_dir) as directory:
                    downloaded = await self.deps.github.download_attachment(
                        url, Path(directory) / "scope.md"
                    )
                    text = downloaded.read_text(encoding="utf-8")
            except (OSError, UnicodeError, RuntimeError, ValueError) as error:
                problems.append(f"Scope attachment could not be downloaded: {error}")
        problems.extend(
            ready_for_implementation(
                ticket,
                self.deps.config.policy,
                selected_solution=selected_solution(ticket),
            )
        )
        if problems:
            raise _Stop(RunState.BLOCKED, problems)
        if text is None and self._scoping(ticket):
            text = await self._stage(
                run_id, Stage.SCOPE, lambda: self._derive_scope(run_id, ticket)
            )
        if text is None:
            problems.append("A scope attachment is required")
        elif not allowed_files(text):
            problems.append("Scope must list allowed files")
        if problems or text is None:
            raise _Stop(RunState.BLOCKED, problems)
        self._artifact(run_id, "scope", text, ".md")
        return text

    def _scoping(self, ticket: Ticket) -> bool:
        rule = self.deps.config.policy.size_rules[ticket.size]
        return rule.analysis == AnalysisDepth.SCOPE

    async def _scoping_context(self, ticket: Ticket) -> str:
        """What the parent Question decided, as data for scoping; empty when unavailable."""
        from fugue.analysis.stage import parent_decision

        return await parent_decision(self.deps.github, ticket)

    async def _derive_scope(self, run_id: int, ticket: Ticket) -> str:
        """Scope an S ticket from the current code; re-size it to M when it does not fit."""
        saved = self.deps.artifacts.open_ref(run_id, "scoping.json")
        if saved is None:
            context = await self._scoping_context(ticket)
            role = self.deps.config.roles[Role.ANALYST]
            adapter = role_adapter(self.deps, run_id, Role.ANALYST, role)
            choice = model_choice(self.deps, run_id, Role.ANALYST, ticket.size)
            result = await adapter.run(
                AgentRequest(
                    role=Role.ANALYST,
                    prompt=scoping_prompt(ticket, context),
                    cwd=self.deps.effective_code_root,
                    access=AccessMode.READ_ONLY,
                    output_schema=scoping_schema(),
                    timeout_seconds=self.deps.config.limits.agent_timeout_seconds,
                    model=choice.models.get(role.provider),
                    models=choice.models,
                )
            )
            if result.usage is not None:
                self.deps.store.record_usage(
                    run_id,
                    Role.ANALYST,
                    result.provider,
                    result.authentication_mode,
                    **asdict(result.usage),
                    **usage_choice(choice, result.provider),
                )
            if not result.ok or result.cancelled or result.timed_out or result.provider_limited:
                state = RunState.PROVIDER_LIMITED if result.provider_limited else RunState.BLOCKED
                raise _Stop(state, [result.error or "Scoping did not complete successfully"])
            scoping = ScopingResult.model_validate(result.structured_output)
            ref = self.deps.artifacts.write(run_id, "scoping.json", scoping.model_dump_json())
            self.deps.store.register_artifact(
                run_id, ref.name, str(ref.path), ref.sha256, ref.size_bytes
            )
        else:
            scoping = ScopingResult.model_validate_json(self.deps.artifacts.read_text(saved))
        if not scoping.fits or scoping.scope is None:
            self._record_resize(run_id, ticket, scoping.resize_reason)
            if isinstance(self.deps.github, SizePort):
                await self.deps.github.set_size(ticket.number, Size.M)
            await self.deps.github.comment(
                ticket.number, resize_comment(ticket, scoping.resize_reason)
            )
            raise _Stop(RunState.BLOCKED, [f"Re-sized to M by scoping: {scoping.resize_reason}"])
        return render_scope_text(f"{ticket.number} scope", scoping.scope)

    async def _base_branch(self, run_id: int, ticket: Ticket) -> str:
        """The branch this run starts from and targets; open blockers must merge first."""
        saved = _operation_by_key(self.deps.store, run_id, "base", "base")
        if saved is not None and saved[4] is not None:
            return str(json.loads(saved[4])["branch"])
        if ticket.blocked_by:
            blockers = ", ".join(f"#{number}" for number in ticket.blocked_by)
            raise _Stop(
                RunState.BLOCKED,
                [f"Open blockers must merge first: {blockers}; or run their parent"],
            )
        return self.deps.base_branch

    async def _prepare(
        self, run_id: int, ticket: Ticket, scope: str, identity: Identity | None
    ) -> None:
        base = await self._base_branch(run_id, ticket)
        identity = identity or self._new_identity(run_id, ticket, scope)
        if not await _is_worktree(self.deps, identity):
            git = Git(self.deps.repo_root)

            async def create() -> None:
                identity.path.parent.mkdir(parents=True, exist_ok=True)
                async with self.deps.repo_lock or RepoLock(
                    self.deps.state_dir, self.deps.repo_root
                ):
                    await git.fetch()
                    if base == self.deps.base_branch:
                        await git.fast_forward(base)
                        await git.create_worktree(identity.path, identity.branch, base)
                        return
                    # Reuse a branch left by an interrupted worktree operation.
                    branches = await git._run(
                        "for-each-ref", "--format=%(refname)", f"refs/heads/{identity.branch}"
                    )
                    if f"refs/heads/{identity.branch}" in branches.splitlines():
                        await git._run("worktree", "add", "--", str(identity.path), identity.branch)
                    else:
                        # Start from the current remote head of the feature branch.
                        await git.create_worktree(
                            identity.path, identity.branch, _base_ref(self.deps, run_id)
                        )

            await self._operation(run_id, "worktree", {"branch": identity.branch}, create)
        if ticket.status != Status.IN_IMPLEMENTATION:
            await self._operation(
                run_id,
                "status",
                {},
                lambda: self.deps.github.set_status(ticket.number, Status.IN_IMPLEMENTATION),
            )

    async def _implement(self, run_id: int, identity: Identity, scope: str) -> None:
        await self._agent(
            run_id,
            identity,
            implementation_prompt(scope, workdir=identity.path, branch=identity.branch),
            scope,
        )
        git = Git(identity.path)
        commits = await git._run("rev-list", "--count", f"{_base_ref(self.deps, run_id)}..HEAD")
        if not await git.status_porcelain() and int(commits) == 0:
            raise _Stop(RunState.BLOCKED, ["Implementer made no changes"])

    async def _agent(self, run_id: int, identity: Identity, prompt: str, scope: str) -> str:
        role_config = self.deps.config.roles[Role.IMPLEMENTER]
        adapter = role_adapter(self.deps, run_id, Role.IMPLEMENTER, role_config)
        # Each chain member resumes only its own session; none is passed between providers.
        members: list[RoleConfig | ProviderEntry] = [role_config, *role_config.fallback]
        sessions: dict[str, str] = {
            member.provider: found
            for member in members
            if (
                found := _session_id(
                    self.deps.store, run_id, member.provider, str(member.authentication_mode)
                )
            )
            is not None
        }
        session = sessions.get(role_config.provider)
        fresh_prompt = prompt
        if scope not in prompt:
            # A new provider conversation needs the scope even when repairing.
            fresh_prompt = (
                implementation_prompt(scope, workdir=identity.path, branch=identity.branch)
                + "\n\n"
                + prompt
            )
        if session is None:
            prompt = fresh_prompt
        choice = saved_model_choice(self.deps.store, run_id, Role.IMPLEMENTER)
        result = await adapter.run(
            AgentRequest(
                role=Role.IMPLEMENTER,
                prompt=prompt,
                cwd=identity.path,
                access=AccessMode.WORKSPACE_WRITE,
                session_id=session,
                sessions=sessions,
                fresh_prompt=fresh_prompt,
                model=role_config.model
                if choice is None
                else choice.models.get(role_config.provider),
                models={} if choice is None else choice.models,
                timeout_seconds=self.deps.config.limits.agent_timeout_seconds,
            )
        )
        self._artifact(run_id, "agent", result.final_text)
        if result.usage is not None:
            self.deps.store.record_usage(
                run_id,
                Role.IMPLEMENTER,
                result.provider,
                result.authentication_mode,
                **asdict(result.usage),
                **usage_choice(choice, result.provider),
            )
        if result.session_id is not None:
            self.deps.store.record_agent_session(
                run_id,
                Role.IMPLEMENTER,
                result.provider,
                result.authentication_mode,
                result.session_id,
            )
        if not result.ok:
            error = result.error or "Implementer failed"
            self._artifact(run_id, "agent-error", error)
            state = RunState.PROVIDER_LIMITED if result.provider_limited else RunState.FAILED
            raise _Stop(state, [error])
        return result.final_text

    async def _gate(self, run_id: int, path: Path) -> GateResult:
        result = await self.deps.gate_runner(
            self.deps.config.policy.gate_commands,
            path,
            log_dir=self.deps.state_dir / f"run-{run_id}" / f"gate-{self._attempt_id.get()}",
        )
        if not result.passed:
            logs = "\n".join(
                step.log_path.read_text(encoding="utf-8", errors="replace")
                for step in result.steps
                if step.log_path is not None
            )
            self._artifact(run_id, "gate-failure", format_failure(result) + "\n\n" + logs)
        return result

    async def _autofix(self, run_id: int, identity: Identity, result: GateResult) -> bool:
        """Run the mechanical fix for the failed gate command; True when it changed files."""
        step = result.failed_step
        fix = (
            None if step is None else self.deps.config.policy.fix_commands.get(" ".join(step.argv))
        )
        if not fix:
            return False
        before = await self._tree_fingerprint(identity)
        await run_command(
            fix, cwd=identity.path, timeout=self.deps.config.limits.agent_timeout_seconds
        )
        return await self._tree_fingerprint(identity) != before

    async def _verify(self, run_id: int, identity: Identity, scope: str) -> bool:
        """Run the gate; on failure store the evidence and repair. True when the gate passed."""
        result = await self._stage(run_id, Stage.VERIFY, lambda: self._gate(run_id, identity.path))
        if result.passed:
            return True
        if await self._autofix(run_id, identity, result):
            return False  # A tool fixed it; verify again before any agent is asked.
        failure = format_failure(result)
        # The limit is checked before an attempt is allocated.
        if _repairs_used(self.deps.store, run_id) >= self.deps.config.limits.max_repair_attempts:
            raise _Stop(RunState.BLOCKED, [failure, "Repair limit reached"])
        self.deps.store.set_run_state(run_id, RunState.REPAIRING)
        await self._stage(
            run_id,
            Stage.REPAIR,
            lambda: self._agent(
                run_id,
                identity,
                repair_prompt(failure, workdir=identity.path, branch=identity.branch),
                scope,
            ),
        )
        self.deps.store.set_run_state(run_id, RunState.RUNNING)
        return False

    async def _commit(self, run_id: int, ticket: Ticket, scope: str, identity: Identity) -> None:
        git = Git(identity.path)
        paths = [entry[3:] for entry in await git.status_porcelain()]
        if not paths:
            return
        message = self._message(ticket, scope, self._changes(run_id, paths))
        message = replace(message, test_change=self._test_change_reason(run_id, scope))
        self._require_unprotected(paths, allowed_files(scope))
        for path in paths:
            try:
                await git.stage([path])
            except (ValueError, GitError) as error:
                raise _Stop(RunState.BLOCKED, [f"Cannot stage path {path!r}: {error}"]) from error
        self._require_unprotected(await git.staged_files(), allowed_files(scope))
        await self._operation(
            run_id,
            "commit",
            {"subject": message.render().splitlines()[0]},
            lambda: git.commit(message),
        )

    @staticmethod
    def _require_unprotected(paths: Sequence[str], allowed: Sequence[str] = ()) -> None:
        violations = protected_paths(paths, allowed)
        if violations:
            raise _Stop(RunState.BLOCKED, [f"Protected path changed: {p}" for p in violations])

    def _agent_messages(self, run_id: int) -> list[str]:
        """The agent's final messages from the latest implement attempt on, oldest first."""
        implements = self.deps.store.stage_attempts(run_id, Stage.IMPLEMENT)
        latest = implements[-1].id if implements else 0
        found: list[tuple[int, str]] = []
        for artifact in self.deps.store.list_artifacts(run_id):
            if not artifact.name.startswith("agent-") or artifact.name.startswith("agent-error-"):
                continue
            attempt_id = artifact.name.split("-", 2)[1]
            if not attempt_id.isdecimal() or int(attempt_id) < latest:
                continue
            try:
                found.append((int(attempt_id), Path(artifact.path).read_text(encoding="utf-8")))
            except (OSError, UnicodeError):
                continue
        return [text for _, text in sorted(found)]

    def _test_change_reason(self, run_id: int, scope: str) -> str | None:
        """The `Test-change:` reason the agent gave, else the scope's, else none."""
        for text in reversed(self._agent_messages(run_id)):
            match = _AGENT_TEST_CHANGE_RE.search(text)
            if match is not None:
                return str(match[1])
        return read_test_change_reason(scope)

    async def _commit_trailer(
        self, run_id: int, ticket: Ticket, scope: str, identity: Identity, reason: str
    ) -> None:
        git = Git(identity.path)
        changes = ["Record the intended test change: the check asks for a reason on the commit"]
        message = replace(self._message(ticket, scope, changes), test_change=reason)
        await self._operation(
            run_id,
            "commit",
            {"subject": message.render().splitlines()[0]},
            lambda: git.commit(message, allow_empty=True),
        )

    @staticmethod
    async def _tree_fingerprint(identity: Identity) -> tuple[str, str, tuple[str, ...]]:
        """Head, the content of tracked changes, and the untracked paths of the worktree."""
        git = Git(identity.path)
        diff = await git._run("diff", "HEAD", "--")
        status = tuple(await git.status_porcelain())
        return await git.head_sha(), hashlib.sha256(diff.encode()).hexdigest(), status

    @staticmethod
    def _no_change_stop(what: str, answer: str) -> _Stop:
        lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
        reason = " ".join(lines[-3:])[:400] or "the agent gave no explanation"
        return _Stop(RunState.BLOCKED, [f"The {what} made no changes: {reason}"])

    async def _push(self, run_id: int, identity: Identity) -> None:
        git = Git(identity.path)
        await self._require_remote_not_ahead(git, identity)
        await self._operation(
            run_id, "push", {"branch": identity.branch}, lambda: git.push(identity.branch)
        )

    async def _require_remote_not_ahead(self, git: Git, identity: Identity) -> None:
        remote = await git._run("ls-remote", "--heads", "origin", f"refs/heads/{identity.branch}")
        if not remote.strip():
            return
        remote_sha = remote.split()[0]
        head = await git.head_sha()
        if remote_sha == head:
            return
        async with self.deps.repo_lock or RepoLock(self.deps.state_dir, self.deps.repo_root):
            await git.fetch()
        await self._check_remote_ancestry(git, remote_sha, head)

    async def _check_remote_ancestry(self, git: Git, remote_sha: str, head: str) -> None:
        if await self._is_ancestor(git, remote_sha, head):
            return
        if await self._is_ancestor(git, head, remote_sha):
            message = "The remote branch is ahead of the local branch; resolve it before pushing"
        else:
            message = (
                "The remote branch has diverged from the local branch; resolve it before pushing"
            )
        raise _Stop(RunState.BLOCKED, [message])

    @staticmethod
    async def _is_ancestor(git: Git, ancestor: str, descendant: str) -> bool:
        try:
            await git._run("merge-base", "--is-ancestor", ancestor, descendant)
        except GitError as error:
            if error.returncode == 1:
                return False
            raise
        return True

    async def _open_pr(self, run_id: int, ticket: Ticket, scope: str, identity: Identity) -> None:
        if await self.deps.github.find_pr(identity.branch) is not None:
            return
        git = Git(identity.path)
        base = _base_ref(self.deps, run_id)
        merge_base = (await git._run("merge-base", base, "HEAD")).strip()
        diff = await git._run("diff", "--name-only", "--no-renames", "-z", merge_base, "HEAD", "--")
        paths = [path for path in diff.split("\0") if path]
        message = self._message(ticket, scope, self._changes(run_id, paths))
        verification = "\n".join(
            f"- {' '.join(command)}: passed" for command in self.deps.config.policy.gate_commands
        )
        body = pr_body(ticket, message.summary, message.changes, verification)
        listed = allowed_files(scope)
        outside = [p for p in paths if listed and validate_staging([p], listed)]
        if outside:
            body += "\n\nFiles changed outside the scope:\n" + "\n".join(
                f"- `{path}`" for path in outside
            )
        target = _ticket_base(self.deps, run_id)
        parent = feature_parent(self.deps.store, run_id)
        if parent is not None:
            body += (
                f"\n\nTargets the feature branch `{target}` of #{parent}. The ticket closes "
                "when the feature pull request merges into the base branch."
            )
        await self._operation(
            run_id,
            "pr",
            {"branch": identity.branch},
            lambda: self.deps.github.create_pr(
                identity.branch,
                target,
                message.render().splitlines()[0],
                body,
                ",".join(ticket.assignees) or "Bojan-Ivanovski",
                ticket.milestone or "0.1.0",
                "Fugue",
            ),
        )

    def _ci_port(self) -> CiGitHubPort:
        port = self.deps.github
        if not isinstance(port, CiGitHubPort):
            raise TypeError("The GitHub port does not support checks, logs and reruns")
        return port

    async def _wait_for_green(self, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        store = self.deps.store
        limits = self.deps.config.limits
        port = self._ci_port()
        identity = self._saved_identity(run_id)
        pr = None if identity is None else await port.find_pr(identity.branch)
        if identity is None or pr is None:
            raise _Stop(RunState.BLOCKED, ["No pull request to monitor"])
        started = self.deps.clock()
        empty_polls = 0
        restarted: set[str] = set()
        while True:
            store.set_run_state(run_id, RunState.WAITING_FOR_CI)
            summary = summarize_checks(
                without_ignored(await read_checks(partial(port.checks, pr)), limits.ignored_checks)
            )
            empty_polls = (
                0 if summary.green or summary.failed or summary.pending else empty_polls + 1
            )
            if empty_polls >= EMPTY_POLLS_BEFORE_RESTART and isinstance(port, CheckRestartPort):
                # GitHub sometimes starts no workflows for a push; reopening the pull request
                # starts them. This happens once per head, so a broken workflow cannot loop.
                head = await Git(identity.path).head_sha()
                if head not in restarted:
                    restarted.add(head)
                    await port.restart_checks(pr)
                empty_polls = 0
            if summary.is_green:
                head = await Git(identity.path).head_sha()
                store.set_run_state(run_id, RunState.RUNNING)
                return RunOutcome(run_id, RunState.RUNNING, pr.number, head, ci_green=True)
            # GitHub serves a run's logs only once the whole run has finished, so a failure
            # whose run still has pending checks waits for the next poll.
            running = {check.link for check in summary.pending}
            ready = [check for check in summary.failed if check.link not in running]
            if ready and await self._handle_failures(run_id, ticket, scope, identity, port, ready):
                # A new commit starts new checks, so the wait begins again.
                started = self.deps.clock()
            remaining = limits.ci_timeout_seconds - (self.deps.clock() - started).total_seconds()
            if remaining <= 0:
                raise _Stop(
                    RunState.BLOCKED,
                    [f"CI did not finish within {limits.ci_timeout_seconds} seconds"],
                )
            await self.deps.sleep(min(limits.ci_poll_interval_seconds, remaining))

    async def _handle_failures(
        self,
        run_id: int,
        ticket: Ticket,
        scope: str,
        identity: Identity,
        port: CiGitHubPort,
        failed: Sequence[CheckResult],
    ) -> bool:
        """Rerun infrastructure failures once; repair the rest. True when a commit was pushed."""
        store = self.deps.store
        names: dict[str, list[str]] = {}
        for check in failed:
            names.setdefault(check.link, []).append(check.name)
        substantive: list[tuple[str, str]] = []
        reruns: dict[str, tuple[Operation, str]] = {}
        for link, link_names in names.items():
            label = ", ".join(link_names)
            # A cancelled check never ran or was superseded; it has no log and no code fault.
            cancelled = all(
                check.bucket.strip().casefold() == "cancel"
                for check in failed
                if check.link == link
            )
            try:
                excerpt = await port.failed_log_tail(link)
            except (ValueError, GitHubError) as error:
                if not cancelled:
                    raise _Stop(
                        RunState.BLOCKED, [f"Cannot read the failure log for {label}: {error}"]
                    ) from error
                excerpt = ""
            kind = (
                FailureKind.INFRASTRUCTURE
                if cancelled
                else classify_failure(excerpt, self.deps.config.limits.infrastructure_markers)
            )
            # A rerun acts on the whole run, so it is keyed by run id and happens at most once.
            key = actions_run_key(link)
            operation = store.begin_operation(run_id, "ci_rerun", key, {"checks": link_names})
            if kind == FailureKind.INFRASTRUCTURE and operation.status != "done":
                reruns.setdefault(key, (operation, link))
            else:
                substantive.append((label, excerpt))
        if substantive:
            await self._repair_ci(run_id, ticket, scope, identity, substantive)
            return True
        for operation, link in reruns.values():
            try:
                await port.rerun_failed(link)
            except (ValueError, GitHubError) as error:
                raise _Stop(
                    RunState.BLOCKED, [f"Cannot rerun the failed jobs of {link}: {error}"]
                ) from error
            store.complete_operation(operation.id, {"done": True})
        return False

    async def _repair_ci(
        self,
        run_id: int,
        ticket: Ticket,
        scope: str,
        identity: Identity,
        failures: Sequence[tuple[str, str]],
    ) -> None:
        """Repair, re-run the gate, then add a new commit and push it."""
        store = self.deps.store
        prompt = ci_repair_prompt(failures)
        if _repairs_used(store, run_id) >= self.deps.config.limits.max_repair_attempts:
            raise _Stop(RunState.BLOCKED, ["CI repair limit reached"])
        store.set_run_state(run_id, RunState.REPAIRING)

        async def repair() -> str:
            # Artifacts belong to a stage attempt, so the failure is recorded inside it.
            self._artifact(run_id, "ci-failure", prompt)
            return await self._agent(run_id, identity, prompt, scope)

        if await Git(identity.path).status_porcelain():
            # An earlier repair is already in the worktree but was not delivered (the run was
            # interrupted). Verify and deliver it; asking the agent again repeats the work.
            store.set_run_state(run_id, RunState.RUNNING)
        else:
            before = await self._tree_fingerprint(identity)
            answer = await self._stage(run_id, Stage.REPAIR, repair)
            if await self._tree_fingerprint(identity) == before:
                reason = _AGENT_TEST_CHANGE_RE.search(answer)
                if reason is None:
                    raise self._no_change_stop("CI repair", answer)
                # The check only wants the trailer: record it on a commit of its own.
                store.set_run_state(run_id, RunState.RUNNING)
                await self._commit_trailer(run_id, ticket, scope, identity, str(reason[1]))
                await self._execute(Push(), run_id, ticket, scope, identity)
                return
            store.set_run_state(run_id, RunState.RUNNING)
        while not await self._verify(run_id, identity, scope):
            pass
        await self._execute(Commit(), run_id, ticket, scope, identity)
        await self._execute(Push(), run_id, ticket, scope, identity)

    # Independent review stage ------------------------------------------------------------

    def _review_port(self) -> ReviewGitHubPort:
        port = self.deps.github
        if not isinstance(port, ReviewGitHubPort):
            raise TypeError("The GitHub port does not support posting reviews and threads")
        return port

    async def _guarded(self, run_id: int, work: Callable[[], Awaitable[RunOutcome]]) -> RunOutcome:
        store = self.deps.store
        store.set_run_state(run_id, RunState.RUNNING)
        try:
            return await work()
        except asyncio.CancelledError:
            store.set_run_state(run_id, RunState.INTERRUPTED)
            raise
        except _Stop as stop:
            return await self._stopped(run_id, stop)
        except Exception:
            store.set_run_state(run_id, RunState.BLOCKED)
            raise

    async def run_review(self, run_id: int) -> RunOutcome:
        """Wait for green checks, then post one review of the head; wait for decisions on findings.

        A clean round proceeds toward merge approval.
        """
        self._review_port()  # fail fast, before any work is done
        number = self.deps.store.get_run(run_id).ticket_number

        async def work() -> RunOutcome:
            ticket, scope = await self._load(run_id, number)
            return await self._review_round(run_id, ticket, scope)

        return await self._guarded(run_id, work)

    async def continue_review(self, run_id: int) -> RunOutcome:
        """Resume a saved decision before continuing review for a parent work."""
        pending = [
            row for row in _operations(self.deps.store, run_id, "decision") if row[2] != "done"
        ]
        if pending:
            return await self.decide(run_id, _decisions(pending[-1][3]))
        return await self.run_review(run_id)

    async def decide(self, run_id: int, decisions: Mapping[int, Decision]) -> RunOutcome:
        """Apply decisions to the findings of the open round, then review again if needed.

        Accepted findings go to the implementer as a repair, then the gate, commit, push, CI and
        a new round. Rejected and deferred findings get a reply and are resolved. Decisions
        recorded by an earlier, interrupted call take precedence over the ones passed now.
        """
        self._review_port()
        number = self.deps.store.get_run(run_id).ticket_number
        latest, started = self._open_round(run_id)
        if not started:
            problems = validate_decisions(latest.findings, decisions)
            if problems:
                raise ValueError("; ".join(problems))

        async def work() -> RunOutcome:
            ticket, scope = await self._load(run_id, number)
            return await self._decide(run_id, ticket, scope, latest, decisions)

        return await self._guarded(run_id, work)

    async def _load(self, run_id: int, number: int) -> tuple[Ticket, str]:
        ticket = await self._stage(run_id, Stage.LOAD, lambda: self.deps.github.get_ticket(number))
        self._choose_models(run_id, ticket)
        scope = await self._stage(run_id, Stage.VALIDATE, lambda: self._validate(run_id, ticket))
        return ticket, scope

    def _open_round(self, run_id: int) -> tuple["_ReviewRecord", bool]:
        """The posted round with findings still to decide, and whether deciding has started."""
        rows = _operations(self.deps.store, run_id, "review")
        latest = _review_record(rows[-1]) if rows else None
        if latest is None or latest.status != "done" or not latest.findings:
            raise ValueError("No review round is waiting for a decision")
        decision = _operation_by_key(self.deps.store, run_id, "decision", f"round-{latest.round}")
        if decision is not None and decision[2] == "done":
            raise ValueError("No review round is waiting for a decision")
        return latest, decision is not None

    async def _review_round(self, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        port = self._review_port()
        identity = self._saved_identity(run_id)
        pr = None if identity is None else await port.find_pr(identity.branch)
        if identity is None or pr is None:
            raise _Stop(RunState.BLOCKED, ["No pull request to review"])
        await self._stage(
            run_id, Stage.MONITOR, lambda: self._wait_for_green(run_id, ticket, scope)
        )
        git = Git(identity.path)
        head = await git.head_sha()
        remote = await git._run("ls-remote", "--heads", "origin", f"refs/heads/{identity.branch}")
        if not remote.strip() or remote.split()[0] != head:
            raise _Stop(RunState.BLOCKED, ["The head to review has not been pushed"])
        ci_result = _ci_result(await port.checks(pr))
        return await self._stage(
            run_id,
            Stage.REVIEW,
            lambda: self._review(run_id, scope, identity, pr, head, ci_result),
        )

    def _round_for(self, run_id: int, head: str) -> tuple[int, "_ReviewRecord | None"]:
        """The round number to run at this head and the posted round to resume, if any."""
        store = self.deps.store
        rows = _operations(store, run_id, "review")
        if not rows:
            return 1, None
        latest = _review_record(rows[-1])
        decision = _operation_by_key(store, run_id, "decision", f"round-{latest.round}")
        if decision is not None and decision[2] != "done":
            raise _Stop(
                RunState.BLOCKED,
                [f"Decisions for round {latest.round} are partly applied; call decide again"],
            )
        if decision is None and latest.head == head:
            return latest.round, latest
        return latest.round + 1, None

    async def _review(
        self,
        run_id: int,
        scope: str,
        identity: Identity,
        pr: PullRequest,
        head: str,
        ci_result: str,
    ) -> RunOutcome:
        store = self.deps.store
        number, record = self._round_for(run_id, head)
        if record is None:
            findings = await self._run_reviewer(run_id, identity, scope, ci_result)
            intent = {"round": number, "head": head, "findings": [asdict(f) for f in findings]}
            operation = store.begin_operation(run_id, "review", f"round-{number}", intent)
            self._artifact(run_id, "review-findings", json.dumps(intent, indent=2), ".json")
            record = _review_record(
                (operation.id, operation.key, operation.status, operation.intent_json, None)
            )
        if record.status != "done":
            threads = await self._post_review(run_id, identity, pr, head, record)
            store.complete_operation(
                record.operation_id,
                {
                    "threads": {
                        str(index): {"comment_id": comment_id, "thread_id": thread_id}
                        for index, (comment_id, thread_id) in threads.items()
                    }
                },
            )
            record = replace(record, status="done", threads=threads)
        notes = self._review_notes()
        if record.findings:
            store.set_run_state(run_id, RunState.WAITING_FOR_DECISION)
            return RunOutcome(
                run_id,
                RunState.WAITING_FOR_DECISION,
                pr.number,
                head,
                findings=record.findings,
                notes=notes,
            )
        return await self._ready_for_approval(run_id, pr, head, notes)

    def _review_notes(self) -> tuple[str, ...]:
        if self.deps.config.github.reviewer_identity_separate:
            return ()
        return (
            "Approvals are not available: reviews are posted as COMMENT because "
            "reviewer_identity_separate is false.",
        )

    async def _ready_for_approval(
        self, run_id: int, pr: PullRequest, head: str, notes: tuple[str, ...]
    ) -> RunOutcome:
        unresolved = await self._review_port().unresolved_thread_count(pr)
        if unresolved:
            notes = (*notes, f"{unresolved} review thread(s) remain unresolved")
        self.deps.store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
        return RunOutcome(
            run_id, RunState.WAITING_FOR_MERGE_APPROVAL, pr.number, head, ci_green=True, notes=notes
        )

    def _round_log(self, run_id: int) -> list[RoundEntry]:
        store = self.deps.store
        entries: list[RoundEntry] = []
        for row in _operations(store, run_id, "review"):
            record = _review_record(row)
            decision = _operation_by_key(store, run_id, "decision", f"round-{record.round}")
            if decision is None or decision[2] != "done":
                continue
            decided = _decisions(decision[3])
            for finding in record.findings:
                chosen = decided[finding.index]
                if chosen.accept:
                    entries.append(RoundEntry(record.round, finding, "accepted"))
                elif chosen.deferred_to is not None:
                    reason = f"Deferred to #{chosen.deferred_to}"
                    entries.append(RoundEntry(record.round, finding, "deferred", reason))
                else:
                    entries.append(RoundEntry(record.round, finding, "rejected", chosen.reason))
        return entries

    async def _run_reviewer(
        self, run_id: int, identity: Identity, scope: str, ci_result: str
    ) -> list[Finding]:
        role_config = self.deps.config.roles[Role.REVIEWER]
        adapter = role_adapter(self.deps, run_id, Role.REVIEWER, role_config)
        prompt = review_prompt(
            scope=scope,
            ci_result=ci_result,
            rounds=self._round_log(run_id),
            diff=await self._diff(run_id, identity.path),
        )
        # Every round is a fresh, read-only conversation so the review stays independent.
        choice = saved_model_choice(self.deps.store, run_id, Role.REVIEWER)
        result = await adapter.run(
            AgentRequest(
                role=Role.REVIEWER,
                prompt=prompt,
                cwd=identity.path,
                access=AccessMode.READ_ONLY,
                model=role_config.model
                if choice is None
                else choice.models.get(role_config.provider),
                models={} if choice is None else choice.models,
                timeout_seconds=self.deps.config.limits.agent_timeout_seconds,
            )
        )
        self._artifact(run_id, "review-output", result.final_text)
        if result.usage is not None:
            self.deps.store.record_usage(
                run_id,
                Role.REVIEWER,
                result.provider,
                result.authentication_mode,
                **asdict(result.usage),
                **usage_choice(choice, result.provider),
            )
        implementers = self.deps.store.agent_sessions(run_id, role=Role.IMPLEMENTER)
        if implementers and implementers[-1].provider == result.provider:
            self._report("review: same provider as implementation")
        if not result.ok:
            error = result.error or "Reviewer failed"
            self._artifact(run_id, "review-error", error)
            state = RunState.PROVIDER_LIMITED if result.provider_limited else RunState.FAILED
            raise _Stop(state, [error])
        parsed = parse_findings(result.final_text)
        return [] if isinstance(parsed, Clean) else parsed

    async def _diff(self, run_id: int, path: Path) -> str:
        git = Git(path)
        base = _base_ref(self.deps, run_id)
        merge_base = (await git._run("merge-base", base, "HEAD")).strip()
        return await git._run("diff", "--no-renames", "-U3", merge_base, "HEAD", "--")

    async def _post_review(
        self,
        run_id: int,
        identity: Identity,
        pr: PullRequest,
        head: str,
        record: "_ReviewRecord",
    ) -> dict[int, tuple[int, str]]:
        """Post the round once for this head, then map its findings to their threads."""
        port = self._review_port()
        built = build_review(
            record.findings,
            diff_ranges(await self._diff(run_id, identity.path)),
            record.round,
            separate_identity=self.deps.config.github.reviewer_identity_separate,
        )
        marker = review_marker(record.round)
        posted = [r for r in await port.reviews(pr) if r.commit_id == head and marker in r.body]
        if not posted:
            await self._operation(
                run_id,
                "review_post",
                {"round": record.round, "event": built.event, "comments": len(built.comments)},
                lambda: port.post_review(pr, head, built.body, built.comments, built.event),
            )
        threads: dict[int, tuple[int, str]] = {}
        for thread in await port.review_threads(pr):
            marked = marked_finding(thread.comments[0].body) if thread.comments else None
            if marked is not None and marked[0] == record.round:
                threads[marked[1]] = (thread.comments[0].id, thread.id)
        return threads

    async def _decide(
        self,
        run_id: int,
        ticket: Ticket,
        scope: str,
        latest: "_ReviewRecord",
        decisions: Mapping[int, Decision],
    ) -> RunOutcome:
        store = self.deps.store
        port = self._review_port()
        identity = self._saved_identity(run_id)
        pr = None if identity is None else await port.find_pr(identity.branch)
        if identity is None or pr is None:
            raise _Stop(RunState.BLOCKED, ["No pull request to update"])
        intent = {
            "round": latest.round,
            "decisions": {str(index): asdict(item) for index, item in decisions.items()},
        }
        operation = store.begin_operation(run_id, "decision", f"round-{latest.round}", intent)
        decided = _decisions(operation.intent_json)
        accepted = [finding for finding in latest.findings if decided[finding.index].accept]
        what = ""
        if accepted:
            await self._apply_fixes(run_id, ticket, scope, identity, latest, accepted)
            names = await Git(identity.path)._run(
                "diff", "--name-only", "--no-renames", "-z", latest.head, "HEAD", "--"
            )
            what = "; ".join(self._changes(run_id, [p for p in names.split("\0") if p]))
        head = await Git(identity.path).head_sha()
        await self._stage(
            run_id,
            Stage.REVIEW,
            lambda: self._answer_threads(run_id, pr, latest, decided, head, what),
        )
        store.complete_operation(operation.id, {"done": True})
        if accepted or any(finding.blocking for finding in latest.findings):
            return await self._review_round(run_id, ticket, scope)
        return await self._ready_for_approval(run_id, pr, head, self._review_notes())

    async def _apply_fixes(
        self,
        run_id: int,
        ticket: Ticket,
        scope: str,
        identity: Identity,
        latest: "_ReviewRecord",
        accepted: Sequence[Finding],
    ) -> None:
        """Repair, run the gate, commit and push; each step resumes where it stopped."""
        store = self.deps.store
        git = Git(identity.path)
        repair = store.begin_operation(
            run_id,
            "review_repair",
            f"round-{latest.round}",
            {"findings": [item.index for item in accepted]},
        )
        if repair.status != "done":
            if _repairs_used(store, run_id) >= self.deps.config.limits.max_repair_attempts:
                raise _Stop(RunState.BLOCKED, ["Repair limit reached"])
            store.set_run_state(run_id, RunState.REPAIRING)
            prompt = review_repair_prompt(accepted)
            before = await self._tree_fingerprint(identity)
            await self._stage(
                run_id, Stage.REPAIR, lambda: self._agent(run_id, identity, prompt, scope)
            )
            if await self._tree_fingerprint(identity) == before:
                # Leave the repair open so the next call asks the agent again.
                raise _Stop(
                    RunState.BLOCKED, ["The implementer made no changes for the accepted findings"]
                )
            store.complete_operation(repair.id, {"done": True})
            store.set_run_state(run_id, RunState.RUNNING)
        if await git.status_porcelain():
            while not await self._verify(run_id, identity, scope):
                pass
            await self._execute(Commit(), run_id, ticket, scope, identity)
        head = await git.head_sha()
        if head == latest.head:
            raise _Stop(
                RunState.BLOCKED, ["The implementer made no changes for the accepted findings"]
            )
        remote = await git._run("ls-remote", "--heads", "origin", f"refs/heads/{identity.branch}")
        if not remote.strip() or remote.split()[0] != head:
            await self._execute(Push(), run_id, ticket, scope, identity)

    async def _answer_threads(
        self,
        run_id: int,
        pr: PullRequest,
        latest: "_ReviewRecord",
        decided: Mapping[int, Decision],
        head: str,
        what: str,
    ) -> None:
        """Reply on each finding's thread and resolve it; report threads that cannot be resolved."""
        store = self.deps.store
        port = self._review_port()
        resolved = {thread.id for thread in await port.review_threads(pr) if thread.resolved}
        refused: list[str] = []
        for finding in latest.findings:
            target = latest.threads.get(finding.index)
            if target is None:
                continue  # Posted in the review body, so there is no thread to answer.
            comment_id, thread_id = target
            chosen = decided[finding.index]
            if chosen.accept:
                body = f"Fixed in {head}: {what or 'the accepted finding was addressed'}"
            elif chosen.deferred_to is not None:
                body = f"Deferred to #{chosen.deferred_to}"
            else:
                body = f"Rejected: {chosen.reason}"
            reply = store.begin_operation(
                run_id,
                "thread_reply",
                f"round-{latest.round}:f{finding.index}",
                {"thread": thread_id, "body": body},
            )
            if reply.status != "done":
                await port.reply_to_thread(pr, comment_id, body)
                store.complete_operation(reply.id, {"done": True})
            if thread_id in resolved:
                continue
            try:
                await port.resolve_thread(thread_id)
            except ResolvePermissionError:
                refused.append(thread_id)
        if refused:
            raise _Stop(
                RunState.BLOCKED,
                [f"Cannot resolve review threads (permission denied): {', '.join(refused)}"],
            )


@dataclass(frozen=True)
class _ReviewRecord:
    operation_id: int
    status: str
    round: int
    head: str
    findings: tuple[Finding, ...]
    threads: dict[int, tuple[int, str]]


_OperationRow = tuple[int, str, str, str, str | None]


def _operations(store: Store, run_id: int, kind: str) -> list[_OperationRow]:
    rows = store._connection.execute(
        "SELECT id, key, status, intent_json, result_json FROM operations "
        "WHERE run_id = ? AND kind = ? ORDER BY id",
        (run_id, kind),
    ).fetchall()
    return [(row[0], row[1], row[2], row[3], row[4]) for row in rows]


def _operation_by_key(store: Store, run_id: int, kind: str, key: str) -> _OperationRow | None:
    return next((row for row in _operations(store, run_id, kind) if row[1] == key), None)


def _review_record(row: _OperationRow) -> _ReviewRecord:
    intent = json.loads(row[3])
    result = json.loads(row[4]) if row[4] else {}
    return _ReviewRecord(
        row[0],
        row[2],
        int(intent["round"]),
        str(intent["head"]),
        tuple(Finding(**item) for item in intent["findings"]),
        {
            int(index): (int(value["comment_id"]), str(value["thread_id"]))
            for index, value in result.get("threads", {}).items()
        },
    )


def _decisions(intent_json: str) -> dict[int, Decision]:
    stored = json.loads(intent_json)["decisions"]
    return {int(index): Decision(**item) for index, item in stored.items()}


def _ci_result(checks: Sequence[CheckResult]) -> str:
    summary = summarize_checks(checks)
    header = "CI is green." if summary.is_green else "CI is not green."
    return "\n".join([header, *(f"- {check.name}: {check.bucket}" for check in checks)])
