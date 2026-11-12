"""Execute the ticket-to-pull-request slice with durable operation intents."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from fugue.agents import AccessMode, AdapterRegistry, AgentRequest
from fugue.artifacts import ArtifactStore
from fugue.config import FugueConfig
from fugue.engine.facts import (
    allowed_files,
    branch_name,
    scope_url,
    selected_solution,
    worktree_path,
)
from fugue.engine.ports import GitHubPort
from fugue.engine.prompts import commit_message, implementation_prompt, pr_body, repair_prompt
from fugue.gate import GateResult, format_failure, run_gate
from fugue.gitops import CommitMessage, Git, GitError, validate_message, validate_staging
from fugue.models import IssueType, Role, Status, Ticket
from fugue.process import run_command
from fugue.store import Store
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


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    state: RunState
    pr_number: int | None = None
    head_sha: str | None = None
    problems: tuple[str, ...] = ()


class _Stop(Exception):
    def __init__(self, state: RunState, problems: Sequence[str]) -> None:
        self.state = state
        self.problems = tuple(problems)
        super().__init__("; ".join(problems))


class Engine:
    def __init__(self, deps: EngineDeps) -> None:
        self.deps = deps

    def _artifact(self, run_id: int, name: str, text: str) -> None:
        ref = self.deps.artifacts.write(run_id, name, text)
        self.deps.store.register_artifact(run_id, name, str(ref.path), ref.sha256, ref.size_bytes)

    async def _stage[T](self, run_id: int, stage: Stage, action: Callable[[], Awaitable[T]]) -> T:
        attempt = self.deps.store.begin_attempt(run_id, stage)
        try:
            if (
                stage == Stage.REPAIR
                and attempt.attempt > self.deps.config.limits.max_repair_attempts
            ):
                raise _Stop(RunState.BLOCKED, ["Repair limit reached"])
            result = await action()
        except BaseException as error:
            status = "interrupted" if isinstance(error, asyncio.CancelledError) else "failed"
            if isinstance(error, _Stop):
                status = error.state
            self.deps.store.finish_attempt(attempt.id, status, str(error))
            raise
        if isinstance(result, GateResult) and not result.passed:
            self.deps.store.finish_attempt(attempt.id, "failed", format_failure(result))
        else:
            self.deps.store.finish_attempt(attempt.id, "completed")
        return result

    async def _operation(
        self,
        run_id: int,
        kind: str,
        key: str,
        intent: dict[str, Any],
        action: Callable[[], Awaitable[dict[str, Any]]],
        reconcile: Callable[[], Awaitable[dict[str, Any] | None]],
    ) -> dict[str, Any]:
        operation = self.deps.store.begin_operation(run_id, kind, key, intent)
        result = await reconcile()
        if result is not None:
            self.deps.store.mark_reconciled(operation.id, result)
            return result
        result = await action()
        self.deps.store.complete_operation(operation.id, result)
        return result

    def _identity(self, run_id: int, ticket: Ticket) -> dict[str, Any]:
        # A constant operation key lets resumes recover identity without deriving
        # a new branch, path or subject from mutable ticket fields.
        operation = self.deps.store.begin_operation(run_id, "prepare", "identity", {})
        if operation.result_json is not None:
            identity: dict[str, Any] = json.loads(operation.result_json)
            return identity
        message = commit_message(ticket, ticket.body_sections.get("Summary", ""), [])
        identity = {
            "branch": branch_name(ticket),
            "path": str(worktree_path(self.deps.worktrees_root, ticket).resolve()),
            "issue_type": message.issue_type,
            "explanation": message.explanation,
            "subject": message.render().splitlines()[0],
        }
        self.deps.store.complete_operation(operation.id, identity)
        return identity

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        store = self.deps.store
        runs = store.list_runs(ticket_number)
        run = next(
            (r for r in reversed(runs) if r.state not in (RunState.COMPLETED, RunState.FAILED)),
            None,
        )
        if run is None:
            run = store.create_run(ticket_number, str(self.deps.repo_root.resolve()))
        run_id = run.id
        head: str | None = None
        store.set_run_state(run_id, RunState.RUNNING)
        try:
            ticket = await self._stage(
                run_id, Stage.LOAD, lambda: self.deps.github.get_ticket(ticket_number)
            )
            scope = await self._stage(
                run_id, Stage.VALIDATE, lambda: self._validate(run_id, ticket)
            )
            path = await self._stage(run_id, Stage.PREPARE, lambda: self._prepare(run_id, ticket))
            scope_sha = hashlib.sha256(scope.encode("utf-8")).hexdigest()
            implementations = [
                artifact
                for artifact in store.list_artifacts(run_id)
                if artifact.name.startswith("implemented-")
            ]
            identity = self._identity(run_id, ticket)
            implementation_key = json.dumps(
                {
                    "scope_sha256": scope_sha,
                    "branch": identity["branch"],
                    "path": str(path),
                },
                sort_keys=True,
            )
            implemented_key = (
                Path(implementations[-1].path).read_text(encoding="utf-8")
                if implementations
                else None
            )
            if implemented_key != implementation_key:
                await self._stage(
                    run_id,
                    Stage.IMPLEMENT,
                    lambda: self._agent(run_id, path, implementation_prompt(scope)),
                )
                self._artifact(run_id, f"implemented-{len(implementations)}", implementation_key)
            await self._verify(run_id, path)
            head, pr_number = await self._stage(
                run_id, Stage.DELIVER, lambda: self._deliver(run_id, ticket, scope, path)
            )
            state = RunState.WAITING_FOR_CI
            store.set_run_state(run_id, state)
            return RunOutcome(run_id, state, pr_number, head)
        except asyncio.CancelledError:
            store.set_run_state(run_id, RunState.INTERRUPTED)
            raise
        except _Stop as stop:
            store.set_run_state(run_id, stop.state)
            return RunOutcome(run_id, stop.state, head_sha=head, problems=stop.problems)

    async def _validate(self, run_id: int, ticket: Ticket) -> str:
        url = scope_url(ticket)
        name = f"scope-{hashlib.sha256(url.encode()).hexdigest()}.md" if url else None
        ref = self.deps.artifacts.open_ref(run_id, name) if name else None
        problems: list[str] = []
        if ref is None and url is not None:
            try:
                self.deps.state_dir.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=self.deps.state_dir) as directory:
                    downloaded = await self.deps.github.download_attachment(
                        url, Path(directory) / "scope.md"
                    )
                    text = downloaded.read_text(encoding="utf-8")
                assert name is not None
                self._artifact(run_id, name, text)
                ref = self.deps.artifacts.open_ref(run_id, name)
            except (OSError, UnicodeError, RuntimeError, ValueError) as error:
                problems.append(f"Scope attachment could not be downloaded: {error}")
        problems.extend(
            ready_for_implementation(
                ticket,
                self.deps.config.policy,
                has_scope_attachment=ref is not None,
                selected_solution=selected_solution(ticket),
            )
        )
        if ref is None:
            problems.append("A scope attachment is required")
        text = self.deps.artifacts.read_text(ref) if ref is not None else ""
        if ref is not None and not allowed_files(text):
            problems.append("Scope must list allowed files")
        if problems:
            raise _Stop(RunState.BLOCKED, problems)
        return text

    async def _prepare(self, run_id: int, ticket: Ticket) -> Path:
        identity = self._identity(run_id, ticket)
        branch = str(identity["branch"])
        path = Path(identity["path"])
        git = Git(self.deps.repo_root)
        base_name = f"preparation-base-{hashlib.sha256(branch.encode()).hexdigest()}"
        operation = self.deps.store.begin_operation(run_id, "worktree", branch, {"path": str(path)})

        async def existing() -> dict[str, Any] | None:
            if path.exists():
                try:
                    await git.resolve_ref(branch)
                    if await Git(path).current_branch() == branch:
                        saved = json.loads(operation.result_json) if operation.result_json else {}
                        base_sha = saved.get("base_sha")
                        if base_sha is None:
                            ref = self.deps.artifacts.open_ref(run_id, base_name)
                            if ref is not None:
                                base_sha = self.deps.artifacts.read_text(ref)
                            else:
                                # Adopt older or pre-existing worktrees using their
                                # common ancestor, never the current base tip.
                                base = await git.resolve_ref(self.deps.base_branch)
                                head = await Git(path).head_sha()
                                base_sha = (await git._run("merge-base", base, head)).strip()
                                self._artifact(run_id, base_name, base_sha)
                        return {**identity, "base_sha": base_sha}
                except GitError:
                    pass
            return None

        async def create() -> dict[str, Any]:
            path.parent.mkdir(parents=True, exist_ok=True)
            await git.fetch()
            await git.fast_forward(self.deps.base_branch)
            ref = self.deps.artifacts.open_ref(run_id, base_name)
            if ref is None:
                base_sha = await git.resolve_ref(self.deps.base_branch)
                # Persist before creating the worktree so reconciliation can
                # recover this base if creation finishes before its result is saved.
                self._artifact(run_id, base_name, base_sha)
            else:
                base_sha = self.deps.artifacts.read_text(ref)
            await git.create_worktree(path, branch, base_sha)
            return {**identity, "base_sha": base_sha}

        await self._operation(run_id, "worktree", branch, {"path": str(path)}, create, existing)

        async def status_exists() -> dict[str, Any] | None:
            current = await self.deps.github.get_ticket(ticket.number)
            return (
                {"status": Status.IN_IMPLEMENTATION}
                if (current.status == Status.IN_IMPLEMENTATION)
                else None
            )

        async def set_status() -> dict[str, Any]:
            await self.deps.github.set_status(ticket.number, Status.IN_IMPLEMENTATION)
            return {"status": Status.IN_IMPLEMENTATION}

        await self._operation(run_id, "status", str(ticket.number), {}, set_status, status_exists)
        return path

    async def _agent(self, run_id: int, path: Path, prompt: str) -> None:
        role_config = self.deps.config.roles[Role.IMPLEMENTER]
        adapter = self.deps.registry.build(role_config)
        sessions = [
            a for a in self.deps.store.list_artifacts(run_id) if a.name.startswith("session-")
        ]
        session = Path(sessions[-1].path).read_text() if sessions else None
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
        call = len(
            [a for a in self.deps.store.list_artifacts(run_id) if a.name.startswith("agent-")]
        )
        self._artifact(run_id, f"agent-{call}.txt", result.final_text)
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
            self._artifact(run_id, f"session-{call}.txt", result.session_id)
        if not result.ok:
            error = result.error or "Implementer failed"
            self._artifact(run_id, f"agent-error-{call}.txt", error)
            limited = re.search(r"rate[ _-]?limit|too many requests|\b429\b", error, re.I)
            raise _Stop(RunState.PROVIDER_LIMITED if limited else RunState.FAILED, [error])

    async def _pending_repair(self, run_id: int, path: Path, failure: str) -> None:
        # The intent is the pending-repair marker. It precedes the failure artifact
        # so termination immediately after writing that artifact cannot lose work.
        for operation in self.deps.store.pending_operations(run_id):
            if operation.kind != "repair":
                continue
            self.deps.store.set_run_state(run_id, RunState.REPAIRING)
            try:
                await self._stage(
                    run_id,
                    Stage.REPAIR,
                    partial(self._agent, run_id, path, repair_prompt(failure)),
                )
            except _Stop as stop:
                if stop.state == RunState.BLOCKED:
                    raise _Stop(stop.state, [failure, *stop.problems]) from stop
                raise
            self.deps.store.complete_operation(operation.id, {"repaired": True})
            self.deps.store.set_run_state(run_id, RunState.RUNNING)

    async def _verify(self, run_id: int, path: Path) -> None:
        while True:
            # Gate attempts and repair attempts have independent counters. The
            # Store allocates each repair's ordinal through begin_attempt.
            attempt = self.deps.store.begin_attempt(run_id, Stage.VERIFY)
            try:
                result = await self.deps.gate_runner(
                    self.deps.config.policy.gate_commands,
                    path,
                    log_dir=self.deps.state_dir / f"run-{run_id}" / f"gate-{attempt.attempt}",
                )
            except BaseException as error:
                status = "interrupted" if isinstance(error, asyncio.CancelledError) else "failed"
                self.deps.store.finish_attempt(attempt.id, status, str(error))
                raise
            if result.passed:
                self.deps.store.finish_attempt(attempt.id, "completed")
                for pending in self.deps.store.pending_operations(run_id):
                    if pending.kind == "repair":
                        self.deps.store.complete_operation(pending.id, {"satisfied_by_gate": True})
                return
            failure = format_failure(result)
            self.deps.store.finish_attempt(attempt.id, "failed", failure)
            full = "\n".join(
                step.log_path.read_text(encoding="utf-8", errors="replace")
                for step in result.steps
                if step.log_path is not None
            )
            if not any(
                pending.kind == "repair" for pending in self.deps.store.pending_operations(run_id)
            ):
                self.deps.store.begin_operation(
                    run_id, "repair", str(attempt.id), {"failure": failure}
                )
            self._artifact(run_id, f"gate-failure-{attempt.attempt}.txt", failure + "\n\n" + full)
            await self._pending_repair(run_id, path, failure)

    async def _deliver(
        self, run_id: int, ticket: Ticket, scope: str, path: Path
    ) -> tuple[str, int]:
        git = Git(path)
        identity = self._identity(run_id, ticket)
        branch = str(identity["branch"])
        summary = ticket.body_sections.get("Summary", "")
        changes = [f"{name}: implement the selected scope" for name in allowed_files(scope)]
        message = CommitMessage(
            str(identity["issue_type"]),
            ticket.number,
            str(identity["explanation"]),
            summary,
            tuple(changes),
        )
        subject = str(identity["subject"])
        problems = validate_message(
            message, list(IssueType), self.deps.config.policy.commit_subject_max
        )
        if problems:
            raise _Stop(RunState.BLOCKED, problems)

        preparation = self.deps.store.begin_operation(run_id, "worktree", branch, {})
        prepared = json.loads(preparation.result_json) if preparation.result_json else {}
        base_sha = prepared.get("base_sha")
        if not isinstance(base_sha, str):
            raise _Stop(RunState.BLOCKED, ["Preparation base SHA is missing"])
        operation = self.deps.store.begin_operation(run_id, "commit", branch, {})

        async def committed() -> dict[str, Any] | None:
            head = await git.head_sha()
            changed = await git._run(
                "diff",
                "--name-status",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--no-relative",
                "--ignore-submodules=none",
                "-z",
                f"{base_sha}..{head}",
                "--",
            )
            paths = changed.removesuffix("\0").split("\0")[1::2] if changed else []
            problems = [
                f"Outside allowed files: {p}" for p in validate_staging(paths, allowed_files(scope))
            ]
            if operation.result_json is not None:
                recorded = json.loads(operation.result_json)
                if recorded.get("head_sha") != head:
                    problems.append("Commit head SHA differs from the recorded operation result")
            if problems:
                raise _Stop(RunState.BLOCKED, problems)
            if await git.status_porcelain():
                return None
            result = await run_command(["git", "log", "-1", "--format=%s"], cwd=path)
            if result.stdout.strip() == subject:
                return {"head_sha": await git.head_sha()}
            return None

        async def commit() -> dict[str, Any]:
            paths = [entry[3:] for entry in await git.status_porcelain()]
            await git.stage(paths)
            violations = validate_staging(await git.staged_files(), allowed_files(scope))
            if violations:
                raise _Stop(RunState.BLOCKED, [f"Outside allowed files: {p}" for p in violations])
            return {"head_sha": await git.commit(message)}

        committed_result = await self._operation(run_id, "commit", branch, {}, commit, committed)
        head = str(committed_result["head_sha"])

        async def pushed() -> dict[str, Any] | None:
            result = await run_command(
                ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], cwd=path
            )
            if result.returncode == 0 and result.stdout.split()[:1] == [head]:
                return {"head_sha": head}
            return None

        async def push() -> dict[str, Any]:
            await git.push(branch)
            return {"head_sha": head}

        await self._operation(run_id, "push", branch, {"head_sha": head}, push, pushed)

        async def found() -> dict[str, Any] | None:
            pr = await self.deps.github.find_pr(branch)
            return {"pr_number": pr.number} if pr else None

        async def create_pr() -> dict[str, Any]:
            verification = "\n".join(
                f"- {' '.join(command)}: passed"
                for command in self.deps.config.policy.gate_commands
            )
            pr = await self.deps.github.create_pr(
                branch,
                self.deps.base_branch,
                subject,
                pr_body(ticket, summary, changes, verification),
                ",".join(ticket.assignees) or "Bojan-Ivanovski",
                ticket.milestone or "0.1.0",
                "Fugue",
            )
            return {"pr_number": pr.number}

        result = await self._operation(run_id, "pr", branch, {}, create_pr, found)
        return head, int(result["pr_number"])
