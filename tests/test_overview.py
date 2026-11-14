"""Classification and gathering of the repository overview from fake GitHub data."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from fugue.github.overview import ClosedIssue, MergedPr, OverviewPr
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.overview import Overview, gather, offline_overview
from fugue.store.records import Approval, Run

REPO = "o/r"
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
LINK = "[{n}-solution-1.md](https://github.com/user-attachments/files/{n}/a.md)"
SELECTED = "[Selected Solution] " + LINK


def ticket(
    number: int,
    *,
    size: Size = Size.XS,
    status: Status = Status.TO_DO,
    issue_type: IssueType = IssueType.TASK,
    blocked_by: list[int] | None = None,
    solution: str = "Do the work.",
    analysis: str = "",
) -> Ticket:
    return Ticket(
        number=number,
        title=f"ticket {number}",
        issue_type=issue_type,
        size=size,
        status=status,
        priority=Priority.MEDIUM,
        blocked_by=blocked_by or [],
        body_sections={
            "Summary": "Summary text",
            "Scope": "Scope text",
            "Analysis": analysis,
            "Solution": solution,
            "Details": "",
        },
    )


class FakePort:
    def __init__(
        self,
        tickets: list[Ticket],
        *,
        bad: Mapping[int, str] | None = None,
        truncated: bool = False,
        prs: list[OverviewPr] | None = None,
        progress: Mapping[int, tuple[int, int]] | None = None,
    ) -> None:
        self.tickets = {t.number: t for t in tickets}
        self.bad = dict(bad or {})
        self.truncated = truncated
        self.prs = prs or []
        self.progress = dict(progress or {})
        self.limit_seen: int | None = None
        self.read: list[int] = []

    async def list_open_issue_numbers(self, limit: int) -> tuple[list[int], bool]:
        self.limit_seen = limit
        numbers = [*self.tickets, *self.bad]
        return sorted(numbers)[:limit], self.truncated

    async def get_ticket(self, number: int) -> Ticket:
        self.read.append(number)
        if number in self.bad:
            raise ValueError(self.bad[number])
        return self.tickets[number]

    async def open_pull_requests(self) -> list[OverviewPr]:
        return self.prs

    async def recent_merged_pull_requests(self, count: int) -> list[MergedPr]:
        return [MergedPr(30, "merged thirty", "2026-04-30T00:00:00Z")][:count]

    async def recent_closed_issues(self, count: int) -> list[ClosedIssue]:
        return [ClosedIssue(31, "closed thirty-one")][:count]

    async def sub_issue_progress(self, number: int) -> tuple[int, int]:
        return self.progress[number]


def pr(number: int, head: str) -> OverviewPr:
    return OverviewPr(number, f"pr {number}", head, "branch", "develop", False, "none", 1, 0, 0)


def approval(subject: str, head: str, run_id: int = 1) -> Approval:
    return Approval(1, run_id, "merge", subject, head, "approved", "dev", "ok", NOW, None)


def run_record(id: int, ticket_number: int, state: str, age_minutes: int, repo: str = REPO) -> Run:
    moment = NOW - timedelta(minutes=age_minutes)
    return Run(id, ticket_number, repo, state, moment, moment)


def collect(
    port: FakePort,
    runs: list[Run] | None = None,
    approvals: list[Approval] | None = None,
    limit: int = 100,
) -> Overview:
    return asyncio.run(gather(port, runs or [], approvals or [], repo=REPO, limit=limit, now=NOW))


def test_tickets_grouped_with_progress_for_questions_and_epics() -> None:
    port = FakePort(
        [
            ticket(1),
            ticket(2, status=Status.IN_IMPLEMENTATION),
            ticket(
                3,
                size=Size.L,
                status=Status.IN_ANALYSIS,
                issue_type=IssueType.QUESTION,
                analysis="Overview",
            ),
            ticket(4, size=Size.XL, status=Status.IN_IMPLEMENTATION, issue_type=IssueType.EPIC),
        ],
        progress={3: (1, 3), 4: (2, 5)},
    )
    overview = collect(port)
    assert {t.number: t.status for t in overview.tickets} == {
        1: "S0: To Do",
        2: "S4: In Implementation",
        3: "S1: In Analysis",
        4: "S4: In Implementation",
    }
    assert {(p.number, p.closed, p.total) for p in overview.progress} == {(3, 1, 3), (4, 2, 5)}
    row = next(t for t in overview.tickets if t.number == 3)
    assert (row.issue_type, row.size, row.priority) == ("Question", "L", "Medium")


def test_ready_requires_the_guard_status_and_no_blocker() -> None:
    port = FakePort(
        [
            ticket(1),  # XS in S0
            ticket(2, status=Status.IN_IMPLEMENTATION),  # XS in S4
            ticket(3, size=Size.S, solution=LINK.format(n=3)),  # S in S0 with scope attachment
            ticket(4, size=Size.S),  # S without a scope attachment
            ticket(
                5,
                size=Size.M,
                status=Status.READY_FOR_IMPLEMENTATION,
                solution=SELECTED.format(n=5),
            ),
            ticket(6, size=Size.M, status=Status.IN_ANALYSIS, solution=SELECTED.format(n=6)),
            ticket(7, size=Size.XL, issue_type=IssueType.EPIC),
            ticket(8, solution="_Pending._"),
            ticket(9, size=Size.M, status=Status.IN_IMPLEMENTATION, solution=SELECTED.format(n=9)),
        ]
    )
    assert [r.number for r in collect(port).ready] == [1, 3, 5]


def test_blocked_ticket_lists_open_blockers_and_is_not_ready() -> None:
    port = FakePort([ticket(1, blocked_by=[20, 21]), ticket(2)])
    overview = collect(port)
    assert [(b.number, b.blockers) for b in overview.blocked] == [(1, (20, 21))]
    assert [r.number for r in overview.ready] == [2]


def test_waiting_for_selection_needs_analysis_and_no_selected_marker() -> None:
    port = FakePort(
        [
            ticket(1, size=Size.M, status=Status.IN_ANALYSIS, analysis="Options"),
            ticket(2, size=Size.M, status=Status.IN_ANALYSIS, analysis="_Pending._"),
            ticket(3, size=Size.M, status=Status.IN_ANALYSIS, analysis=""),
            ticket(
                4,
                size=Size.M,
                status=Status.READY_FOR_IMPLEMENTATION,
                analysis="Options",
                solution=SELECTED.format(n=4),
            ),
            ticket(5, analysis="Small tickets carry their solution"),
            ticket(
                6,
                size=Size.L,
                status=Status.IN_ANALYSIS,
                issue_type=IssueType.QUESTION,
                analysis="Options",
            ),
        ],
        progress={6: (0, 2)},
    )
    overview = collect(port)
    assert [w.number for w in overview.waiting] == [1, 6]
    assert [r.number for r in overview.ready] == [4, 5]


def test_a_blocked_ticket_is_listed_once() -> None:
    port = FakePort(
        [ticket(1, size=Size.M, status=Status.IN_ANALYSIS, analysis="x", blocked_by=[2])]
    )
    overview = collect(port)
    assert [b.number for b in overview.blocked] == [1]
    assert overview.waiting == () and overview.ready == ()


def test_pull_request_is_approved_only_for_the_current_head() -> None:
    port = FakePort([], prs=[pr(7, "a" * 40), pr(8, "c" * 40), pr(9, "d" * 40)])
    overview = collect(
        port, approvals=[approval("7", "a" * 40), approval("8", "b" * 40), approval("70", "d" * 40)]
    )
    assert {p.number: p.approved for p in overview.pull_requests} == {7: True, 8: False, 9: False}
    assert (overview.pull_requests[0].passing, overview.pull_requests[0].failing) == (1, 0)


def test_unfinished_runs_are_for_this_repository_newest_first_and_capped() -> None:
    runs = [
        run_record(1, 5, "completed", 1),
        run_record(2, 6, "waiting_for_decision", 90),
        run_record(3, 7, "interrupted", 5),
        run_record(4, 8, "running", 1, repo="other/repo"),
        *(run_record(10 + i, 20 + i, "failed", 200 + i) for i in range(25)),
    ]
    overview = collect(FakePort([]), runs=runs)
    assert len(overview.runs) == 20
    assert [r.id for r in overview.runs[:2]] == [3, 2]
    assert overview.runs[0].age_seconds == 300
    assert all(r.id not in {1, 4} for r in overview.runs)
    assert {r.state for r in overview.runs} <= {"interrupted", "waiting_for_decision", "failed"}


def test_recent_merged_and_closed_are_carried() -> None:
    overview = collect(FakePort([]))
    assert [(m.number, m.title) for m in overview.merged] == [(30, "merged thirty")]
    assert [(c.number, c.title) for c in overview.closed] == [(31, "closed thirty-one")]


def test_truncation_and_unreadable_tickets_do_not_abort_the_report() -> None:
    port = FakePort(
        [ticket(1), ticket(2)],
        bad={3: "Issue 3 is absent from project 2"},
        truncated=True,
    )
    overview = collect(port, limit=3)
    assert port.limit_seen == 3
    assert overview.truncated is True and overview.limit == 3
    assert overview.unreadable == ((3, "Issue 3 is absent from project 2"),)
    assert [t.number for t in overview.tickets] == [1, 2]
    assert [r.number for r in overview.ready] == [1, 2]


def test_progress_failure_is_reported_without_dropping_the_ticket() -> None:
    port = FakePort([ticket(1, size=Size.XL, issue_type=IssueType.EPIC)], progress={})
    overview = collect(port)
    assert [t.number for t in overview.tickets] == [1]
    assert overview.progress == ()
    assert overview.unreadable[0][0] == 1 and "children" in overview.unreadable[0][1]


def test_empty_repository_has_no_rows() -> None:
    overview = collect(FakePort([]))
    assert overview.tickets == () and overview.pull_requests == () and overview.runs == ()
    assert overview.ready == () and overview.blocked == () and overview.waiting == ()


def test_offline_overview_holds_only_local_runs() -> None:
    runs = [run_record(1, 5, "running", 2), run_record(2, 6, "completed", 3)]
    overview = offline_overview(runs, repo=REPO, now=NOW, reason="gh not found")
    assert overview.offline == "gh not found"
    assert [r.id for r in overview.runs] == [1]
    assert overview.tickets == () and overview.pull_requests == ()
