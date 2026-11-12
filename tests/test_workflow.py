from itertools import product

import pytest

from fugue.config import RepoPolicy
from fugue.models import Component, IssueType, Priority, Size, SizeRule, Status, Ticket
from fugue.workflow import (
    TRANSITIONS,
    RunState,
    Stage,
    can_close,
    can_transition,
    commit_type_matches,
    components_ok,
    merge_allowed,
    ready_for_implementation,
    review_required,
    stages_for,
)


def make_ticket(size: Size = Size.S, status: Status = Status.TO_DO) -> Ticket:
    return Ticket(
        number=26,
        title="Add workflow guards",
        issue_type=IssueType.FEATURE,
        size=size,
        status=status,
        priority=Priority.HIGH,
        components=[Component.SOURCE_CODE],
        body_sections={"Summary": "Workflow guards", "Scope": "Workflow", "Solution": "Add guards"},
    )


EDGES = {
    ("load", "validate"),
    ("validate", "analyse"),
    ("validate", "prepare"),
    ("analyse", "select"),
    ("select", "prepare"),
    ("prepare", "implement"),
    ("implement", "verify"),
    ("verify", "deliver"),
    ("verify", "repair"),
    ("repair", "verify"),
    ("deliver", "monitor"),
    ("monitor", "review"),
    ("monitor", "repair"),
    ("monitor", "merge"),
    ("review", "repair"),
    ("review", "merge"),
    ("merge", "close"),
}


@pytest.mark.parametrize(("source", "target"), list(product(Stage, repeat=2)))
def test_every_transition(source: Stage, target: Stage) -> None:
    assert can_transition(source, target) == ((source.value, target.value) in EDGES)


def test_transition_table_and_enum_values() -> None:
    assert set(TRANSITIONS) == set(Stage)
    assert all(isinstance(targets, frozenset) for targets in TRANSITIONS.values())
    assert [stage.value for stage in Stage] == [
        "load",
        "validate",
        "analyse",
        "select",
        "prepare",
        "implement",
        "verify",
        "deliver",
        "monitor",
        "review",
        "repair",
        "merge",
        "close",
    ]
    assert [state.value for state in RunState] == [
        "running",
        "waiting_for_selection",
        "waiting_for_merge_approval",
        "waiting_for_ci",
        "checking",
        "repairing",
        "blocked",
        "interrupted",
        "provider_limited",
        "completed",
        "failed",
    ]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (Size.XS, "load validate prepare implement verify deliver monitor merge close"),
        (Size.S, "load validate prepare implement verify deliver monitor review merge close"),
        (
            Size.M,
            "load validate analyse select prepare implement verify deliver "
            "monitor review merge close",
        ),
        (
            Size.L,
            "load validate analyse select prepare implement verify deliver "
            "monitor review merge close",
        ),
        (Size.XL, "load validate close"),
    ],
)
def test_stages_for(size: Size, expected: str) -> None:
    assert stages_for(size, RepoPolicy()) == tuple(Stage(value) for value in expected.split())


@pytest.mark.parametrize("size", list(Size))
def test_review_required(size: Size) -> None:
    assert review_required(size, RepoPolicy()) == (size in (Size.S, Size.M, Size.L))


def test_configured_size_rules() -> None:
    policy = RepoPolicy(
        size_rules={Size.XS: SizeRule(analysis=True, review=True, max_components=2)}
    )
    assert stages_for(Size.XS, policy) == stages_for(Size.M, RepoPolicy())
    assert review_required(Size.XS, policy)
    assert components_ok(Size.XS, [Component.BUILD, Component.TESTS], policy) == []


@pytest.mark.parametrize("size", [Size.XS, Size.S, Size.M, Size.L])
@pytest.mark.parametrize("in_implementation", [False, True])
def test_ready_sizes(size: Size, in_implementation: bool) -> None:
    status = Status.TO_DO if size in (Size.XS, Size.S) else Status.READY_FOR_IMPLEMENTATION
    if in_implementation:
        status = Status.IN_IMPLEMENTATION
    assert (
        ready_for_implementation(
            make_ticket(size, status),
            RepoPolicy(),
            has_scope_attachment=True,
            selected_solution=True,
        )
        == []
    )


@pytest.mark.parametrize("section", ["Summary", "Scope", "Solution"])
@pytest.mark.parametrize("text", [None, "", " \n "])
def test_missing_or_empty_sections(section: str, text: str | None) -> None:
    ticket = make_ticket()
    if text is None:
        del ticket.body_sections[section]
    else:
        ticket.body_sections[section] = text
    problems = ready_for_implementation(
        ticket, RepoPolicy(), has_scope_attachment=True, selected_solution=True
    )
    assert len(problems) == 1
    assert section in problems[0]


@pytest.mark.parametrize("text", ["_Pending._", " \n_Pending._ \n"])
def test_pending_solution(text: str) -> None:
    ticket = make_ticket()
    ticket.body_sections["Solution"] = text
    assert ready_for_implementation(
        ticket, RepoPolicy(), has_scope_attachment=True, selected_solution=True
    ) == ["Solution must be non-empty and ready"]


