"""Run the children of a parent on one feature branch, then deliver it to the base branch.

Every child starts from the current head of the feature branch, targets it with its own
pull request and is merged into it with a merge commit (an integration merge) once its
gate, review and CI are done. A child starts, and is analysed, only when its sibling
blockers are merged; ready S and XS children are analysed together in analysis batches.
When every child is merged, one feature pull request delivers the feature branch to the
base branch; it is reviewed at its exact head (`policy.feature_review`) and its merge
always waits for the developer's approval of that head. A child is reviewed only when its
Size rule requires a review.
"""

import asyncio
import getpass
import json
import sys
from collections.abc import Callable, Collection, Sequence
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from functools import partial
from heapq import heapify, heappop, heappush
from pathlib import Path

from fugue.engine.ci import read_checks, summarize_checks
from fugue.engine.closure import review_clean
from fugue.engine.facts import (
    explanation,
    feature_branch_name,
    selected_solution,
    worktree_path,
)
from fugue.engine.locks import RepoLock, TicketLock
from fugue.engine.pipeline import (
    Engine,
    EngineDeps,
    Identity,
    RunOutcome,
    _is_worktree,
    _operation_by_key,
    _operations,
    _Stop,
)
from fugue.engine.prompts import (
    feature_repair_prompt,
    merge_conflict_prompt,
    without_orchestrator_notes,
)
from fugue.engine.review import Decision
from fugue.github import PullRequest
from fugue.gitops import CommitMessage, Git, GitError, protected_paths
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.store import Run
from fugue.store.records import ParentRun
from fugue.workflow import RunState, Stage
from fugue.workflow.guards import can_close, review_required

INTEGRATION = "integration_merge"
FEATURE_MERGE = "feature_merge"
AUTHORISED = "authorised for this run"
MERGED = "merged"
ANSWERED = "answered"
NOT_STARTED = "not_started"

# Ready children start most urgent first; an earlier rank starts earlier.
_PRIORITY_RANK = {Priority.URGENT: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}

# The state reported for a parent whose children did not all merge, most severe first.
_PRECEDENCE = (
    RunState.FAILED,
    RunState.PROVIDER_LIMITED,
    RunState.BLOCKED,
    RunState.WAITING_FOR_SELECTION,
    RunState.WAITING_FOR_DECISION,
    RunState.WAITING_FOR_MERGE_APPROVAL,
    RunState.WAITING_FOR_CI,
)


def _start_key(child: Ticket) -> tuple[int, int]:
    """Among ready children the most urgent Priority starts first, then the lowest number."""
    return (_PRIORITY_RANK[child.priority], child.number)


def order_children(children: Sequence[Ticket]) -> list[Ticket]:
    """Put open blockers first, then the most urgent Priority and the lowest number.

    A child never starts before its open blockers, whatever its Priority: a blocker
    does not inherit the Priority of the children that wait for it. Reject dependency
    cycles.
    """
    pending = {
        child.number: child
        for child in children
        if child.status != Status.DONE and getattr(child, "state", "open") != "closed"
    }
    blockers = {
        number: set(child.blocked_by).intersection(pending) for number, child in pending.items()
    }
    dependents: dict[int, list[int]] = {number: [] for number in pending}
    for number, dependencies in blockers.items():
        for blocker in dependencies:
            dependents[blocker].append(number)

    ready = [_start_key(child) for number, child in pending.items() if not blockers[number]]
    heapify(ready)
    ordered: list[Ticket] = []
    while ready:
        _, number = heappop(ready)
        ordered.append(pending[number])
        for dependent in dependents[number]:
            blockers[dependent].remove(number)
            if not blockers[dependent]:
                heappush(ready, _start_key(pending[dependent]))

    if len(ordered) != len(pending):
        unresolved = sorted(number for number, dependencies in blockers.items() if dependencies)
        raise ValueError(f"Dependency cycle prevents ordering children: {unresolved}")
    return ordered


@dataclass(frozen=True)
class ChildPlan:
    """The open children in blocker order and what each one waits for.

    `blockers` holds a child's open sibling blockers: it is ready when all of them are
    merged into the feature branch. `outside` holds blockers that are not children of
    this parent; one that is still open blocks the child.
    """

    order: tuple[int, ...]
    blockers: dict[int, frozenset[int]]
    outside: dict[int, tuple[int, ...]]


def plan_children(children: Sequence[Ticket]) -> ChildPlan:
    """Plan the open children; a dependency cycle raises ValueError before any work."""
    ordered = order_children(children)
    known = {child.number for child in children}
    pending = {child.number for child in ordered}
    return ChildPlan(
        tuple(child.number for child in ordered),
        {child.number: frozenset(set(child.blocked_by) & pending) for child in ordered},
        {
            child.number: tuple(sorted({b for b in child.blocked_by if b not in known}))
            for child in ordered
            if any(blocker not in known for blocker in child.blocked_by)
        },
    )


@dataclass(frozen=True)
class ChildState:
    """Where one child stands after a parent run: merged, answered, a run state or not started."""

    number: int
    state: str
    run_id: int | None = None
    pr_number: int | None = None
    problems: tuple[str, ...] = ()
    next_step: str | None = None
    waits_for: tuple[int, ...] = ()


@dataclass(frozen=True)
class ParentOutcome:
    parent_run_id: int
    state: RunState
    outcomes: tuple[tuple[int, RunOutcome], ...] = ()
    problems: tuple[str, ...] = ()
    next_step: str | None = None
    children: tuple[ChildState, ...] = ()
    feature_branch: str | None = None
    feature_run_id: int | None = None
    feature_pr: int | None = None
    summary: str | None = None


def _done(store_rows: list[tuple[int, str, str, str, str | None]]) -> bool:
    return any(row[2] == "done" for row in store_rows)


def integrated(deps: EngineDeps, run_id: int) -> bool:
    """True when the run store records this child's integration merge as confirmed."""
    return _done(_operations(deps.store, run_id, INTEGRATION))


def _requested(deps: EngineDeps, run_id: int, head: str) -> bool:
    saved = _operation_by_key(deps.store, run_id, "merge_request", head)
    return saved is not None and saved[2] == "done"


