"""Run the ordered children of a parent as sequential stack layers."""

import asyncio
import json
import sys
from collections.abc import Sequence
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from functools import partial
from heapq import heapify, heappop, heappush

from fugue.engine.facts import selected_solution
from fugue.engine.locks import RepoLock, TicketLock
from fugue.engine.pipeline import Engine, EngineDeps, Identity, RunOutcome, _ticket_base
from fugue.engine.review import Decision
from fugue.github import GitHubError
from fugue.github.stack import lower_branch
from fugue.gitops import Git
from fugue.models import IssueType, Size, Status, Ticket
from fugue.store import Operation, Run
from fugue.workflow import RunState, Stage


def order_children(children: Sequence[Ticket]) -> list[Ticket]:
    """Put open blockers first, breaking ties by number; reject dependency cycles."""
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

    ready = [number for number, dependencies in blockers.items() if not dependencies]
    heapify(ready)
    ordered: list[Ticket] = []
    while ready:
        number = heappop(ready)
        ordered.append(pending[number])
        for dependent in dependents[number]:
            blockers[dependent].remove(number)
            if not blockers[dependent]:
                heappush(ready, dependent)

    if len(ordered) != len(pending):
        unresolved = sorted(number for number, dependencies in blockers.items() if dependencies)
        raise ValueError(f"Dependency cycle prevents ordering children: {unresolved}")
    return ordered


@dataclass(frozen=True)
class ParentOutcome:
    parent_run_id: int
    state: RunState
    outcomes: tuple[tuple[int, RunOutcome], ...] = ()
    problems: tuple[str, ...] = ()
    next_step: str | None = None


class _WorkEngine(Engine):
    """Bind a work to its saved run and the preceding layer."""

    def __init__(self, deps: EngineDeps, run: Run, lower: int | None) -> None:
        super().__init__(deps)
        self.work_run = run
        self.lower = lower

    def _find_or_create_run(self, ticket_number: int) -> Run:
        if ticket_number != self.work_run.ticket_number:
            raise ValueError("Work ticket does not match its saved run")
        return self.work_run

    async def _stack_base(self, run_id: int, ticket: Ticket) -> str:
        if self.lower is not None:
            ticket = ticket.model_copy(update={"blocked_by": [self.lower]})
        return await super()._stack_base(run_id, ticket)

    async def check_again(self, run_id: int) -> RunOutcome:
        """Refresh CI while retaining the review of an unchanged layer diff."""

        async def check() -> RunOutcome:
            run = self.deps.store.get_run(run_id)
            ticket, scope = await self._load(run_id, run.ticket_number)
            identity = self._saved_identity(run_id)
            assert identity is not None
            diff = await self._diff(run_id, identity.path)
            await self._wait_for_green(run_id, ticket, scope)
            if diff != await self._diff(run_id, identity.path):
                return await self._review_round(run_id, ticket, scope)
            pr = await self.deps.github.find_pr(identity.branch)
            assert pr is not None
            return await self._ready_for_approval(
                run_id, pr, await Git(identity.path).head_sha(), ()
            )

        return await self._guarded(run_id, check)


