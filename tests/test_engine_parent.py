"""Ordering, filtering and dependency-cycle cases for children of a parent."""

import asyncio
from collections.abc import Iterator
from itertools import permutations

import pytest
from test_engine_to_pr import Harness, result
from test_engine_to_pr import harness as harness
from test_stack import FakeStack

from fugue.agents import AgentRequest, ScriptedAdapter, ScriptStep
from fugue.engine import Engine, RunOutcome
from fugue.engine.facts import branch_name, worktree_path
from fugue.engine.locks import TicketLock, TicketLocked
from fugue.engine.parent import order_children, run_parent
from fugue.github import PullRequest
from fugue.github.stack import StackProbe
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.workflow import RunState


def child(
    number: int, *, blocked_by: tuple[int, ...] = (), status: Status = Status.TO_DO
) -> Ticket:
    return Ticket(
        number=number,
        title=f"Child {number}",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=status,
        priority=Priority.MEDIUM,
        blocked_by=list(blocked_by),
    )


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        ([child(5), child(6), child(7, blocked_by=(5,))], [5, 6, 7]),
        ([child(5, blocked_by=(7,)), child(6), child(7)], [6, 7, 5]),
        ([child(5), child(6), child(7)], [5, 6, 7]),
        ([child(5, blocked_by=(6, 7)), child(6, blocked_by=(7,)), child(7)], [7, 6, 5]),
        ([child(5, blocked_by=(7, 7, 99)), child(6), child(7)], [6, 7, 5]),
    ],
)
def test_dependency_order_is_independent_of_input_order(
    children: list[Ticket], expected: list[int]
) -> None:
    for sequence in permutations(children):
        assert [item.number for item in order_children(sequence)] == expected


def test_finished_children_and_their_dependencies_are_ignored() -> None:
    done = child(5, blocked_by=(7,), status=Status.DONE)
    # Ticket currently has no state field; model_copy retains supplied provider data.
    closed = child(6, blocked_by=(7,)).model_copy(update={"state": "closed"})
    remaining = child(7, blocked_by=(5, 6, 99))
    assert order_children([done, closed, remaining]) == [remaining]
    assert order_children([done, closed]) == []


def test_empty_children() -> None:
    assert order_children([]) == []


@pytest.mark.parametrize(
    ("children", "numbers"),
    [
        ([child(5, blocked_by=(5,))], [5]),
        ([child(5, blocked_by=(7,)), child(6), child(7, blocked_by=(5,))], [5, 7]),
        (
            [child(5, blocked_by=(6,)), child(6, blocked_by=(7,)), child(7, blocked_by=(5,))],
            [5, 6, 7],
        ),
    ],
)
def test_cycles_raise_with_ticket_numbers(children: list[Ticket], numbers: list[int]) -> None:
    with pytest.raises(ValueError, match="cycle") as error:
        order_children(children)
    for number in numbers:
        assert str(number) in str(error.value)


def test_ordering_preserves_tickets_and_input_data() -> None:
    children = [child(5, blocked_by=(7, 99)), child(7)]
    before = [item.model_dump() for item in children]
    ordered = order_children(children)
    assert ordered[0] is children[1]
    assert ordered[1] is children[0]
    assert [item.model_dump() for item in children] == before


@pytest.fixture
def parent_stack(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Harness, FakeStack, dict[int, Ticket]]]:
    h = harness
    first = h.github.ticket
    second = first.model_copy(
        update={
            "number": 34,
            "title": "Upper child",
            "blocked_by": [33],
            "body_sections": {
                **first.body_sections,
                "Solution": "[34-solution-1.md](https://github.com/user-attachments/files/scope)",
            },
        }
    )
    catalog = {33: first, 34: second}
    prs: dict[str, PullRequest] = {}

    async def get_ticket(number: int) -> Ticket:
        if number > h.github.ticket.number:
            if h.github.pr is not None:
                prs[branch_name(h.github.ticket)] = h.github.pr
            h.github.ticket = catalog[number]
            h.github.pr = None
        return catalog[number]

    async def set_status(number: int, status: Status) -> None:
        catalog[number] = catalog[number].model_copy(update={"status": status})
        h.github.ticket = catalog[number]

    async def find_pr(branch: str) -> PullRequest | None:
        if branch == branch_name(h.github.ticket):
            return h.github.pr
        return prs.get(branch)

    def implement(request: AgentRequest) -> None:
        for number in (100, h.github.ticket.number):
            with pytest.raises(TicketLocked), TicketLock(h.deps.state_dir, h.github.repo, number):
                pass
        parent = h.deps.store.find_open_parent_run(h.github.repo, 100)
        assert parent is not None
        work = h.deps.store.list_work(parent.id)
        assert next(item for item in work if item.ticket_number == h.github.ticket.number).run_id
        (request.cwd / "allowed.txt").write_text(f"child {h.github.ticket.number}")

    adapter = ScriptedAdapter(
        [ScriptStep(result=result(), side_effect=implement) for _ in range(2)]
    )
    h.deps.registry.register("codex", lambda config: adapter)
    fake = FakeStack(h)
    h.deps.stack_factory = lambda path: fake
    monkeypatch.setattr(h.github, "get_ticket", get_ticket)
    monkeypatch.setattr(h.github, "set_status", set_status)
    monkeypatch.setattr(h.github, "find_pr", find_pr)
    yield h, fake, catalog