class _WorkEngine(Engine):
    """Bind a child to its saved run and to the parent's feature branch."""

    def __init__(self, deps: EngineDeps, run: Run, feature: str, parent_number: int) -> None:
        super().__init__(deps)
        self.work_run = run
        self.feature = feature
        self.parent_number = parent_number

    def _find_or_create_run(self, ticket_number: int) -> Run:
        if ticket_number != self.work_run.ticket_number:
            raise ValueError("Work ticket does not match its saved run")
        return self.work_run

    async def _base_branch(self, run_id: int, ticket: Ticket) -> str:
        # Sibling blockers are the parent's concern: they are merged before this starts.
        saved = _operation_by_key(self.deps.store, run_id, "base", "base")
        if saved is not None and saved[4] is not None:
            return str(json.loads(saved[4])["branch"])
        operation = self.deps.store.begin_operation(run_id, "base", "base", {})
        self.deps.store.complete_operation(
            operation.id, {"branch": self.feature, "parent": self.parent_number}
        )
        return self.feature

    async def green(self, run_id: int) -> RunOutcome:
        """Wait for green CI on a head that is reviewed or needs no review, repairing failures."""

        async def check() -> RunOutcome:
            number = self.deps.store.get_run(run_id).ticket_number
            ticket, scope = await self._load(run_id, number)
            outcome = await self._wait_for_green(run_id, ticket, scope)
            self.deps.store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
            return replace(outcome, state=RunState.WAITING_FOR_MERGE_APPROVAL)

        return await self._guarded(run_id, check)