async def _refresh_stack(
    deps: EngineDeps, parent_id: int, active: set[int]
) -> tuple[dict[int, Operation], str | None]:
    """Reconcile an interrupted refresh before any saved delivery can be skipped."""
    engine = Engine(deps)
    layers: list[tuple[Run, Identity]] = []
    refresh: dict[int, Operation] = {}
    with ExitStack() as locks:
        for work in deps.store.list_work(parent_id):
            if work.ticket_number not in active:
                continue
            if work.run_id is None:
                candidates = [
                    candidate
                    for candidate in reversed(deps.store.list_runs(work.ticket_number))
                    if candidate.state not in {RunState.COMPLETED, RunState.FAILED}
                    and candidate.repo.casefold() == deps.github.repo.casefold()
                    and json.loads(engine._identity_operation(candidate.id, {}).intent_json).get(
                        "repo_root"
                    )
                    == engine._repo_root()
                ]
                if not candidates:
                    continue
                run = candidates[0]
                deps.store.set_work_run(parent_id, work.ticket_number, run.id)
            else:
                run = deps.store.get_run(work.run_id)
            if run.state == RunState.COMPLETED:
                continue
            identity = engine._saved_identity(run.id)
            if identity is None or not identity.path.exists():
                continue
            locks.enter_context(TicketLock(deps.state_dir, deps.github.repo, run.ticket_number))
            layers.append((run, identity))
            for operation in deps.store.pending_operations(run.id):
                if operation.kind == "parent_refresh":
                    refresh[run.id] = operation
        if not layers:
            return refresh, None
        async with RepoLock(deps.state_dir, deps.repo_root):
            git = Git(deps.repo_root)
            await git.fetch()
            moved: list[int] = []
            for run, identity in layers:
                base = _ticket_base(deps, run.id)
                pending_lower = next(
                    (
                        identity
                        for run, identity in layers
                        if identity.branch == base and run.id in refresh
                    ),
                    None,
                )
                base_head = await git.head_sha(
                    f"refs/heads/{base}"
                    if pending_lower is not None
                    else f"refs/remotes/origin/{base}"
                )
                merge_base = (
                    await Git(identity.path)._run("merge-base", base_head, "HEAD")
                ).strip()
                if merge_base != base_head:
                    moved.append(run.ticket_number)
            if not moved and not refresh:
                return refresh, None
            for run, identity in layers:
                layer_git = Git(identity.path)
                for directory in ("rebase-merge", "rebase-apply"):
                    path = (await layer_git._run("rev-parse", "--git-path", directory)).strip()
                    if (identity.path / path).exists():
                        return refresh, f"Layer #{run.ticket_number} has an unfinished rebase"
                if await layer_git.status_porcelain():
                    return (
                        refresh,
                        f"Layer #{run.ticket_number} worktree must be clean before rebase",
                    )
            for run, identity in layers:
                if moved and run.id not in refresh:
                    head = await Git(identity.path).head_sha()
                    reviewed = deps.store.begin_operation(run.id, "parent_work", "reviewed", {})
                    saved_review = json.loads(reviewed.result_json or "{}")
                    diff = saved_review.get("diff")
                    if diff is None:
                        diff = await engine._diff(run.id, identity.path)
                    refresh[run.id] = deps.store.begin_operation(
                        run.id,
                        "parent_refresh",
                        head,
                        {"diff": diff},
                    )
            stack = deps.stack_factory(layers[0][1].path)
            if moved:
                bottom_run, bottom_identity = layers[0]
                if await stack.find_stack(bottom_identity.branch) is None:
                    await stack.init(_ticket_base(deps, bottom_run.id), [bottom_identity.branch])
                # The native rebase uses the local bottom base. Fast-forward it
                # only after every worktree has passed the cleanliness check.
                await git.fast_forward(deps.base_branch)
                try:
                    await stack.rebase()
                except GitHubError as error:
                    for run, identity in layers:
                        for directory in ("rebase-merge", "rebase-apply"):
                            path = (
                                await Git(identity.path)._run("rev-parse", "--git-path", directory)
                            ).strip()
                            if (identity.path / path).exists():
                                return (
                                    refresh,
                                    f"Rebase stopped at layer #{run.ticket_number}: {error}",
                                )
                    return refresh, f"Rebase stopped at layer #{moved[0]}: {error}"
            # All layers pass the gate before publishing any rebased head.
            for run, identity in layers:
                if run.id not in refresh:
                    continue
                gate = deps.store.begin_operation(
                    run.id, "parent_refresh_gate", await Git(identity.path).head_sha(), {}
                )
                if gate.status == "done":
                    continue
                result = await engine._stage(
                    run.id, Stage.VERIFY, partial(engine._gate, run.id, identity.path)
                )
                if not result.passed:
                    return refresh, f"Gate failed for layer #{run.ticket_number} after rebase"
                deps.store.complete_operation(gate.id, {})
            heads = [await Git(identity.path).head_sha() for _, identity in layers]
            push = deps.store.begin_operation(
                layers[0][0].id, "parent_refresh_push", ":".join(heads), {}
            )
            if push.status != "done":
                await stack.push()
                deps.store.complete_operation(push.id, {})
    return refresh, None