@pytest.mark.parametrize("dependent", [False, True])
def test_parent_runs_children_as_one_stack(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]], dependent: bool
) -> None:
    h, stack, catalog = parent_stack
    if not dependent:
        catalog[34] = catalog[34].model_copy(update={"blocked_by": []})
    outcome = asyncio.run(run_parent(h.deps, 100, list(reversed(list(catalog.values())))))
    assert outcome.state == RunState.COMPLETED
    assert [number for number, _ in outcome.outcomes] == [33, 34]
    assert all(result.state == RunState.WAITING_FOR_CI for _, result in outcome.outcomes)
    work = h.deps.store.list_work(outcome.parent_run_id)
    assert [item.ticket_number for item in work] == [33, 34]
    assert [item.run_id for item in work] == [result.run_id for _, result in outcome.outcomes]
    assert stack.branches == [branch_name(catalog[33]), branch_name(catalog[34])]
    assert stack.submits == 1
    assert stack.ready == [branch_name(catalog[34])]
    assert h.github.created[0][1] == "develop"


def test_parent_reentry_keeps_delivered_lower_and_resumes_upper(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
) -> None:
    h, stack, catalog = parent_stack
    stack.interrupt = True
    with pytest.raises(RuntimeError, match="interrupted before submit"):
        asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    parent = h.deps.store.find_open_parent_run(h.github.repo, 100)
    assert parent is not None
    before = h.deps.store.list_work(parent.id)
    assert all(item.run_id is not None for item in before)
    lower_id = before[0].run_id
    assert lower_id is not None
    assert h.deps.store.get_run(lower_id).state == RunState.WAITING_FOR_CI
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert outcome.parent_run_id == parent.id
    assert [number for number, _ in outcome.outcomes] == [34]
    assert h.deps.store.list_work(parent.id) == before
    assert len(h.github.created) == 1
    assert stack.submits == 1


def test_parent_question_stops_before_worktree_and_releases_locks(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
) -> None:
    h, _, catalog = parent_stack
    catalog[33] = catalog[33].model_copy(update={"issue_type": IssueType.QUESTION})
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert outcome.state == RunState.WAITING_FOR_DECISION
    assert outcome.next_step == "fugue question 33"
    assert not h.deps.worktrees_root.exists()
    assert h.deps.store.list_work(outcome.parent_run_id)[1].run_id is None
    with (
        TicketLock(h.deps.state_dir, h.github.repo, 100),
        TicketLock(h.deps.state_dir, h.github.repo, 33),
    ):
        pass


def test_parent_unavailable_stack_keeps_lower_layer(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
) -> None:
    h, stack, catalog = parent_stack
    stack.available = StackProbe(False, "extension_missing")
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("gh stack unavailable: extension_missing",)
    assert h.github.created
    assert worktree_path(h.deps.worktrees_root, catalog[33]).exists()
    assert not worktree_path(h.deps.worktrees_root, catalog[34]).exists()


@pytest.mark.parametrize("locked", [100, 33])
def test_parent_respects_parent_and_child_locks(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]], locked: int
) -> None:
    h, _, catalog = parent_stack
    with TicketLock(h.deps.state_dir, h.github.repo, locked), pytest.raises(TicketLocked):
        asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert not h.deps.worktrees_root.exists()


@pytest.mark.parametrize("state", [RunState.BLOCKED, RunState.FAILED, RunState.PROVIDER_LIMITED])
def test_parent_stops_on_unsuccessful_work(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
    state: RunState,
) -> None:
    h, _, catalog = parent_stack

    async def stop(self: Engine, number: int) -> RunOutcome:
        if number == 33:
            return await Engine.run_to_pr(self, number)
        run = h.deps.store.list_runs(number)[0]
        h.deps.store.set_run_state(run.id, state)
        return RunOutcome(run.id, state)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.run_to_pr", stop)
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert outcome.state == state
    assert [number for number, _ in outcome.outcomes] == [33, 34]
    assert worktree_path(h.deps.worktrees_root, catalog[33]).exists()
    assert not worktree_path(h.deps.worktrees_root, catalog[34]).exists()


