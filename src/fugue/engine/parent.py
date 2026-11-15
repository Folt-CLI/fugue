"""Order unfinished children using only the supplied ticket data."""

from collections.abc import Sequence
from heapq import heapify, heappop, heappush

from fugue.models import Status, Ticket


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
