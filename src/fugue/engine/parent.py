"""Run the ordered children of a parent as sequential stack layers."""

import asyncio
import json
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from heapq import heapify, heappop, heappush

from fugue.engine.facts import selected_solution
from fugue.engine.locks import TicketLock
from fugue.engine.pipeline import Engine, EngineDeps, RunOutcome
from fugue.github.stack import lower_branch
from fugue.models import IssueType, Size, Status, Ticket
from fugue.store import Run
from fugue.workflow import RunState


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


async def run_parent(
    deps: EngineDeps,
    parent_number: int,
    children: Sequence[Ticket],
    *,
    until_green: bool = False,
    parent_locked: bool = False,
) -> ParentOutcome:
    """Hold the parent lock while executing one child at a time.

    Work ids are saved before any per-ticket execution. Delivered work is never
    repeated on re-entry, including interruption between delivery and the next
    work. Delivery alone does not satisfy a later request to reach green CI.
    Existing pipeline operations reconcile unfinished work in place.
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
                    reached_green = json.loads(finished.result_json or "{}").get("ci_green", False)
                    if run.state == RunState.COMPLETED or (
                        finished.status == "done" and (not until_green or reached_green)
                    ):
                        if ticket.issue_type != IssueType.QUESTION:
                            lower = ticket.number
                        continue
                    if run.state == RunState.WAITING_FOR_CI and not until_green:
                        store.complete_operation(finished.id, {})
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
                    outcome = await (
                        engine.run_to_green(ticket.number)
                        if until_green
                        else engine.run_to_pr(ticket.number)
                    )
                    outcomes.append((ticket.number, outcome))
                    delivered = outcome.state == RunState.WAITING_FOR_CI or (
                        outcome.state == RunState.RUNNING and outcome.ci_green
                    )
                    if not delivered:
                        return stop(outcome.state)
                    store.complete_operation(finished.id, {"ci_green": outcome.ci_green})
                    lower = ticket.number
            return stop(RunState.COMPLETED)
        except (asyncio.CancelledError, KeyboardInterrupt):
            store.set_parent_state(parent.id, RunState.INTERRUPTED)
            raise
        except Exception:
            store.set_parent_state(parent.id, RunState.BLOCKED)
            raise
