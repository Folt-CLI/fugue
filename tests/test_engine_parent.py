"""Ordering, filtering and dependency-cycle cases for children of a parent."""

import asyncio
import fcntl
import sys
from collections.abc import Iterator
from itertools import permutations
from pathlib import Path

import pytest
from test_engine_review import BLOCKING, FIXED, LINES, review, write
from test_engine_review import Harness as ReviewHarness
from test_engine_review import harness as _review_harness
from test_engine_to_pr import Harness, git_command, result
from test_engine_to_pr import harness as harness
from test_stack import FakeStack

from fugue.agents import AgentRequest, ScriptedAdapter, ScriptStep
from fugue.engine import Engine, RunOutcome
from fugue.engine.facts import branch_name, worktree_path
from fugue.engine.locks import RepoLock, TicketLock, TicketLocked
from fugue.engine.parent import _prompt_decisions, order_children, run_parent
from fugue.engine.review import Decision, Finding
from fugue.gate import GateResult
from fugue.github import GitHubError, PullRequest
from fugue.github.stack import StackProbe
from fugue.gitops import Git, GitError
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.workflow import RunState

review_harness = _review_harness


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

    async def clean(self: Engine, run_id: int) -> RunOutcome:
        h.deps.store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
        return RunOutcome(run_id, RunState.WAITING_FOR_MERGE_APPROVAL, ci_green=True)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.continue_review", clean)
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
    assert all(
        result.state == RunState.WAITING_FOR_MERGE_APPROVAL for _, result in outcome.outcomes
    )
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
    assert h.deps.store.get_run(lower_id).state == RunState.WAITING_FOR_MERGE_APPROVAL
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


@pytest.mark.parametrize("state", [RunState.BLOCKED, RunState.FAILED, RunState.PROVIDER_LIMITED])
def test_review_failure_keeps_lower_and_retries_only_upper(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
    state: RunState,
) -> None:
    h, stack, catalog = parent_stack
    calls: list[int] = []
    failing = True

    async def review_work(self: Engine, run_id: int) -> RunOutcome:
        number = h.deps.store.get_run(run_id).ticket_number
        calls.append(number)
        outcome_state = state if number == 34 and failing else RunState.WAITING_FOR_MERGE_APPROVAL
        h.deps.store.set_run_state(run_id, outcome_state)
        return RunOutcome(run_id, outcome_state)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.continue_review", review_work)
    blocked = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert blocked.state == state
    assert calls == [33, 34]
    failing = False
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.COMPLETED
    assert calls == [33, 34, 34]
    assert len(h.github.created) == 1
    assert stack.submits == 1


def test_parent_clean_review_reaches_merge_approval(review_harness: ReviewHarness) -> None:
    h = review_harness
    h.script(write(LINES), review("No findings."))
    outcome = asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert outcome.state == RunState.COMPLETED
    assert outcome.outcomes[0][1].state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert len(h.reviewer_requests()) == 1
    assert len(h.github.posted) == 1
    repeated = asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert repeated.outcomes == ()
    assert len(h.reviewer_requests()) == 1