async def run_parent(
    deps: EngineDeps,
    parent_number: int,
    children: Sequence[Ticket],
    *,
    until_green: bool = False,
    parent_locked: bool = False,
) -> ParentOutcome:
    """Hold the parent lock while executing one child at a time.

    Work ids and delivery are saved before review. Each work reaches merge
    approval before the next child starts. Existing pipeline operations
    reconcile interrupted delivery, decisions and repairs in place.
    """
    # Import lazily: analysis uses the engine package too.
    from fugue.analysis.stage import analyse

    store = deps.store
    repo = deps.github.repo
    with nullcontext() if parent_locked else TicketLock(deps.state_dir, repo, parent_number):
        ordered = order_children(children)
        parent = store.find_open_parent_run(repo, parent_number)
        if parent is None:
            parent = store.create_parent_run(parent_number, repo)
        work = store.list_work(parent.id)
        known = {item.ticket_number for item in work}
        position = max((item.position for item in work), default=-1) + 1
        for ticket in ordered:
            if ticket.number not in known:
                store.add_work(parent.id, ticket.number, position)
                position += 1
        store.set_parent_state(parent.id, RunState.RUNNING)
        outcomes: list[tuple[int, RunOutcome]] = []
        lower: int | None = None
        active = {ticket.number for ticket in ordered}

        def stop(state: RunState, *problems: str, next_step: str | None = None) -> ParentOutcome:
            store.set_parent_state(parent.id, state)
            return ParentOutcome(parent.id, state, tuple(outcomes), tuple(problems), next_step)

        try:
            refresh, problem = await _refresh_stack(deps, parent.id, active)
            if problem is not None:
                return stop(RunState.BLOCKED, problem)
            for item in store.list_work(parent.id):
                if item.ticket_number not in active:
                    continue
                with TicketLock(deps.state_dir, repo, item.ticket_number):
                    ticket = await deps.github.get_ticket(item.ticket_number)
                    if ticket.status == Status.DONE:
                        continue
                    run = (
                        store.get_run(item.run_id)
                        if item.run_id is not None
                        else Engine(deps)._find_or_create_run(ticket.number)
                    )
                    store.set_work_run(parent.id, ticket.number, run.id)
                    finished = store.begin_operation(run.id, "parent_work", "delivered", {})
                    reviewed = store.begin_operation(run.id, "parent_work", "reviewed", {})
                    refreshing = refresh.get(run.id)
                    if refreshing is None and (
                        run.state == RunState.COMPLETED
                        or (
                            reviewed.status == "done"
                            and run.state == RunState.WAITING_FOR_MERGE_APPROVAL
                        )
                    ):
                        if ticket.issue_type != IssueType.QUESTION:
                            lower = ticket.number
                        continue
                    if ticket.issue_type == IssueType.QUESTION:
                        if not selected_solution(ticket):
                            store.set_run_state(run.id, RunState.WAITING_FOR_DECISION)
                            return stop(
                                RunState.WAITING_FOR_DECISION,
                                f"Question #{ticket.number} requires an answer",
                                next_step=f"fugue question {ticket.number}",
                            )
                        store.set_run_state(run.id, RunState.COMPLETED)
                        store.complete_operation(finished.id, {})
                        continue
                    if ticket.size in {Size.M, Size.L} and not selected_solution(ticket):
                        outcome = await analyse(deps, ticket, run_id=run.id)
                        outcomes.append((ticket.number, outcome))
                        return stop(
                            outcome.state,
                            next_step=(
                                f"fugue question {ticket.number}"
                                if outcome.state == RunState.WAITING_FOR_SELECTION
                                else None
                            ),
                        )
                    if ticket.blocked_by and (
                        len(ticket.blocked_by) != 1
                        or await lower_branch(ticket, deps.github) is None
                    ):
                        store.set_run_state(run.id, RunState.BLOCKED)
                        return stop(RunState.BLOCKED, "Open blockers require a sole lower open PR")
                    if lower is not None or ticket.blocked_by:
                        if lower is not None:
                            candidate = ticket.model_copy(update={"blocked_by": [lower]})
                            if await lower_branch(candidate, deps.github) is None:
                                store.set_run_state(run.id, RunState.BLOCKED)
                                return stop(
                                    RunState.BLOCKED, "The preceding layer needs an open PR"
                                )
                        probe = await deps.stack_factory(deps.repo_root).probe(repo)
                        if not probe.available:
                            store.set_run_state(run.id, RunState.BLOCKED)
                            return stop(RunState.BLOCKED, f"gh stack unavailable: {probe.reason}")
                    engine = _WorkEngine(deps, run, lower)
                    if finished.status != "done":
                        if run.state != RunState.WAITING_FOR_CI:
                            outcome = await (
                                engine.run_to_green(ticket.number)
                                if until_green
                                else engine.run_to_pr(ticket.number)
                            )
                            delivered = outcome.state == RunState.WAITING_FOR_CI or (
                                outcome.state == RunState.RUNNING and outcome.ci_green
                            )
                            if not delivered:
                                outcomes.append((ticket.number, outcome))
                                return stop(outcome.state)
                        store.complete_operation(finished.id, {})
                    identity = engine._saved_identity(run.id)
                    if (
                        identity is not None
                        and refreshing is not None
                        and reviewed.status == "done"
                        and json.loads(refreshing.intent_json)["diff"]
                        == await engine._diff(run.id, identity.path)
                    ):
                        outcome = await engine.check_again(run.id)
                    else:
                        outcome = await engine.continue_review(run.id)
                    while outcome.state == RunState.WAITING_FOR_DECISION:
                        decisions = _prompt_decisions(outcome)
                        if decisions is None:
                            break
                        outcome = await engine.decide(run.id, decisions)
                    outcomes.append((ticket.number, outcome))
                    if outcome.state != RunState.WAITING_FOR_MERGE_APPROVAL:
                        return stop(outcome.state)
                    identity = engine._saved_identity(run.id)
                    store.complete_operation(
                        reviewed.id,
                        {"diff": await engine._diff(run.id, identity.path)}
                        if identity is not None
                        else {},
                    )
                    if refreshing is not None:
                        store.complete_operation(refreshing.id, {})
                    lower = ticket.number
            return stop(RunState.COMPLETED)
        except (asyncio.CancelledError, KeyboardInterrupt):
            store.set_parent_state(parent.id, RunState.INTERRUPTED)
            raise
        except Exception:
            store.set_parent_state(parent.id, RunState.BLOCKED)
            raise


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