@pytest.mark.parametrize("size", [Size.XS, Size.S, Size.M, Size.L])
def test_scope_and_selection_requirements(size: Size) -> None:
    status = Status.TO_DO if size in (Size.XS, Size.S) else Status.READY_FOR_IMPLEMENTATION
    problems = ready_for_implementation(
        make_ticket(size, status), RepoPolicy(), has_scope_attachment=False, selected_solution=False
    )
    assert bool(problems) == (size != Size.XS)
    if size == Size.S:
        assert problems == ["Size S requires a scope attachment"]
    elif size in (Size.M, Size.L):
        assert problems == ["Size M/L requires a selected solution"]


@pytest.mark.parametrize(
    ("size", "status"), list(product([Size.XS, Size.S, Size.M, Size.L], Status))
)
def test_status_requirements(size: Size, status: Status) -> None:
    allowed = (
        (Status.TO_DO, Status.IN_IMPLEMENTATION)
        if size in (Size.XS, Size.S)
        else (Status.READY_FOR_IMPLEMENTATION, Status.IN_IMPLEMENTATION)
    )
    problems = ready_for_implementation(
        make_ticket(size, status), RepoPolicy(), has_scope_attachment=True, selected_solution=True
    )
    assert bool(problems) == (status not in allowed)


@pytest.mark.parametrize("size", list(Size))
@pytest.mark.parametrize("count", range(7))
def test_component_limits(size: Size, count: int) -> None:
    components = list(Component)[:count]
    maximum = {Size.XS: 1, Size.S: 1, Size.M: 2, Size.L: 6, Size.XL: 0}[size]
    problems = components_ok(size, components, RepoPolicy())
    assert bool(problems) == (count > maximum)
    ticket = make_ticket(size, Status.IN_IMPLEMENTATION)
    ticket.components = components
    readiness = ready_for_implementation(
        ticket, RepoPolicy(), has_scope_attachment=True, selected_solution=True
    )
    assert bool(readiness) == (size == Size.XL or count == 0 or count > maximum)


@pytest.mark.parametrize("size", [Size.XS, Size.S, Size.M, Size.L])
def test_components_may_be_unset_before_s4(size: Size) -> None:
    status = Status.TO_DO if size in (Size.XS, Size.S) else Status.READY_FOR_IMPLEMENTATION
    ticket = make_ticket(size, status)
    ticket.components = []
    assert (
        ready_for_implementation(
            ticket, RepoPolicy(), has_scope_attachment=True, selected_solution=True
        )
        == []
    )


@pytest.mark.parametrize("status", list(Status))
def test_xl_never_implementable(status: Status) -> None:
    ticket = make_ticket(Size.XL, status)
    ticket.components = []
    assert "Size XL is never implementable" in ready_for_implementation(
        ticket, RepoPolicy(), has_scope_attachment=True, selected_solution=True
    )


@pytest.mark.parametrize("disposition_set", [False, True])
@pytest.mark.parametrize("child_status", [None, *Status])
def test_can_close(disposition_set: bool, child_status: Status | None) -> None:
    children = [] if child_status is None else [make_ticket(status=child_status)]
    problems = can_close(make_ticket(), children, disposition_set=disposition_set)
    assert len(problems) == int(not disposition_set) + int(
        child_status is not None and child_status != Status.DONE
    )


def test_can_close_reports_each_open_child() -> None:
    children = [
        make_ticket(status=status) for status in (Status.DONE, Status.TO_DO, Status.IN_ANALYSIS)
    ]
    children[1].number = 27
    children[2].number = 28
    problems = can_close(make_ticket(), children, disposition_set=True)
    assert len(problems) == 2
    assert "#27" in problems[0]
    assert "#28" in problems[1]


@pytest.mark.parametrize("issue_type", list(IssueType))
def test_commit_type_matches_all_issue_types(issue_type: IssueType) -> None:
    ticket = make_ticket()
    ticket.issue_type = issue_type
    assert commit_type_matches(ticket, f"{issue_type}[#26]: Add guards")


@pytest.mark.parametrize(
    "subject",
    [
        "Bug[#26]: Add guards",
        "feature[#26]: Add guards",
        "Feature[#260]: Add guards",
        "Feature[#026]: Add guards",
        "Feature[#26] Add guards",
        " Feature[#26]: Add guards",
        "",
    ],
)
def test_commit_type_mismatch(subject: str) -> None:
    assert not commit_type_matches(make_ticket(), subject)


@pytest.mark.parametrize(
    ("gate", "ci", "review", "approval", "threads", "expected_count"),
    [
        (True, True, True, "head", 0, 0),
        (False, True, True, "head", 0, 1),
        (True, False, True, "head", 0, 1),
        (True, True, False, "head", 0, 1),
        (True, True, True, None, 0, 1),
        (True, True, True, "old-head", 0, 1),
        (True, True, True, "head", 1, 1),
        (True, True, True, "head", 3, 1),
        (False, False, False, None, 2, 5),
    ],
)
def test_merge_allowed(
    gate: bool, ci: bool, review: bool, approval: str | None, threads: int, expected_count: int
) -> None:
    assert (
        len(
            merge_allowed(
                gate_passed=gate,
                ci_green=ci,
                review_clean_or_not_required=review,
                approval_head_sha=approval,
                current_head_sha="head",
                unresolved_threads=threads,
            )
        )
        == expected_count
    )
