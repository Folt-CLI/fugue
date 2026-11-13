"""Execute the ticket-to-pull-request slice by observing live state and deciding."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from fugue.agents import AccessMode, AdapterRegistry, AgentRequest
from fugue.artifacts import ArtifactConflict, ArtifactRef, ArtifactStore
from fugue.config import FugueConfig
from fugue.engine.ci import (
    FailureKind,
    actions_run_key,
    ci_repair_prompt,
    classify_failure,
    summarize_checks,
)
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
    scope_url,
    selected_solution,
    worktree_path,
)
from fugue.engine.ports import CiGitHubPort, GitHubPort
from fugue.engine.prompts import commit_message, implementation_prompt, pr_body, repair_prompt
from fugue.gate import GateResult, format_failure, run_gate
from fugue.github import CheckResult, GitHubError
from fugue.gitops import CommitMessage, Git, GitError, validate_message, validate_staging
from fugue.models import IssueType, Role, Status, Ticket
from fugue.store import Operation, Run, Store
from fugue.workflow import RunState, Stage, ready_for_implementation


class GateRunner(Protocol):
    def __call__(
        self, commands: Sequence[Sequence[str]], cwd: Path, *, log_dir: Path
    ) -> Awaitable[GateResult]: ...


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


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    state: RunState
    pr_number: int | None = None
    head_sha: str | None = None
    problems: tuple[str, ...] = ()
    ci_green: bool = False


@dataclass(frozen=True)
class Identity:
    """Branch and worktree path, fixed when the worktree is first prepared."""

    branch: str
    path: Path


class _Stop(Exception):
    def __init__(self, state: RunState, problems: Sequence[str]) -> None:
        self.state = state
        self.problems = tuple(problems)
        super().__init__("; ".join(problems))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _implemented_sha256(store: Store, run_id: int) -> str | None:
    # Store has no public attempt reader; the latest finished implement attempt
    # records the scope digest it implemented in its detail.
    row = store._connection.execute(
        "SELECT detail FROM stage_attempts WHERE run_id = ? AND stage = ? "
        "AND status = 'completed' ORDER BY id DESC LIMIT 1",
        (run_id, Stage.IMPLEMENT),
    ).fetchone()
    return None if row is None else row[0]


def _repairs_used(store: Store, run_id: int) -> int:
    row = store._connection.execute(
        "SELECT COUNT(*) FROM stage_attempts WHERE run_id = ? AND stage = ? "
        "AND status = 'completed'",
        (run_id, Stage.REPAIR),
    ).fetchone()
    return int(row[0])


def _session_id(store: Store, run_id: int, provider: str, authentication_mode: str) -> str | None:
    row = store._connection.execute(
        "SELECT session_id FROM agent_sessions WHERE run_id = ? AND role = ? "
        "AND provider = ? AND authentication_mode = ? ORDER BY id DESC LIMIT 1",
        (run_id, Role.IMPLEMENTER, provider, authentication_mode),
    ).fetchone()
    return None if row is None else row[0]


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
    base = f"refs/heads/{deps.base_branch}"
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
        # The operation is an audit record only; resume never reads it back.
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

    def _message(self, ticket: Ticket, scope: str) -> CommitMessage:
        changes = [f"{name}: implement the selected scope" for name in allowed_files(scope)]
        message = commit_message(ticket, ticket.body_sections.get("Summary", ""), changes)
        problems = validate_message(
            message, list(IssueType), self.deps.config.policy.commit_subject_max
        )
        if problems:
            raise _Stop(RunState.BLOCKED, problems)
        return message

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
            store.set_run_state(run_id, stop.state)
            return RunOutcome(run_id, stop.state, problems=stop.problems)
        except Exception:
            store.set_run_state(run_id, RunState.BLOCKED)
            raise

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
                has_scope_attachment=text is not None,
                selected_solution=selected_solution(ticket),
            )
        )
        if text is None:
            problems.append("A scope attachment is required")
        elif not allowed_files(text):
            problems.append("Scope must list allowed files")
        if problems or text is None:
            raise _Stop(RunState.BLOCKED, problems)
        self._artifact(run_id, "scope", text, ".md")
        return text

    async def _prepare(
        self, run_id: int, ticket: Ticket, scope: str, identity: Identity | None
    ) -> None:
        identity = identity or self._new_identity(run_id, ticket, scope)
        if not await _is_worktree(self.deps, identity):
            git = Git(self.deps.repo_root)

            async def create() -> None:
                identity.path.parent.mkdir(parents=True, exist_ok=True)
                await git.fetch()
                await git.fast_forward(self.deps.base_branch)
                await git.create_worktree(identity.path, identity.branch, self.deps.base_branch)

            await self._operation(run_id, "worktree", {"branch": identity.branch}, create)
        if ticket.status != Status.IN_IMPLEMENTATION:
            await self._operation(
                run_id,
                "status",
                {},
                lambda: self.deps.github.set_status(ticket.number, Status.IN_IMPLEMENTATION),
            )

    async def _implement(self, run_id: int, identity: Identity, scope: str) -> None:
        await self._agent(run_id, identity.path, implementation_prompt(scope), scope)
        git = Git(identity.path)
        commits = await git._run("rev-list", "--count", f"refs/heads/{self.deps.base_branch}..HEAD")
        if not await git.status_porcelain() and int(commits) == 0:
            raise _Stop(RunState.BLOCKED, ["Implementer made no changes"])

    async def _agent(self, run_id: int, path: Path, prompt: str, scope: str) -> None:
        role_config = self.deps.config.roles[Role.IMPLEMENTER]
        adapter = self.deps.registry.build(role_config)
        session = _session_id(
            self.deps.store, run_id, adapter.name, str(adapter.authentication_mode)
        )
        if session is None and scope not in prompt:
            # A new provider conversation needs the scope even when repairing.
            prompt = implementation_prompt(scope) + "\n\n" + prompt
        result = await adapter.run(
            AgentRequest(
                role=Role.IMPLEMENTER,
                prompt=prompt,
                cwd=path,
                access=AccessMode.WORKSPACE_WRITE,
                session_id=session,
                model=role_config.model,
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

    async def _verify(self, run_id: int, identity: Identity, scope: str) -> bool:
        """Run the gate; on failure store the evidence and repair. True when the gate passed."""
        result = await self._stage(run_id, Stage.VERIFY, lambda: self._gate(run_id, identity.path))
        if result.passed:
            return True
        failure = format_failure(result)
        # The limit is checked before an attempt is allocated.
        if _repairs_used(self.deps.store, run_id) >= self.deps.config.limits.max_repair_attempts:
            raise _Stop(RunState.BLOCKED, [failure, "Repair limit reached"])
        self.deps.store.set_run_state(run_id, RunState.REPAIRING)
        await self._stage(
            run_id,
            Stage.REPAIR,
            lambda: self._agent(run_id, identity.path, repair_prompt(failure), scope),
        )
        self.deps.store.set_run_state(run_id, RunState.RUNNING)
        return False

    async def _commit(self, run_id: int, ticket: Ticket, scope: str, identity: Identity) -> None:
        git = Git(identity.path)
        paths = [entry[3:] for entry in await git.status_porcelain()]
        if not paths:
            return
        message = self._message(ticket, scope)
        allowed = allowed_files(scope)
        self._require_allowed(paths, allowed)
        for path in paths:
            try:
                await git.stage([path])
            except (ValueError, GitError) as error:
                raise _Stop(RunState.BLOCKED, [f"Cannot stage path {path!r}: {error}"]) from error
        self._require_allowed(await git.staged_files(), allowed)
        await self._operation(
            run_id,
            "commit",
            {"subject": message.render().splitlines()[0]},
            lambda: git.commit(message),
        )

    @staticmethod
    def _require_allowed(paths: Sequence[str], allowed: Sequence[str]) -> None:
        violations = validate_staging(paths, allowed)
        if violations:
            raise _Stop(RunState.BLOCKED, [f"Outside allowed files: {p}" for p in violations])

    async def _push(self, run_id: int, identity: Identity) -> None:
        git = Git(identity.path)
        await self._operation(
            run_id, "push", {"branch": identity.branch}, lambda: git.push(identity.branch)
        )

    async def _open_pr(self, run_id: int, ticket: Ticket, scope: str, identity: Identity) -> None:
        if await self.deps.github.find_pr(identity.branch) is not None:
            return
        message = self._message(ticket, scope)
        verification = "\n".join(
            f"- {' '.join(command)}: passed" for command in self.deps.config.policy.gate_commands
        )
        body = pr_body(ticket, message.summary, message.changes, verification)
        await self._operation(
            run_id,
            "pr",
            {"branch": identity.branch},
            lambda: self.deps.github.create_pr(
                identity.branch,
                self.deps.base_branch,
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
        while True:
            store.set_run_state(run_id, RunState.WAITING_FOR_CI)
            summary = summarize_checks(await port.checks(pr))
            if summary.is_green:
                head = await Git(identity.path).head_sha()
                store.set_run_state(run_id, RunState.RUNNING)
                return RunOutcome(run_id, RunState.RUNNING, pr.number, head, ci_green=True)
            if summary.failed and await self._handle_failures(
                run_id, ticket, scope, identity, port, summary.failed
            ):
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
            try:
                excerpt = await port.failed_log_tail(link)
            except (ValueError, GitHubError) as error:
                raise _Stop(
                    RunState.BLOCKED, [f"Cannot read the failure log for {label}: {error}"]
                ) from error
            kind = classify_failure(excerpt, self.deps.config.limits.infrastructure_markers)
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
        self._artifact(run_id, "ci-failure", prompt)
        if _repairs_used(store, run_id) >= self.deps.config.limits.max_repair_attempts:
            raise _Stop(RunState.BLOCKED, ["CI repair limit reached"])
        store.set_run_state(run_id, RunState.REPAIRING)
        await self._stage(
            run_id, Stage.REPAIR, lambda: self._agent(run_id, identity.path, prompt, scope)
        )
        store.set_run_state(run_id, RunState.RUNNING)
        while not await self._verify(run_id, identity, scope):
            pass
        await self._execute(Commit(), run_id, ticket, scope, identity)
        await self._execute(Push(), run_id, ticket, scope, identity)