def test_parent_without_terminal_waits_and_resumes_decision(
    review_harness: ReviewHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = review_harness
    h.script(write(LINES), review(BLOCKING), write(FIXED), review("No findings."))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    pending = asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert pending.state == RunState.WAITING_FOR_DECISION
    run_id = pending.outcomes[0][1].run_id
    assert len(h.reviewer_requests()) == 1
    repeated = asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert repeated.state == RunState.WAITING_FOR_DECISION
    assert repeated.outcomes[0][1].run_id == run_id
    assert len(h.reviewer_requests()) == 1
    with (
        TicketLock(h.deps.state_dir, h.github.repo, 100),
        TicketLock(h.deps.state_dir, h.github.repo, h.github.ticket.number),
    ):
        pass
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("fugue.cli.choices.LineChooser.choose", lambda *args, **kwargs: 0)
    repaired = asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert repaired.state == RunState.COMPLETED
    assert repaired.outcomes[0][1].state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert (h.worktree / "allowed.txt").read_text() == FIXED
    assert len(h.reviewer_requests()) == 2
    assert len(h.github.posted) == 2


def test_parent_resumes_interrupted_accepted_repair(
    review_harness: ReviewHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = review_harness
    h.script(write(LINES), review(BLOCKING), write(FIXED), review("No findings."))
    monkeypatch.setattr(
        "fugue.engine.parent._prompt_decisions", lambda outcome: {1: Decision(True)}
    )
    original = Engine._apply_fixes

    async def interrupt(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(Engine, "_apply_fixes", interrupt)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert Engine._apply_fixes is original
    # Saved acceptance resumes even when there is no terminal after interruption.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    resumed = asyncio.run(run_parent(h.deps, 100, [h.github.ticket]))
    assert resumed.state == RunState.COMPLETED
    assert len(h.reviewer_requests()) == 2
    assert (h.worktree / "allowed.txt").read_text() == FIXED


@pytest.mark.parametrize("choice", [None, 2])
def test_undecided_blocking_finding_stops_before_next_child(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
    choice: int | None,
) -> None:
    h, _, catalog = parent_stack
    finding = Finding(1, "High", "Crash", "allowed.txt", 1, 1, None, True)

    async def pending(self: Engine, run_id: int) -> RunOutcome:
        h.deps.store.set_run_state(run_id, RunState.WAITING_FOR_DECISION)
        return RunOutcome(run_id, RunState.WAITING_FOR_DECISION, findings=(finding,))

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.continue_review", pending)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("fugue.cli.choices.LineChooser.choose", lambda *args, **kwargs: choice)
    outcome = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert outcome.state == RunState.WAITING_FOR_DECISION
    assert h.deps.store.list_work(outcome.parent_run_id)[1].run_id is None
    assert worktree_path(h.deps.worktrees_root, catalog[33]).exists()


@pytest.mark.parametrize(
    ("choice", "answer", "blocking", "expected"),
    [
        (1, "Reason", True, {1: Decision(False, reason="Reason")}),
        (1, "  ", True, None),
        (2, "42", False, {1: Decision(False, deferred_to=42)}),
        (2, "0", False, None),
        (2, "invalid", False, None),
    ],
)
def test_review_choice_validates_reason_and_deferred_ticket(
    monkeypatch: pytest.MonkeyPatch,
    choice: int,
    answer: str,
    blocking: bool,
    expected: dict[int, Decision] | None,
) -> None:
    finding = Finding(1, "Low", "Finding", None, None, None, None, blocking)
    outcome = RunOutcome(1, RunState.WAITING_FOR_DECISION, findings=(finding,))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("fugue.cli.choices.LineChooser.choose", lambda *args, **kwargs: choice)
    monkeypatch.setattr("builtins.input", lambda message: answer)
    assert _prompt_decisions(outcome) == expected


@pytest.fixture
def reentry_stack(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Harness, FakeStack, dict[int, Ticket], list[str]]:
    h, stack, catalog = parent_stack
    calls: list[str] = []

    def check_lock() -> None:
        lock = RepoLock(h.deps.state_dir, h.deps.repo_root)
        with lock.path.open("a+") as handle, pytest.raises(BlockingIOError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    async def rebase() -> str:
        check_lock()
        calls.append("rebase")
        for number in (33, 34):
            path = worktree_path(h.deps.worktrees_root, catalog[number])
            base = "develop" if number == 33 else branch_name(catalog[33])
            try:
                await Git(path)._run("rebase", base)
            except GitError as error:
                raise GitHubError(("gh", "stack", "rebase"), str(error)) from error
        return ""

    async def push() -> str:
        check_lock()
        calls.append("push")
        for number in (33, 34):
            branch = branch_name(catalog[number])
            await Git(h.deps.repo_root)._run(
                "push", "--force-with-lease", "origin", f"refs/heads/{branch}"
            )
        return ""

    async def ready(
        self: Engine, run_id: int, pr: PullRequest, head: str, notes: tuple[str, ...]
    ) -> RunOutcome:
        h.deps.store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
        return RunOutcome(run_id, RunState.WAITING_FOR_MERGE_APPROVAL, head_sha=head)

    async def green(self: Engine, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        return RunOutcome(run_id, RunState.RUNNING, ci_green=True)

    monkeypatch.setattr(Engine, "_ready_for_approval", ready)
    monkeypatch.setattr(Engine, "_wait_for_green", green)
    monkeypatch.setattr(stack, "rebase", rebase)
    monkeypatch.setattr(stack, "push", push)
    first = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert first.state == RunState.COMPLETED
    return h, stack, catalog, calls


def advance_base(h: Harness, *, lower: Ticket | None = None, conflict: bool = False) -> None:
    async def advance() -> None:
        path = h.deps.repo_root if lower is None else worktree_path(h.deps.worktrees_root, lower)
        filename = "allowed.txt" if conflict else "README"
        (path / filename).write_text("Advanced base\n")
        await git_command(path, "add", filename)
        await git_command(path, "commit", "-m", "Advance base")
        await git_command(path, "push", "origin", "HEAD")

    asyncio.run(advance())


@pytest.mark.parametrize("lower_advanced", [False, True])
def test_reentry_rebases_advanced_base_and_checks_every_layer(
    reentry_stack: tuple[Harness, FakeStack, dict[int, Ticket], list[str]],
    monkeypatch: pytest.MonkeyPatch,
    lower_advanced: bool,
) -> None:
    h, _, catalog, calls = reentry_stack
    advance_base(h, lower=catalog[33] if lower_advanced else None)
    gates: list[int] = []
    checks: list[int] = []
    reviews: list[int] = []
    original_gate = Engine._gate

    async def gate(self: Engine, run_id: int, path: Path) -> GateResult:
        gates.append(h.deps.store.get_run(run_id).ticket_number)
        assert calls == ["rebase"]
        return await original_gate(self, run_id, path)

    async def green(self: Engine, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        checks.append(ticket.number)
        assert calls == ["rebase", "push"]
        return RunOutcome(run_id, RunState.RUNNING, ci_green=True)

    async def review_again(self: Engine, run_id: int) -> RunOutcome:
        number = h.deps.store.get_run(run_id).ticket_number
        reviews.append(number)
        await green(self, run_id, catalog[number], "scope")
        h.deps.store.set_run_state(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)
        return RunOutcome(run_id, RunState.WAITING_FOR_MERGE_APPROVAL)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.continue_review", review_again)
    monkeypatch.setattr(Engine, "_gate", gate)
    monkeypatch.setattr(Engine, "_wait_for_green", green)
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.COMPLETED
    assert gates == checks == [33, 34]
    assert reviews == ([33] if lower_advanced else [])
    assert calls == ["rebase", "push"]
    again = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert again.outcomes == ()
    assert calls == ["rebase", "push"]
    assert gates == checks == [33, 34]


def test_reentry_without_movement_does_not_rebase(
    reentry_stack: tuple[Harness, FakeStack, dict[int, Ticket], list[str]],
) -> None:
    h, _, catalog, calls = reentry_stack
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.COMPLETED
    assert resumed.outcomes == ()
    assert calls == []


@pytest.mark.parametrize("dirty", [33, 34])
def test_reentry_requires_every_worktree_clean_before_rebase(
    reentry_stack: tuple[Harness, FakeStack, dict[int, Ticket], list[str]], dirty: int
) -> None:
    h, _, catalog, calls = reentry_stack
    advance_base(h)
    path = worktree_path(h.deps.worktrees_root, catalog[dirty])
    (path / "untracked.txt").write_text("Keep this")
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.BLOCKED
    assert resumed.problems == (f"Layer #{dirty} worktree must be clean before rebase",)
    assert calls == []
    assert (path / "untracked.txt").read_text() == "Keep this"


@pytest.mark.parametrize("conflicting_layer", [33, 34])
def test_reentry_conflict_stops_and_names_the_layer(
    reentry_stack: tuple[Harness, FakeStack, dict[int, Ticket], list[str]],
    conflicting_layer: int,
) -> None:
    h, _, catalog, calls = reentry_stack
    advance_base(h, lower=catalog[33] if conflicting_layer == 34 else None, conflict=True)
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.BLOCKED
    assert f"Rebase stopped at layer #{conflicting_layer}" in resumed.problems[0]
    assert calls == ["rebase"]
    repeated = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert repeated.state == RunState.BLOCKED
    assert f"Layer #{conflicting_layer} has an unfinished rebase" in repeated.problems[0]
    assert calls == ["rebase"]
    with TicketLock(h.deps.state_dir, h.github.repo, 33):
        pass


def test_reentry_gate_failure_does_not_push_and_resumes_without_rebase(
    reentry_stack: tuple[Harness, FakeStack, dict[int, Ticket], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _, catalog, calls = reentry_stack
    advance_base(h)
    gates: list[int] = []
    fail = True

    async def gate(self: Engine, run_id: int, path: Path) -> GateResult:
        number = h.deps.store.get_run(run_id).ticket_number
        gates.append(number)
        return GateResult(not (fail and number == 34), (), None)

    monkeypatch.setattr(Engine, "_gate", gate)
    stopped = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert stopped.state == RunState.BLOCKED
    assert stopped.problems == ("Gate failed for layer #34 after rebase",)
    assert calls == ["rebase"]
    fail = False
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.COMPLETED
    assert gates == [33, 34, 34]
    assert calls == ["rebase", "push"]


def test_interrupted_ci_resumes_refresh_without_rebase_gate_or_push(
    reentry_stack: tuple[Harness, FakeStack, dict[int, Ticket], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _, catalog, calls = reentry_stack
    advance_base(h)
    checks: list[int] = []
    interrupt = True

    async def green(self: Engine, run_id: int, ticket: Ticket, scope: str) -> RunOutcome:
        checks.append(ticket.number)
        if interrupt and ticket.number == 34:
            raise asyncio.CancelledError
        return RunOutcome(run_id, RunState.RUNNING, ci_green=True)

    monkeypatch.setattr(Engine, "_wait_for_green", green)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert calls == ["rebase", "push"]
    interrupt = False

    async def no_gate(self: Engine, run_id: int, path: Path) -> GateResult:
        pytest.fail("A successful gate must not repeat for the same head")

    monkeypatch.setattr(Engine, "_gate", no_gate)
    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.COMPLETED
    assert checks == [33, 34, 34]
    assert calls == ["rebase", "push"]


def test_reentry_initializes_bottom_tracking_before_second_child_delivery(
    parent_stack: tuple[Harness, FakeStack, dict[int, Ticket]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, stack, catalog = parent_stack
    reviews: list[int] = []
    gates: list[int] = []
    mutations: list[str] = []
    bottom = branch_name(catalog[33])
    path = worktree_path(h.deps.worktrees_root, catalog[33])
    finding = Finding(1, "High", "Needs a decision", "allowed.txt", 1, 1, None, True)
    original_init = stack.init
    original_gate = Engine._gate

    def check_lock() -> None:
        lock = RepoLock(h.deps.state_dir, h.deps.repo_root)
        with lock.path.open("a+") as handle, pytest.raises(BlockingIOError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    async def pending(self: Engine, run_id: int) -> RunOutcome:
        reviews.append(h.deps.store.get_run(run_id).ticket_number)
        h.deps.store.set_run_state(run_id, RunState.WAITING_FOR_DECISION)
        return RunOutcome(run_id, RunState.WAITING_FOR_DECISION, findings=(finding,))

    async def initialize(base: str, branches: list[str]) -> str:
        check_lock()
        mutations.append("init")
        assert base == "develop"
        assert branches == [bottom]
        return await original_init(base, branches)

    async def rebase() -> str:
        check_lock()
        if await stack.find_stack(bottom) is None:
            raise GitHubError(("gh", "stack", "rebase"), "Branch is not tracked")
        mutations.append("rebase")
        await Git(path)._run("rebase", "develop")
        return ""

    async def push() -> str:
        check_lock()
        mutations.append("push")
        await Git(path)._run("push", "--force-with-lease", "origin", f"refs/heads/{bottom}")
        return ""

    async def gate(self: Engine, run_id: int, path: Path) -> GateResult:
        gates.append(h.deps.store.get_run(run_id).ticket_number)
        return await original_gate(self, run_id, path)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("fugue.engine.parent._WorkEngine.continue_review", pending)
    monkeypatch.setattr(stack, "init", initialize)
    monkeypatch.setattr(stack, "rebase", rebase)
    monkeypatch.setattr(stack, "push", push)
    first = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert first.state == RunState.WAITING_FOR_DECISION
    assert asyncio.run(stack.find_stack(bottom)) is None
    assert h.deps.store.list_work(first.parent_run_id)[1].run_id is None
    old_head = asyncio.run(Git(path).head_sha())
    advance_base(h)
    monkeypatch.setattr(Engine, "_gate", gate)

    resumed = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert resumed.state == RunState.WAITING_FOR_DECISION
    assert resumed.problems == ()
    assert resumed.parent_run_id == first.parent_run_id
    assert mutations == ["init", "rebase", "push"]
    assert stack.branches == [bottom]
    assert stack.initializations == 1
    assert asyncio.run(Git(path).head_sha()) != old_head
    assert gates == [33]
    assert reviews == [33, 33]
    assert len(h.github.created) == 1
    assert h.deps.store.list_work(first.parent_run_id)[1].run_id is None
    assert not worktree_path(h.deps.worktrees_root, catalog[34]).exists()

    repeated = asyncio.run(run_parent(h.deps, 100, list(catalog.values())))
    assert repeated.state == RunState.WAITING_FOR_DECISION
    assert mutations == ["init", "rebase", "push"]
    assert gates == [33]