def test_parent_analyses_unselected_m_child_and_stops(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _, catalog = parent_stack
    catalog[33] = catalog[33].model_copy(update={"size": Size.M})
    analysed: list[int] = []

    async def analyse_child(
        deps: object, ticket: Ticket, *, run_id: int | None = None
    ) -> RunOutcome:
        assert run_id is not None
        parent = h.deps.store.find_open_parent_run(h.github.repo, 100)
        assert parent is not None
        assert h.deps.store.list_work(parent.id)[0].run_id == run_id
        analysed.append(ticket.number)
        return RunOutcome(run_id, RunState.WAITING_FOR_SELECTION)

    monkeypatch.setattr("fugue.analysis.stage.analyse", analyse_child)
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert analysed == [33]
    assert outcome.state == RunState.WAITING_FOR_SELECTION
    assert outcome.next_step == "fugue question 33"
    assert not h.deps.worktrees_root.exists()


def test_parent_skips_done_children(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
) -> None:
    h, _, catalog = parent_stack
    catalog[34] = catalog[34].model_copy(update={"status": Status.DONE})
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert outcome.state == RunState.COMPLETED
    assert [item.ticket_number for item in h.deps.store.list_work(outcome.parent_run_id)] == [33]


def test_parent_cancellation_records_interruption_and_releases_locks(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _, catalog = parent_stack

    async def cancel(self: Engine, number: int) -> RunOutcome:
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr("fugue.engine.parent._WorkEngine.run_to_pr", cancel)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    parent = h.deps.store.find_open_parent_run(h.github.repo, 100)
    assert parent is not None and parent.state == RunState.INTERRUPTED
    first = h.deps.store.list_work(parent.id)[0]
    assert first.run_id is not None
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.COMPLETED
    assert h.deps.store.list_work(parent.id)[0].run_id == first.run_id


def test_parent_green_work_is_recorded_and_never_repeated(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _, catalog = parent_stack
    calls: list[int] = []

    async def green(self: Engine, number: int) -> RunOutcome:
        calls.append(number)
        run = h.deps.store.list_runs(number)[0]
        return RunOutcome(run.id, RunState.RUNNING, pr_number=71, ci_green=True)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.run_to_green", green)
    first = asyncio.run(run_parent(h.deps, 100, [catalog[33]], until_green=True))
    assert first.state == RunState.COMPLETED
    assert first.outcomes[0][1].ci_green
    second = asyncio.run(run_parent(h.deps, 100, [catalog[33]], until_green=True))
    assert second.state == RunState.COMPLETED
    assert second.outcomes == ()
    assert calls == [33]


@pytest.mark.parametrize("legacy_delivery", [False, True])
def test_parent_to_green_monitors_previously_delivered_children(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
    legacy_delivery: bool,
) -> None:
    h, stack, catalog = parent_stack
    delivered = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    saved = {number: outcome.run_id for number, outcome in delivered.outcomes}
    if legacy_delivery:
        for run_id in saved.values():
            operation = h.deps.store.begin_operation(run_id, "parent_work", "delivered", {})
            h.deps.store.complete_operation(operation.id, {})
    calls: list[int] = []

    async def green(self: Engine, number: int) -> RunOutcome:
        run = self._find_or_create_run(number)
        assert run.id == saved[number]
        calls.append(number)
        return RunOutcome(run.id, RunState.RUNNING, pr_number=71, ci_green=True)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.run_to_green", green)
    monitored = asyncio.run(run_parent(h.deps, 100, list(catalog.values()), until_green=True))
    assert monitored.state == RunState.COMPLETED
    assert calls == [33, 34]
    assert all(outcome.ci_green for _, outcome in monitored.outcomes)
    repeated = asyncio.run(run_parent(h.deps, 100, list(catalog.values()), until_green=True))
    assert repeated.state == RunState.COMPLETED
    assert repeated.outcomes == ()
    assert calls == [33, 34]
    assert len(h.github.created) == 1
    assert stack.submits == 1


def test_parent_to_green_retries_failed_monitoring_without_repeating_green_children(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, stack, catalog = parent_stack
    delivered = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    saved = {number: outcome.run_id for number, outcome in delivered.outcomes}
    calls: list[int] = []
    failing = True

    async def green(self: Engine, number: int) -> RunOutcome:
        run = self._find_or_create_run(number)
        assert run.id == saved[number]
        calls.append(number)
        if number == 34 and failing:
            h.deps.store.set_run_state(run.id, RunState.BLOCKED)
            return RunOutcome(run.id, RunState.BLOCKED, problems=("CI checks failed",))
        return RunOutcome(run.id, RunState.RUNNING, pr_number=71, ci_green=True)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.run_to_green", green)
    blocked = asyncio.run(run_parent(h.deps, 100, list(catalog.values()), until_green=True))
    assert blocked.state == RunState.BLOCKED
    assert calls == [33, 34]
    assert blocked.outcomes[-1][1].problems == ("CI checks failed",)
    failing = False
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values()), until_green=True))
    assert resumed.state == RunState.COMPLETED
    assert calls == [33, 34, 34]
    assert len(h.github.created) == 1
    assert stack.submits == 1
