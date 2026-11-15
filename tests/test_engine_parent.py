"""Ordering, filtering and dependency-cycle cases for children of a parent."""

from itertools import permutations

import pytest

from fugue.engine.parent import order_children
from fugue.models import IssueType, Priority, Size, Status, Ticket


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