class _FeatureEngine(Engine):
    """Review the feature pull request, and repair accepted findings, as a child's review does.

    The parent has no implementation scope: the reviewer judges the feature head against the
    parent's decisions and the merged children, which `context` renders. CI on the feature pull
    request is never repaired; a failure stops for the developer.
    """

    def __init__(self, deps: EngineDeps, parent_ticket: Ticket, context: Callable[[], str]) -> None:
        super().__init__(deps)
        self.parent_ticket = parent_ticket
        self.context = context

    async def _load(self, run_id: int, number: int) -> tuple[Ticket, str]:
        self._choose_models(run_id, self.parent_ticket)
        return self.parent_ticket, self.context()

    async def _wait_for_green(self, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        """Wait for green CI on the feature pull request; a failure stops for the developer."""
        identity = self._saved_identity(run_id)
        pr = None if identity is None else await self.deps.github.find_pr(identity.branch)
        if identity is None or pr is None:
            raise _Stop(RunState.BLOCKED, ["No feature pull request to monitor"])
        port = self._ci_port()
        limits = self.deps.config.limits
        started = self.deps.clock()
        while True:
            self.deps.store.set_run_state(run_id, RunState.WAITING_FOR_CI)
            summary = summarize_checks(await read_checks(partial(port.checks, pr)))
            if summary.is_green:
                self.deps.store.set_run_state(run_id, RunState.RUNNING)
                head = await Git(identity.path).head_sha()
                return RunOutcome(run_id, RunState.RUNNING, pr.number, head, ci_green=True)
            if summary.failed:
                names = ", ".join(check.name for check in summary.failed)
                raise _Stop(
                    RunState.BLOCKED,
                    [f"CI failed on the feature pull request #{pr.number}: {names}"],
                )
            remaining = limits.ci_timeout_seconds - (self.deps.clock() - started).total_seconds()
            if remaining <= 0:
                raise _Stop(
                    RunState.BLOCKED,
                    [f"CI did not finish within {limits.ci_timeout_seconds} seconds"],
                )
            await self.deps.sleep(min(limits.ci_poll_interval_seconds, remaining))

    async def _diff(self, run_id: int, path: Path) -> str:
        # The feature branch started from the remote base branch, so diff against it.
        git = Git(path)
        base = f"refs/remotes/origin/{self.deps.base_branch}"
        merge_base = (await git._run("merge-base", base, "HEAD")).strip()
        return await git._run("diff", "--no-renames", "-U3", merge_base, "HEAD", "--")

    async def _agent(self, run_id: int, identity: Identity, prompt: str, scope: str) -> str:
        # A repair here fixes findings or the gate on the whole feature, never a child's scope.
        task = feature_repair_prompt(prompt, workdir=identity.path, branch=identity.branch)
        return await super()._agent(run_id, identity, task, "")


async def _review_and_decide(engine: Engine, run_id: int) -> RunOutcome:
    """Review the head, or resume its saved round, and take decisions on a terminal."""
    outcome = await engine.continue_review(run_id)
    while outcome.state == RunState.WAITING_FOR_DECISION:
        decisions = _prompt_decisions(outcome)
        if decisions is None:
            break
        outcome = await engine.decide(run_id, decisions)
    return outcome


async def run_parent(
    deps: EngineDeps,
    parent_number: int,
    children: Sequence[Ticket],
    *,
    until_green: bool = False,
    parent_locked: bool = False,
    authorise_merge: bool = False,
    by: str | None = None,
) -> ParentOutcome:
    """Run every child on the parent's feature branch, then deliver the feature branch.

    `authorise_merge` is the developer's consent, for this invocation only, to merge each
    child into the feature branch at the exact head it reaches when green and reviewed.
    It never covers the feature pull request, which always waits for explicit approval.
    """
    decided_by = (getpass.getuser() if by is None else by) if authorise_merge else ""
    if authorise_merge and not decided_by.strip():
        raise ValueError("Approval requires a non-empty authorising identity")
    store = deps.store
    repo = deps.github.repo
    with nullcontext() if parent_locked else TicketLock(deps.state_dir, repo, parent_number):
        plan = plan_children(children)
        parent = store.find_open_parent_run(repo, parent_number)
        if parent is None:
            parent = store.create_parent_run(parent_number, repo)
        work = store.list_work(parent.id)
        known = {item.ticket_number for item in work}
        position = max((item.position for item in work), default=-1) + 1
        for number in plan.order:
            if number not in known:
                store.add_work(parent.id, number, position)
                position += 1
        if not store.list_work(parent.id):
            store.set_parent_state(parent.id, RunState.COMPLETED)
            return ParentOutcome(parent.id, RunState.COMPLETED)
        store.set_parent_state(parent.id, RunState.RUNNING)
        runner = _ParentRun(deps, parent, plan, children, until_green, decided_by)
        try:
            outcome = await runner.execute()
        except (asyncio.CancelledError, KeyboardInterrupt):
            store.set_parent_state(parent.id, RunState.INTERRUPTED)
            raise
        except Exception:
            store.set_parent_state(parent.id, RunState.BLOCKED)
            raise
        store.set_parent_state(parent.id, outcome.state)
        return outcome


class _ParentRun:
    """One invocation of a parent run: the feature branch, the children and the delivery."""

    def __init__(
        self,
        deps: EngineDeps,
        parent: ParentRun,
        plan: ChildPlan,
        children: Sequence[Ticket],
        until_green: bool,
        decided_by: str,
    ) -> None:
        self.deps = deps
        self.store = deps.store
        self.parent = parent
        self.number = parent.parent_number
        self.plan = plan
        self.tickets = {child.number: child for child in children}
        self.until_green = until_green
        self.decided_by = decided_by
        self.outcomes: list[tuple[int, RunOutcome]] = []
        # Integration merges are checked and made one at a time.
        self.integration = asyncio.Lock()
        self.feature = ""
        self.feature_run: Run | None = None
        self.feature_identity: Identity | None = None
        # Questions answered and S or XS children analysed during this invocation.
        self._answered: set[int] = set()
        self._analysed: set[int] = set()

    # Feature branch -----------------------------------------------------------------------

    def _repo_lock(self) -> RepoLock:
        return self.deps.repo_lock or RepoLock(self.deps.state_dir, self.deps.repo_root)

    async def _prepare_feature(self, parent_ticket: Ticket) -> None:
        """Create the feature branch from the base branch head once, or reuse it; push it."""
        store = self.store
        engine = Engine(self.deps)
        run_id = store.get_parent_run(self.parent.id).feature_run_id
        if run_id is None:
            run = store.create_run(self.number, self.deps.github.repo.strip())
            engine._identity_operation(run.id, {"repo_root": engine._repo_root()})
            store.set_feature_run(self.parent.id, run.id)
            run_id = run.id
        identity = engine._saved_identity(run_id)
        if identity is not None and _done(_operations(store, run_id, FEATURE_MERGE)):
            # Delivered: closure may remove the branch, so never create it again.
            self.feature = identity.branch
            self.feature_run = store.get_run(run_id)
            self.feature_identity = identity
            return
        if identity is None:
            identity = Identity(
                feature_branch_name(parent_ticket),
                worktree_path(self.deps.worktrees_root, parent_ticket).resolve(),
            )
            saved = engine._identity_operation(run_id, {})
            store.complete_operation(
                saved.id, {"branch": identity.branch, "path": str(identity.path)}
            )
        marker = store.begin_operation(run_id, "feature", "feature", {"parent": self.number})
        if marker.result_json is None:
            store.complete_operation(marker.id, {"branch": identity.branch, "parent": self.number})
        git = Git(self.deps.repo_root)
        async with self._repo_lock():
            await git.fetch()
            if not await _is_worktree(self.deps, identity):
                identity.path.parent.mkdir(parents=True, exist_ok=True)
                local = await git._run(
                    "for-each-ref", "--format=%(refname)", f"refs/heads/{identity.branch}"
                )
                remote = await git._run(
                    "for-each-ref", "--format=%(refname)", f"refs/remotes/origin/{identity.branch}"
                )
                if local.strip():
                    await git._run("worktree", "add", "--", str(identity.path), identity.branch)
                elif remote.strip():
                    await git.create_worktree(
                        identity.path, identity.branch, f"refs/remotes/origin/{identity.branch}"
                    )
                else:
                    await git.create_worktree(
                        identity.path,
                        identity.branch,
                        f"refs/remotes/origin/{self.deps.base_branch}",
                    )
            published = await git._run(
                "ls-remote", "--heads", "origin", f"refs/heads/{identity.branch}"
            )
            if not published.strip():
                await Git(identity.path).push(identity.branch)
                await git.fetch()
        self.feature = identity.branch
        self.feature_run = store.get_run(run_id)
        self.feature_identity = identity
        await self._read_feature_code(identity)

    async def _read_feature_code(self, identity: Identity) -> None:
        """Point analysis and scoping at the feature branch, not at the base branch checkout.

        Children build on the feature branch, so the code they are analysed against is the
        feature branch with every merged sibling; the repository checkout holds the base branch.
        """
        if await self._refresh_feature_code(identity):
            self.deps = replace(self.deps, code_root=identity.path)

    async def _refresh_feature_code(self, identity: Identity) -> bool:
        """Fast-forward the feature worktree to the remote head; False when it cannot be."""
        git = Git(self.deps.repo_root)
        async with self._repo_lock():
            await git.fetch()
            try:
                await Git(identity.path)._run(
                    "merge", "--ff-only", f"refs/remotes/origin/{identity.branch}"
                )
            except (OSError, RuntimeError, ValueError):
                return False
        return True

    async def _conflicts_with_feature(self, git: Git, head: str) -> bool:
        """True when merging the feature branch into this head would conflict."""
        feature_head = await self._feature_head()
        if await Engine._is_ancestor(git, feature_head, head):
            return False
        try:
            await git._run("merge-tree", "--write-tree", head, feature_head)
        except GitError:
            return True
        return False

    async def _feature_head(self) -> str:
        git = Git(self.deps.repo_root)
        async with self._repo_lock():
            await git.fetch()
        return await git.head_sha(f"refs/remotes/origin/{self.feature}")

    # Children -----------------------------------------------------------------------------

    def _satisfied(self, number: int) -> bool:
        item = next(
            (item for item in self.store.list_work(self.parent.id) if item.ticket_number == number),
            None,
        )
        if item is None or item.run_id is None:
            return False
        return integrated(self.deps, item.run_id) or _done(
            [
                row
                for row in _operations(self.store, item.run_id, "parent_work")
                if row[1] == ANSWERED
            ]
        )

    async def _outside_open(self, number: int) -> tuple[int, ...]:
        """Blockers outside this parent that are still open and not Done."""
        still: list[int] = []
        for blocker in self.plan.outside.get(number, ()):
            try:
                ticket = await self.deps.github.get_ticket(blocker)
            except Exception:
                still.append(blocker)
                continue
            if ticket.status != Status.DONE and getattr(ticket, "state", "open") != "closed":
                still.append(blocker)
        return tuple(still)

    async def _child(self, number: int) -> ChildState:
        """Work one child to its integration merge; failures stay with this child."""
        run_id: int | None = None
        try:
            with TicketLock(self.deps.state_dir, self.deps.github.repo, number):
                ticket = await self.deps.github.get_ticket(number)
                run = self._work_run(number)
                run_id = run.id
                return await self._work_child(ticket, run)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            if run_id is not None:
                self.store.set_run_state(run_id, RunState.BLOCKED)
            return ChildState(number, RunState.BLOCKED, run_id, problems=(f"#{number}: {error}",))

    def _work_run(self, number: int) -> Run:
        """The child's saved run, or a new one recorded on its work item."""
        item = next(
            item for item in self.store.list_work(self.parent.id) if item.ticket_number == number
        )
        run = (
            self.store.get_run(item.run_id)
            if item.run_id is not None
            else Engine(self.deps)._find_or_create_run(number)
        )
        self.store.set_work_run(self.parent.id, number, run.id)
        return run

    def _state(self, number: int, outcome: RunOutcome) -> ChildState:
        if outcome.state == RunState.COMPLETED and integrated(self.deps, outcome.run_id):
            return ChildState(number, MERGED, outcome.run_id, outcome.pr_number)
        next_step: str | None = None
        if outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL:
            next_step = (
                f"fugue approval record {outcome.run_id} --reason 'reason text', "
                f"then fugue run {self.number}"
            )
        elif outcome.state == RunState.WAITING_FOR_SELECTION:
            next_step = f"fugue question {number}"
        elif outcome.state == RunState.WAITING_FOR_DECISION:
            next_step = f"fugue run {self.number} on a terminal to decide the findings"
        return ChildState(
            number,
            outcome.state,
            outcome.run_id,
            outcome.pr_number,
            tuple(f"#{number}: {problem}" for problem in outcome.problems),
            next_step,
        )

    async def _work_child(self, ticket: Ticket, run: Run) -> ChildState:
        from fugue.analysis.batch import batchable
        from fugue.analysis.stage import analyse

        store = self.store
        number = ticket.number
        if integrated(self.deps, run.id):
            return ChildState(number, MERGED, run.id)
        # A sibling may have merged since this invocation started: analysis and scoping must
        # read the feature branch with it, not the head the run began with.
        if (
            self.feature_identity is not None
            and self.deps.code_root is not None
            and not await self._refresh_feature_code(self.feature_identity)
        ):
            raise _Stop(
                RunState.BLOCKED,
                [f"The feature worktree {self.feature_identity.path} could not be updated"],
            )
        if ticket.issue_type == IssueType.QUESTION:
            if not selected_solution(ticket):
                store.set_run_state(run.id, RunState.WAITING_FOR_DECISION)
                return ChildState(
                    number,
                    RunState.WAITING_FOR_DECISION,
                    run.id,
                    problems=(f"Question #{number} requires an answer",),
                    next_step=f"fugue question {number}",
                )
            answered = store.begin_operation(run.id, "parent_work", ANSWERED, {})
            store.complete_operation(answered.id, {})
            store.set_run_state(run.id, RunState.COMPLETED)
            return ChildState(number, ANSWERED, run.id)
        # An M or L is analysed here by its own agent; an S or XS was analysed in an analysis
        # batch and comes here unselected only when that left it without a selection.
        if (ticket.size in {Size.M, Size.L} and not selected_solution(ticket)) or batchable(
            ticket, self.deps.config.policy
        ):
            outcome = await analyse(self.deps, ticket, run_id=run.id)
            self.outcomes.append((number, outcome))
            if outcome.state != RunState.RUNNING:
                return self._state(number, outcome)
        engine = _WorkEngine(self.deps, run, self.feature, self.number)
        delivered = store.begin_operation(run.id, "parent_work", "delivered", {})
        skip_ci = delivered.status != "done" and await self._conflicting_pull_request(
            engine, run.id
        )
        if skip_ci:
            # GitHub starts no checks for a conflicting pull request, so waiting for CI would
            # never end: go to the integration step, which brings the feature branch in first.
            store.complete_operation(delivered.id, {})
        elif delivered.status != "done":
            if run.state != RunState.WAITING_FOR_CI:
                outcome = await (
                    engine.run_to_green(number) if self.until_green else engine.run_to_pr(number)
                )
                if not (
                    outcome.state == RunState.WAITING_FOR_CI
                    or (outcome.state == RunState.RUNNING and outcome.ci_green)
                ):
                    self.outcomes.append((number, outcome))
                    return self._state(number, outcome)
            store.complete_operation(delivered.id, {})
        outcome = await self._integrate(engine, run.id, ticket)
        self.outcomes.append((number, outcome))
        return self._state(number, outcome)

    async def _conflicting_pull_request(self, engine: _WorkEngine, run_id: int) -> bool:
        """True when this run already has a pull request whose head conflicts with the feature."""
        if not any(row[2] == "done" for row in _operations(self.store, run_id, "pr")):
            return False
        identity = engine._saved_identity(run_id)
        if identity is None or not identity.path.exists():
            return False
        git = Git(identity.path)
        return await self._conflicts_with_feature(git, await git.head_sha())

    async def _integrate(self, engine: _WorkEngine, run_id: int, ticket: Ticket) -> RunOutcome:
        """Review, update from the feature branch when it moved, approve and merge."""
        while True:
            identity = engine._saved_identity(run_id)
            if identity is None:
                raise _Stop(RunState.BLOCKED, ["No saved worktree for this run"])
            git = Git(identity.path)
            head = await git.head_sha()
            if await self._merging(engine, run_id):
                # Reconcile a merge whose confirmation was lost before anything else.
                return await self._approve_and_merge(engine, run_id, head, merged=True)
            pending = [
                row for row in _operations(self.store, run_id, "feature_update") if row[2] != "done"
            ]
            if pending:
                # Finish an update whose gate or push did not complete before reviewing it.
                updated = await self._update(engine, run_id, ticket, identity, pending[-1][1])
                if updated.state != RunState.RUNNING:
                    return updated
                continue
            if await self._conflicts_with_feature(git, head):
                # GitHub starts no checks for a conflicting pull request, so waiting for CI
                # would never end. Bring the feature branch in and resolve the conflict first.
                updated = await self._update(
                    engine, run_id, ticket, identity, await self._feature_head()
                )
                if updated.state != RunState.RUNNING:
                    return updated
                continue
            # A child whose Size rule needs no review (XS, S by default) goes straight to CI.
            if not review_required(ticket.size, self.deps.config.policy) or review_clean(
                self.store, run_id, head
            ):
                outcome = await engine.green(run_id)
            else:
                outcome = await _review_and_decide(engine, run_id)
            if outcome.state != RunState.WAITING_FOR_MERGE_APPROVAL:
                return outcome
            if await git.head_sha() != head:
                continue  # A repair moved the head; check its review and CI again.
            async with self.integration:
                feature_head = await self._feature_head()
                if await Engine._is_ancestor(git, feature_head, head):
                    return await self._approve_and_merge(engine, run_id, head)
            updated = await self._update(engine, run_id, ticket, identity, feature_head)
            if updated.state != RunState.RUNNING:
                return updated

    async def _merging(self, engine: _WorkEngine, run_id: int) -> bool:
        """True when an integration merge was started or the pull request is already merged."""
        if any(row[2] != "done" for row in _operations(self.store, run_id, INTEGRATION)):
            return True
        requests = _operations(self.store, run_id, "merge_request")
        if not requests:
            return False
        saved = json.loads(requests[-1][3])
        state = await engine._merge_port().pr_state(PullRequest(saved["number"], saved["url"]))
        return state.state == "MERGED"

    async def _approve_and_merge(
        self, engine: _WorkEngine, run_id: int, head: str, *, merged: bool = False
    ) -> RunOutcome:
        """Merge an up-to-date child at its approved head with a merge commit."""
        store = self.store

        async def work() -> RunOutcome:
            ticket, _, pr = await engine._merge_context(run_id)
            approval = store.valid_approval(run_id, "merge", str(pr.number), head)
            if approval is None and not merged:
                if not _requested(self.deps, run_id, head):
                    requested = await engine.request_approval(run_id)
                    if requested.state != RunState.WAITING_FOR_MERGE_APPROVAL:
                        return requested
                if not self.decided_by:
                    store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
                    return RunOutcome(
                        run_id, RunState.WAITING_FOR_MERGE_APPROVAL, pr.number, head, ci_green=True
                    )
                try:
                    await engine.record_approval(run_id, self.decided_by, AUTHORISED)
                except ValueError as error:
                    raise _Stop(RunState.BLOCKED, [str(error)]) from error
            pr, merged_head, merge_sha = await engine.merge_approved(run_id, INTEGRATION)
            await engine._mark_merged(run_id, ticket.number, pr, self.feature, merge_sha)
            store.set_run_state(run_id, RunState.COMPLETED)
            return RunOutcome(
                run_id,
                RunState.COMPLETED,
                pr.number,
                merged_head,
                ci_green=True,
                notes=(f"Merged into {self.feature} as {merge_sha}",),
            )

        return await engine._guarded(run_id, work)

    async def _update(
        self,
        engine: _WorkEngine,
        run_id: int,
        ticket: Ticket,
        identity: Identity,
        feature_head: str,
    ) -> RunOutcome:
        """Merge the moved feature branch into the child, then gate and push it.

        A merge, not a rebase, keeps the reviewed commits. A conflict stops this child;
        its review and CI run again on the new head before it can be merged.
        """
        store = self.store

        async def work() -> RunOutcome:
            git = Git(identity.path)
            operation = store.begin_operation(
                run_id, "feature_update", feature_head, {"feature": self.feature}
            )
            if await self._merge_open(git) and not await self._resolve_conflicts(
                engine, run_id, identity, resume=True
            ):
                raise _Stop(
                    RunState.BLOCKED,
                    [f"The open merge of {self.feature} in {identity.path} cannot be concluded"],
                )
            if not await Engine._is_ancestor(git, feature_head, "HEAD"):
                if await git.status_porcelain():
                    raise _Stop(
                        RunState.BLOCKED,
                        [f"The worktree {identity.path} must be clean to merge {self.feature}"],
                    )
                message = CommitMessage(
                    ticket.issue_type,
                    ticket.number,
                    "Merge the feature branch",
                    f"Bring in the work merged into {self.feature} since this branch started.",
                )
                try:
                    await git.merge_ref(f"refs/remotes/origin/{self.feature}", message.render())
                except GitError as error:
                    if not await self._resolve_conflicts(engine, run_id, identity):
                        with suppress(GitError):
                            await git.abort_merge()
                        detail = error.stderr.strip().splitlines()
                        raise _Stop(
                            RunState.BLOCKED,
                            [
                                f"{identity.branch} conflicts with {self.feature}; merge "
                                f"{self.feature} into it in {identity.path}, commit, then run "
                                f"fugue run {self.number} again"
                                + (f": {detail[-1]}" if detail else "")
                            ],
                        ) from error
            # A failing gate goes to the agent as a repair; it only blocks at the repair limit.
            while not await engine._verify(run_id, identity, ""):
                pass
            await engine._commit(run_id, ticket, "", identity)
            await engine._stage(run_id, Stage.DELIVER, partial(engine._push, run_id, identity))
            store.complete_operation(operation.id, {"head": await git.head_sha()})
            return RunOutcome(run_id, RunState.RUNNING)

        return await engine._guarded(run_id, work)

    @staticmethod
    async def _merge_open(git: Git) -> bool:
        try:
            await git._run("rev-parse", "-q", "--verify", "MERGE_HEAD")
        except GitError:
            return False
        return True

    async def _resolve_conflicts(
        self, engine: _WorkEngine, run_id: int, identity: Identity, *, resume: bool = False
    ) -> bool:
        """Let the implementer resolve an open merge once; True when the merge is committed.

        Any failure leaves the merge open for the caller to abort and report.
        """
        git = Git(identity.path)
        try:
            files = await git.conflicted_files()
            if not files:
                return False
            # A merge left open by an earlier run keeps the agent's resolution; conclude it
            # without asking again when no marker is left.
            remaining = files
            if resume:
                remaining = [p for p in files if _has_conflict_markers(identity.path / p)]
            attempts = 0
            while remaining and attempts < 3:
                attempts += 1
                prompt = merge_conflict_prompt(
                    self.feature, remaining, workdir=identity.path, branch=identity.branch
                )
                await engine._stage(
                    run_id, Stage.REPAIR, partial(engine._agent, run_id, identity, prompt, "")
                )
                remaining = [p for p in files if _has_conflict_markers(identity.path / p)]
            if remaining:
                return False
            # The agent may change any file the result needs, so stage all of it.
            await git._run("add", "-A")
            if protected_paths(await git.staged_files()) or await git.conflicted_files():
                return False
            await git.conclude_merge()
        except (GitError, OSError, _Stop):
            return False
        return True

    # Scheduling ---------------------------------------------------------------------------

    async def _run_children(self) -> dict[int, ChildState]:
        """Start each child once its blockers are merged, analysing just in time.

        Nothing is analysed before its blockers are merged into the feature branch: an M
        Question is answered, and the ready S and XS tickets are analysed in analysis
        batches, only when they are ready, so their analysis reads code that exists.
        """
        limit = self.deps.config.limits.parallel_children
        results: dict[int, ChildState] = {}
        satisfied: set[int] = set()
        seen: set[int] = set()
        running: dict[asyncio.Future[ChildState], int] = {}
        try:
            while True:
                await self._check_new(seen, satisfied, results)
                busy = set(results) | set(running.values())
                if await self._answer_questions(satisfied, busy):
                    continue  # Its children joined the plan; check them before starting any.
                await self._analyse_ready(satisfied, results, running)
                for number in self.plan.order:
                    if len(running) >= limit:
                        break
                    if number in satisfied or number in results or number in running.values():
                        continue
                    if self.plan.blockers[number] <= satisfied:
                        running[asyncio.ensure_future(self._child(number))] = number
                if not running:
                    break
                finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    number = running.pop(task)
                    state = task.result()
                    results[number] = state
                    if state.state in (MERGED, ANSWERED):
                        satisfied.add(number)
        finally:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
        for number in self.plan.order:
            if number in satisfied and number not in results:
                results[number] = ChildState(number, MERGED, self._run_id(number))
            elif number not in results:
                waits = tuple(sorted(self.plan.blockers[number] - satisfied))
                results[number] = ChildState(
                    number, NOT_STARTED, self._run_id(number), waits_for=waits
                )
        return results

    async def _check_new(
        self, seen: set[int], satisfied: set[int], results: dict[int, ChildState]
    ) -> None:
        """Note each newly planned child that is already merged or answered, or blocked outside."""
        for number in self.plan.order:
            if number in seen:
                continue
            seen.add(number)
            if self._satisfied(number):
                satisfied.add(number)
                continue
            outside = await self._outside_open(number)
            if outside:
                listed = ", ".join(f"#{blocker}" for blocker in outside)
                results[number] = ChildState(
                    number,
                    RunState.BLOCKED,
                    problems=(
                        f"#{number}: blocked by {listed}, not a child of #{self.number} "
                        "and still open",
                    ),
                    waits_for=outside,
                )

    async def _analyse_ready(
        self,
        satisfied: set[int],
        results: dict[int, ChildState],
        running: dict[asyncio.Future[ChildState], int],
    ) -> None:
        """Analyse the ready S and XS children in analysis batches, most urgent first.

        A child is ready when its sibling blockers are merged; one blocked outside the parent
        is already in `results`. The ready children are taken by Priority, then number, and
        cut into batches of `limits.analysis_batch_size`.
        """
        from fugue.analysis.batch import batchable

        policy = self.deps.config.policy
        busy = set(results) | set(running.values()) | satisfied | self._analysed
        ready = sorted(
            (
                number
                for number in self.plan.order
                if number not in busy
                and self.plan.blockers[number] <= satisfied
                and batchable(self.tickets[number], policy)
            ),
            key=lambda number: _start_key(self.tickets[number]),
        )
        if not ready:
            return
        if (
            self.feature_identity is not None
            and self.deps.code_root is not None
            and not await self._refresh_feature_code(self.feature_identity)
        ):
            return  # Each child reports the stale feature worktree when it starts.
        size = self.deps.config.limits.analysis_batch_size
        for first in range(0, len(ready), size):
            await self._analyse_batch(ready[first : first + size], results)

    async def _analyse_batch(self, numbers: Sequence[int], results: dict[int, ChildState]) -> None:
        """Analyse one batch, then publish and select each ticket on its own.

        A ticket the batch left without a valid handoff is analysed again alone by `analyse`.
        A ticket that does not go on (a fired trigger parks it) is recorded in `results`;
        the others start as children with their selection made.
        """
        from fugue.analysis.batch import BatchOutcome, analyse_batch, batchable
        from fugue.analysis.stage import analyse, has_handoff

        policy = self.deps.config.policy
        members: list[tuple[Ticket, int]] = []
        for number in numbers:
            self._analysed.add(number)
            try:
                ticket = await self.deps.github.get_ticket(number)
            except Exception as error:
                results[number] = ChildState(
                    number, RunState.BLOCKED, problems=(f"#{number}: {error}",)
                )
                continue
            if batchable(ticket, policy):
                members.append((ticket, self._work_run(number).id))
        asked = [
            (ticket, run_id) for ticket, run_id in members if not has_handoff(self.deps, run_id)
        ]
        try:
            batch = await analyse_batch(self.deps, asked)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            batch = BatchOutcome(state=RunState.BLOCKED, problem=str(error))
        stopped = {ticket.number for ticket, _ in asked} if batch.state is not None else set()
        for ticket, run_id in members:
            number = ticket.number
            if number in stopped and batch.state is not None:
                self.store.set_run_state(run_id, batch.state)
                results[number] = ChildState(
                    number, batch.state, run_id, problems=(f"#{number}: {batch.problem}",)
                )
                continue
            with TicketLock(self.deps.state_dir, self.deps.github.repo, number):
                outcome = await analyse(self.deps, ticket, run_id=run_id)
            self.outcomes.append((number, outcome))
            if outcome.state != RunState.RUNNING:
                results[number] = self._state(number, outcome)

    def _run_id(self, number: int) -> int | None:
        return next(
            (
                item.run_id
                for item in self.store.list_work(self.parent.id)
                if item.ticket_number == number
            ),
            None,
        )

    async def _answer_questions(
        self, satisfied: Collection[int] = frozenset(), busy: Collection[int] = ()
    ) -> bool:
        """Answer each ready M Question among the children and take its children into this run.

        A Question is ready when its sibling blockers are merged (`satisfied`); one with an
        open blocker waits for it and is answered by a later call, so its analysis reads code
        that exists. `busy` holds children already started, finished or blocked outside.
        A Question that selects by policy answers itself and creates its children, which then
        run on this feature branch beside their parent's siblings. A Question that stops, for
        example on a fired trigger, stays as it is and reports that it needs an answer.
        True when the plan gained children.
        """
        from fugue.questions.automatic import answer_question, is_automatic

        reader = getattr(self.deps.github, "children", None)
        policy = self.deps.config.policy
        joined = False
        while True:
            pending = [
                ticket
                for number, ticket in sorted(self.tickets.items())
                if number not in self._answered
                and number not in busy
                and is_automatic(ticket, policy)
                and self.plan.blockers.get(number, frozenset()) <= set(satisfied)
            ]
            if not pending or reader is None:
                return joined
            for question in pending:
                self._answered.add(question.number)
                with TicketLock(self.deps.state_dir, self.deps.github.repo, question.number):
                    outcome = await answer_question(self.deps, question)
                self.outcomes.append((question.number, outcome))
                if outcome.state != RunState.RUNNING:
                    continue
                for child in await reader(question.number):
                    self.tickets.setdefault(child.number, child)
            planned = set(self.plan.order)
            self.plan = plan_children(list(self.tickets.values()))
            joined = joined or not set(self.plan.order) <= planned
            known = {item.ticket_number for item in self.store.list_work(self.parent.id)}
            position = max((i.position for i in self.store.list_work(self.parent.id)), default=-1)
            for number in self.plan.order:
                if number not in known:
                    position += 1
                    self.store.add_work(self.parent.id, number, position)

    async def execute(self) -> ParentOutcome:
        parent_ticket = await self.deps.github.get_ticket(self.number)
        await self._prepare_feature(parent_ticket)
        results = await self._run_children()
        # A child closed on merge is no longer open, yet it still counts as merged here.
        listed = set(self.plan.order)
        closed = tuple(
            ChildState(item.ticket_number, MERGED, item.run_id)
            for item in self.store.list_work(self.parent.id)
            if item.ticket_number not in listed
            and item.run_id is not None
            and _done(_operations(self.store, item.run_id, INTEGRATION))
        )
        children = closed + tuple(results[number] for number in self.plan.order)
        assert self.feature_run is not None
        unfinished = [child for child in children if child.state not in (MERGED, ANSWERED)]
        if unfinished:
            states = {child.state for child in unfinished}
            state = next((item for item in _PRECEDENCE if item in states), RunState.BLOCKED)
            next_step = next(
                (child.next_step for child in unfinished if child.next_step is not None), None
            )
            return self._outcome(
                state,
                children,
                tuple(problem for child in unfinished for problem in child.problems),
                next_step,
            )
        delivered = await self._deliver(parent_ticket)
        next_step = None
        if delivered.state == RunState.WAITING_FOR_MERGE_APPROVAL:
            next_step = (
                f"fugue approval record {self.feature_run.id} --reason 'reason text', "
                f"then fugue run {self.number}"
            )
        elif delivered.state == RunState.WAITING_FOR_DECISION:
            next_step = f"fugue run {self.number} on a terminal to decide the findings"
        return self._outcome(
            delivered.state, children, delivered.problems, next_step, delivered.pr_number
        )

    def _outcome(
        self,
        state: RunState,
        children: tuple[ChildState, ...],
        problems: tuple[str, ...],
        next_step: str | None,
        feature_pr: int | None = None,
    ) -> ParentOutcome:
        from fugue.cli.render import render_parent_summary

        assert self.feature_run is not None
        if feature_pr is None:
            saved = _operation_by_key(self.store, self.feature_run.id, "feature_pr", "pr")
            feature_pr = (
                None if saved is None or saved[4] is None else json.loads(saved[4])["number"]
            )
        summary = render_parent_summary(
            self.feature,
            children,
            self.feature_run.id,
            feature_pr,
            state if not any(c.state not in (MERGED, ANSWERED) for c in children) else None,
        )
        return ParentOutcome(
            self.parent.id,
            state,
            tuple(self.outcomes),
            problems,
            next_step,
            children,
            self.feature,
            self.feature_run.id,
            feature_pr,
            summary,
        )

    # Delivery -----------------------------------------------------------------------------

    async def _deliver(self, parent_ticket: Ticket) -> RunOutcome:
        """Open the feature pull request, wait for CI, review it, wait for approval, then merge.

        The feature review runs once CI is green, at the exact feature head; a saved round at
        that head is reused, and a new commit needs a new round before approval is requested.
        """
        assert self.feature_run is not None and self.feature_identity is not None
        run_id = self.feature_run.id
        identity = self.feature_identity
        store = self.store
        engine = _FeatureEngine(
            self.deps, parent_ticket, partial(self._feature_context, parent_ticket)
        )
        if store.get_run(run_id).state == RunState.COMPLETED:
            return RunOutcome(run_id, RunState.COMPLETED)

        async def work() -> RunOutcome:
            # A started merge is only confirmed: its response may have been lost.
            if not _operations(store, run_id, FEATURE_MERGE):
                while True:
                    await self._sync_feature(identity)
                    pr = await self._feature_pr(engine, run_id, parent_ticket)
                    head = await Git(identity.path).head_sha()
                    await engine._wait_for_green(run_id, parent_ticket, "")
                    if not review_required(
                        parent_ticket.size, self.deps.config.policy, feature=True
                    ) or review_clean(store, run_id, head):
                        break
                    reviewed = await _review_and_decide(engine, run_id)
                    if reviewed.state != RunState.WAITING_FOR_MERGE_APPROVAL:
                        return reviewed
                    # Clean now, or a repair moved the head: check CI and review again.
                if store.valid_approval(run_id, "merge", str(pr.number), head) is None:
                    if not _requested(self.deps, run_id, head):
                        requested = await engine.request_approval(run_id)
                        if requested.state != RunState.WAITING_FOR_MERGE_APPROVAL:
                            return requested
                    store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
                    return RunOutcome(
                        run_id, RunState.WAITING_FOR_MERGE_APPROVAL, pr.number, head, ci_green=True
                    )
            pr, head, merge_sha = await engine.merge_approved(run_id, FEATURE_MERGE)
            notes = await self._close_children(pr, merge_sha)
            notes += await engine._stage(
                run_id, Stage.CLOSE, lambda: engine._clean_up(run_id, identity, head)
            )
            store.set_run_state(run_id, RunState.COMPLETED)
            return RunOutcome(
                run_id, RunState.COMPLETED, pr.number, head, ci_green=True, notes=tuple(notes)
            )

        return await engine._guarded(run_id, work)

    async def _sync_feature(self, identity: Identity) -> None:
        """Bring the feature worktree to the remote head that holds every merged child."""
        remote = await self._feature_head()
        git = Git(identity.path)
        if await git.head_sha() == remote:
            return
        if await git.status_porcelain():
            raise _Stop(RunState.BLOCKED, [f"The worktree {identity.path} must be clean"])
        try:
            await git._run("merge", "--ff-only", f"refs/remotes/origin/{self.feature}")
        except GitError as error:
            raise _Stop(
                RunState.BLOCKED,
                [f"The local {self.feature} has commits that are not on origin; resolve it"],
            ) from error

    async def _feature_pr(self, engine: Engine, run_id: int, parent_ticket: Ticket) -> PullRequest:
        saved = _operation_by_key(self.store, run_id, "feature_pr", "pr")
        if saved is not None and saved[4] is not None:
            data = json.loads(saved[4])
            return PullRequest(data["number"], data["url"])
        found = await self.deps.github.find_pr(self.feature)
        if found is not None:
            return found
        title = f"{parent_ticket.issue_type}[#{parent_ticket.number}]: {explanation(parent_ticket)}"
        body = self._feature_body(parent_ticket)
        operation = self.store.begin_operation(
            run_id, "feature_pr", "pr", {"branch": self.feature, "base": self.deps.base_branch}
        )
        pr = await self.deps.github.create_pr(
            self.feature,
            self.deps.base_branch,
            title,
            body,
            ",".join(parent_ticket.assignees) or "Bojan-Ivanovski",
            parent_ticket.milestone or "0.1.0",
            "Fugue",
        )
        self.store.complete_operation(operation.id, {"number": pr.number, "url": pr.url})
        return pr

    def _feature_body(self, parent_ticket: Ticket) -> str:
        lines: list[str] = []
        closes: list[str] = []
        for item in self.store.list_work(self.parent.id):
            if item.run_id is None:
                continue
            ticket = self.tickets.get(item.ticket_number)
            title = ticket.title if ticket is not None else ""
            rows = [row for row in _operations(self.store, item.run_id, INTEGRATION) if row[4]]
            if not rows:
                continue
            number = json.loads(rows[-1][3])["number"]
            merge_sha = json.loads(rows[-1][4] or "{}").get("merge_sha")
            lines.append(
                f"- #{item.ticket_number} {title}: pull request #{number} merged into "
                f"`{self.feature}` as {merge_sha}"
            )
            closes.append(f"Closes #{item.ticket_number}")
        summary = parent_ticket.body_sections.get("Summary", "").strip() or (
            f"Deliver the children of #{parent_ticket.number}."
        )
        assignee = ", ".join(parent_ticket.assignees) or "Bojan-Ivanovski"
        return (
            f"## Summary\n\n{summary}\n\n## Changes\n\n"
            + "\n".join(lines)
            + "\n\n## Related work\n\n"
            + "\n".join([*closes, f"Part of #{parent_ticket.number}"])
            + f"\n\n## Metadata\n\nMilestone: {parent_ticket.milestone or '0.1.0'}\n"
            + f"Assignee: {assignee}\nProject: Fugue"
            + "\n\n## Verification\n\n"
            + "- Each child passed the gate, CI and the review its Size requires on the head "
            "merged into the feature branch.\n"
            + "- CI runs on this pull request; "
            + (
                "the whole feature head is reviewed, and "
                if self.deps.config.policy.feature_review
                else ""
            )
            + "the gate runs again on the feature head before approval is requested.\n\n"
            "## Risks and follow-up\n\n"
            + "Merge requires the developer's approval for the exact head SHA; the run "
            "never authorises it."
        )

    def _feature_context(self, parent_ticket: Ticket) -> str:
        """What the feature review judges against: the parent's decisions and merged children."""
        number = parent_ticket.number
        parts = [
            f"The feature pull request delivers every child of #{number} "
            f"({parent_ticket.title}) from `{self.feature}` to {self.deps.base_branch}. Judge "
            f"the whole feature head against the decisions of #{number} and the merged children "
            "below, with each child's scenarios."
        ]
        for name in ("Summary", "Scope", "Analysis", "Solution"):
            text = parent_ticket.body_sections.get(name, "").strip()
            if text:
                parts.append(f"## {name} of #{number}\n\n{text}")
        children: list[str] = []
        for item in self.store.list_work(self.parent.id):
            if item.run_id is None:
                continue
            ticket = self.tickets.get(item.ticket_number)
            title = f"#{item.ticket_number} {ticket.title if ticket is not None else ''}".strip()
            rows = [row for row in _operations(self.store, item.run_id, INTEGRATION) if row[4]]
            if rows:
                pr = json.loads(rows[-1][3])["number"]
                merge_sha = json.loads(rows[-1][4] or "{}").get("merge_sha")
                outcome = f"pull request #{pr} merged into `{self.feature}` as {merge_sha}"
            elif self._satisfied(item.ticket_number):
                outcome = "answered; its children are listed here"
            else:
                continue
            entry = [f"### {title}", f"Outcome: {outcome}."]
            if ticket is not None and ticket.body_sections.get("Summary", "").strip():
                entry.append(f"Summary: {ticket.body_sections['Summary'].strip()}")
            scopes = [
                artifact
                for artifact in self.store.list_artifacts(item.run_id)
                if artifact.name.startswith("scope-")
            ]
            if scopes:
                with suppress(OSError, UnicodeError):
                    text = Path(scopes[-1].path).read_text(encoding="utf-8")
                    text = without_orchestrator_notes(text).strip()
                    entry.append(f"Scope and scenarios:\n\n{text}")
            children.append("\n\n".join(entry))
        parts.append("## Merged children\n\n" + ("\n\n".join(children) or "None."))
        return "\n\n".join(parts)

    async def _close_children(self, feature_pr: PullRequest, merge_sha: str) -> list[str]:
        """Close every merged child once the feature pull request has merged."""
        notes: list[str] = []
        for item in self.store.list_work(self.parent.id):
            if item.run_id is None:
                continue
            rows = [
                row for row in _operations(self.store, item.run_id, INTEGRATION) if row[2] == "done"
            ]
            if not rows:
                continue  # An answered Question has nothing to close here.
            intent = json.loads(rows[-1][3])
            merged = json.loads(rows[-1][4] or "{}").get("merge_sha")
            child = Engine(self.deps)
            identity = child._saved_identity(item.run_id)
            if identity is None:
                raise _Stop(RunState.BLOCKED, [f"#{item.ticket_number} has no saved worktree"])
            approval = self.store.valid_approval(
                item.run_id, "merge", str(intent["number"]), intent["head"]
            )
            body = (
                f"Merged into `{self.feature}` as {merged} by pull request #{intent['number']}, "
                f"then delivered to {self.deps.base_branch} by #{feature_pr.number} as "
                f"{merge_sha}."
            )
            if approval is not None:
                body += (
                    f" Approval #{approval.id}: {approval.decided_by} approved "
                    f"{approval.head_sha}. Reason: {approval.reason}. "
                    f"Recorded: {approval.created_at.isoformat()}."
                )
            with TicketLock(self.deps.state_dir, self.deps.github.repo, item.ticket_number):
                ticket = await self.deps.github.get_ticket(item.ticket_number)
                port = child._merge_port()
                problems = can_close(
                    ticket, await port.children(ticket.number), disposition_set=True
                )
                if problems:
                    raise _Stop(RunState.BLOCKED, problems)
                run_id = item.run_id
                notes += await child._stage(
                    run_id,
                    Stage.CLOSE,
                    partial(child._close_ticket, run_id, ticket, identity, intent["head"], body),
                )
                self.store.set_run_state(run_id, RunState.COMPLETED)
        return notes


def _prompt_decisions(outcome: RunOutcome) -> dict[int, Decision] | None:
    """Collect a complete round of decisions, leaving cancellation pending."""
    from fugue.cli.choices import make_chooser

    if not sys.stdin.isatty():
        return None
    chooser = make_chooser()
    decisions: dict[int, Decision] = {}
    for finding in outcome.findings:
        choice = chooser.choose(
            f"{finding.index}. {'Blocking' if finding.blocking else 'Non-blocking'}: "
            f"{finding.summary} ({finding.location or 'no location'})",
            ("Accept", "Reject with a reason", "Defer"),
            default=2,
        )
        if choice is None:
            return None
        if choice == 0:
            decisions[finding.index] = Decision(True)
        elif choice == 1:
            try:
                reason = input("Reason for rejecting: ").strip()
            except EOFError:
                return None
            if not reason:
                return None
            decisions[finding.index] = Decision(False, reason=reason)
        elif finding.blocking:
            return None
        else:
            try:
                number = int(input("Defer to ticket number: "))
            except (EOFError, ValueError):
                return None
            if number <= 0:
                return None
            decisions[finding.index] = Decision(False, deferred_to=number)
    return decisions


def _has_conflict_markers(path: Path) -> bool:
    """True when a file still holds a conflict marker line."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False  # A file the resolution deleted holds no marker.
    return any(line.startswith(("<<<<<<< ", ">>>>>>> ")) for line in text.splitlines())
