"""Gather the repository overview from GitHub reads and local runs, without an agent."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fugue.config import RepoPolicy
from fugue.engine.facts import scope_url, selected_solution
from fugue.github.overview import ClosedIssue, MergedPr, OverviewPr
from fugue.models import IssueType, Size, Status, Ticket
from fugue.store.records import Approval, Run
from fugue.workflow import RunState
from fugue.workflow.guards import ready_for_implementation

RECENT_COUNT = 5
MAX_RUNS = 20
WAITING_RUN_STATES = frozenset(
    {
        RunState.WAITING_FOR_SELECTION,
        RunState.WAITING_FOR_DECISION,
        RunState.WAITING_FOR_MERGE_APPROVAL,
    }
)
_PENDING = "_Pending._"


class OverviewPort(Protocol):
    async def list_open_issue_numbers(self, limit: int) -> tuple[list[int], bool]: ...

    async def open_pull_requests(self) -> list[OverviewPr]: ...

    async def recent_merged_pull_requests(self, count: int) -> list[MergedPr]: ...

    async def recent_closed_issues(self, count: int) -> list[ClosedIssue]: ...

    async def sub_issue_progress(self, number: int) -> tuple[int, int]: ...

    async def get_ticket(self, number: int) -> Ticket: ...


@dataclass(frozen=True)
class TicketRow:
    number: int
    title: str
    issue_type: str
    size: str
    status: str
    priority: str


@dataclass(frozen=True)
class ProgressRow:
    number: int
    title: str
    issue_type: str
    closed: int
    total: int


@dataclass(frozen=True)
class BlockedRow:
    number: int
    title: str
    blockers: tuple[int, ...]


@dataclass(frozen=True)
class WaitingTicketRow:
    number: int
    title: str


@dataclass(frozen=True)
class ReadyRow:
    number: int
    title: str
    size: str


@dataclass(frozen=True)
class PrRow:
    number: int
    title: str
    head_sha: str
    passing: int
    failing: int
    pending: int
    review_decision: str
    approved: bool


@dataclass(frozen=True)
class RunRow:
    id: int
    ticket_number: int
    state: str
    age_seconds: float


@dataclass(frozen=True)
class ClosedRow:
    number: int
    title: str


@dataclass(frozen=True)
class MergedRow:
    number: int
    title: str
    merged_at: str


@dataclass(frozen=True)
class Overview:
    repo: str
    generated_at: datetime
    tickets: tuple[TicketRow, ...] = ()
    progress: tuple[ProgressRow, ...] = ()
    ready: tuple[ReadyRow, ...] = ()
    blocked: tuple[BlockedRow, ...] = ()
    waiting: tuple[WaitingTicketRow, ...] = ()
    pull_requests: tuple[PrRow, ...] = ()
    runs: tuple[RunRow, ...] = ()
    merged: tuple[MergedRow, ...] = ()
    closed: tuple[ClosedRow, ...] = ()
    unreadable: tuple[tuple[int, str], ...] = ()
    limit: int | None = None
    truncated: bool = False
    # Why GitHub could not be read; set only for an offline overview of local runs.
    offline: str | None = None


def unfinished_runs(runs: Sequence[Run], repo: str, now: datetime) -> tuple[RunRow, ...]:
    """Runs of this repository that are not completed, newest first, at most MAX_RUNS."""
    open_runs = [r for r in runs if r.repo == repo and r.state != RunState.COMPLETED]
    open_runs.sort(key=lambda r: (r.updated_at, r.id), reverse=True)
    return tuple(
        RunRow(r.id, r.ticket_number, r.state, max(0.0, (now - r.updated_at).total_seconds()))
        for r in open_runs[:MAX_RUNS]
    )


def offline_overview(runs: Sequence[Run], *, repo: str, now: datetime, reason: str) -> Overview:
    """An overview holding only local runs, for when GitHub cannot be read."""
    return Overview(
        repo=repo, generated_at=now, runs=unfinished_runs(runs, repo, now), offline=reason
    )


def _analysis_pending_decision(ticket: Ticket) -> bool:
    if ticket.size not in (Size.M, Size.L) and ticket.issue_type != IssueType.QUESTION:
        return False
    analysis = ticket.body_sections.get("Analysis", "").strip()
    return bool(analysis) and analysis != _PENDING and not selected_solution(ticket)


def _is_ready(ticket: Ticket, policy: RepoPolicy) -> bool:
    if ticket.issue_type in (IssueType.QUESTION, IssueType.EPIC) or ticket.size == Size.XL:
        return False
    problems = ready_for_implementation(
        ticket,
        policy,
        has_scope_attachment=scope_url(ticket) is not None,
        selected_solution=selected_solution(ticket),
    )
    if problems:
        return False
    wanted = Status.TO_DO if ticket.size in (Size.XS, Size.S) else Status.READY_FOR_IMPLEMENTATION
    return ticket.status == wanted


async def gather(
    port: OverviewPort,
    runs: Sequence[Run],
    approvals: Sequence[Approval],
    *,
    repo: str,
    limit: int,
    now: datetime,
    concurrency: int = 8,
    policy: RepoPolicy | None = None,
) -> Overview:
    """Read GitHub through the port and combine it with local runs."""
    rules = policy if policy is not None else RepoPolicy()
    numbers, truncated = await port.list_open_issue_numbers(limit)
    gate = asyncio.Semaphore(concurrency)

    async def read(number: int) -> Ticket | tuple[int, str]:
        async with gate:
            try:
                return await port.get_ticket(number)
            except Exception as error:
                return number, _one_line(error)

    read_results = await asyncio.gather(*(read(number) for number in numbers))
    unreadable: list[tuple[int, str]] = []
    tickets: list[Ticket] = []
    for result in read_results:
        if isinstance(result, Ticket):
            tickets.append(result)
        else:
            unreadable.append(result)

    async def count(ticket: Ticket) -> ProgressRow | tuple[int, str]:
        async with gate:
            try:
                closed, total = await port.sub_issue_progress(ticket.number)
            except Exception as error:
                return ticket.number, f"children: {_one_line(error)}"
            return ProgressRow(ticket.number, ticket.title, str(ticket.issue_type), closed, total)

    parents = [t for t in tickets if t.issue_type in (IssueType.QUESTION, IssueType.EPIC)]
    progress: list[ProgressRow] = []
    for outcome in await asyncio.gather(*(count(t) for t in parents)):
        if isinstance(outcome, ProgressRow):
            progress.append(outcome)
        else:
            unreadable.append(outcome)

    ready: list[ReadyRow] = []
    blocked: list[BlockedRow] = []
    waiting: list[WaitingTicketRow] = []
    for ticket in tickets:
        if ticket.blocked_by:
            blocked.append(BlockedRow(ticket.number, ticket.title, tuple(ticket.blocked_by)))
        elif _analysis_pending_decision(ticket):
            waiting.append(WaitingTicketRow(ticket.number, ticket.title))
        elif _is_ready(ticket, rules):
            ready.append(ReadyRow(ticket.number, ticket.title, str(ticket.size)))

    approved = {(a.subject, a.head_sha) for a in approvals}
    pull_requests = tuple(
        PrRow(
            pr.number,
            pr.title,
            pr.head_sha,
            pr.passing,
            pr.failing,
            pr.pending,
            pr.review_decision,
            (str(pr.number), pr.head_sha) in approved,
        )
        for pr in await port.open_pull_requests()
    )
    merged = tuple(
        MergedRow(m.number, m.title, m.merged_at)
        for m in await port.recent_merged_pull_requests(RECENT_COUNT)
    )
    closed = tuple(
        ClosedRow(c.number, c.title) for c in await port.recent_closed_issues(RECENT_COUNT)
    )
    return Overview(
        repo=repo,
        generated_at=now,
        tickets=tuple(
            TicketRow(
                t.number, t.title, str(t.issue_type), str(t.size), str(t.status), str(t.priority)
            )
            for t in tickets
        ),
        progress=tuple(progress),
        ready=tuple(ready),
        blocked=tuple(blocked),
        waiting=tuple(waiting),
        pull_requests=pull_requests,
        runs=unfinished_runs(runs, repo, now),
        merged=merged,
        closed=closed,
        unreadable=tuple(sorted(unreadable)),
        limit=limit,
        truncated=truncated,
    )


def _one_line(error: BaseException) -> str:
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    text = lines[-1] if lines else type(error).__name__
    return text if len(text) <= 160 else text[:157] + "..."
