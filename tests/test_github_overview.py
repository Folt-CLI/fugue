"""Overview GitHub reads against a fake gh runner; no network and no real gh."""

import asyncio
import functools
import json
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any

import pytest

from fugue.github.overview import OverviewGitHubClient, check_bucket
from fugue.process import CommandResult

REPO = "example/fugue"


def sync[**P, T](test: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return asyncio.run(test(*args, **kwargs))

    return wrapper


class FakeGh:
    """Return JSON per call from a function of the gh arguments; record every call."""

    def __init__(self, answer: Callable[[tuple[str, ...]], object]) -> None:
        self.answer = answer
        self.calls: list[tuple[str, ...]] = []

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        stdin: str | None = None,
        max_output_bytes: int = 65536,
    ) -> CommandResult:
        args = tuple(argv)[1:]
        self.calls.append(args)
        return CommandResult(
            argv=tuple(argv),
            returncode=0,
            timed_out=False,
            cancelled=False,
            stdout=json.dumps(self.answer(args)),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            callback_line_truncated=False,
            full_output_path=None,
            duration_seconds=0.0,
        )


def client(gh: FakeGh) -> OverviewGitHubClient:
    return OverviewGitHubClient(REPO, "example", 2, runner=gh, strip_tokens=False)


def issue(number: int, *, pull: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {"number": number, "title": f"issue {number}"}
    if pull:
        item["pull_request"] = {"url": "x"}
    return item


@sync
async def test_open_issue_numbers_drop_pull_requests_and_report_truncation() -> None:
    gh = FakeGh(lambda args: [issue(n, pull=n % 2 == 0) for n in range(1, 101)])
    numbers, truncated = await client(gh).list_open_issue_numbers(10)
    assert numbers == [1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    assert truncated is True
    assert gh.calls[0][0] == "api"
    assert gh.calls[0][1] == f"repos/{REPO}/issues?state=open&per_page=100&page=1"
    assert "-f" not in gh.calls[0] and "-X" not in gh.calls[0]


@sync
async def test_open_issue_numbers_stop_paging_after_limit_plus_one() -> None:
    pages = {1: list(range(1, 101)), 2: list(range(101, 201)), 3: list(range(201, 301))}

    def answer(args: tuple[str, ...]) -> object:
        page = int(args[1].rsplit("page=", 1)[1])
        return [issue(n) for n in pages[page]]

    gh = FakeGh(answer)
    numbers, truncated = await client(gh).list_open_issue_numbers(100)
    assert len(numbers) == 100 and truncated is True
    assert len(gh.calls) == 2


@sync
async def test_open_issue_numbers_exactly_at_limit_is_not_truncated() -> None:
    gh = FakeGh(lambda args: [issue(n) for n in range(1, 4)])
    numbers, truncated = await client(gh).list_open_issue_numbers(3)
    assert (numbers, truncated) == ([1, 2, 3], False)
    assert len(gh.calls) == 1


@pytest.mark.parametrize(
    ("entry", "bucket"),
    [
        ({"conclusion": "SUCCESS"}, "pass"),
        ({"conclusion": "NEUTRAL"}, "pass"),
        ({"conclusion": "SKIPPED"}, "pass"),
        ({"state": "SUCCESS"}, "pass"),
        ({"conclusion": "FAILURE"}, "fail"),
        ({"conclusion": "CANCELLED"}, "fail"),
        ({"conclusion": "TIMED_OUT"}, "fail"),
        ({"conclusion": "ACTION_REQUIRED"}, "fail"),
        ({"state": "FAILURE"}, "fail"),
        ({"state": "ERROR"}, "fail"),
        ({"status": "IN_PROGRESS", "conclusion": ""}, "pending"),
        ({"state": "PENDING"}, "pending"),
        ({}, "pending"),
    ],
)
def test_check_bucket(entry: dict[str, Any], bucket: str) -> None:
    assert check_bucket(entry) == bucket


@sync
async def test_open_pull_requests_count_checks() -> None:
    gh = FakeGh(
        lambda args: [
            {
                "number": 7,
                "title": "Add overview",
                "headRefOid": "a" * 40,
                "headRefName": "feature/96",
                "baseRefName": "develop",
                "isDraft": False,
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [
                    {"conclusion": "SUCCESS"},
                    {"conclusion": "FAILURE"},
                    {"state": "PENDING"},
                    {"conclusion": "SKIPPED"},
                ],
            },
            {
                "number": 8,
                "title": "No checks",
                "headRefOid": "b" * 40,
                "headRefName": "x",
                "baseRefName": "develop",
                "isDraft": True,
                "reviewDecision": "",
                "statusCheckRollup": None,
            },
        ]
    )
    prs = await client(gh).open_pull_requests()
    assert [(p.number, p.passing, p.failing, p.pending) for p in prs] == [
        (7, 2, 1, 1),
        (8, 0, 0, 0),
    ]
    assert prs[0].head_sha == "a" * 40 and prs[0].review_decision == "APPROVED"
    assert prs[1].draft is True and prs[1].review_decision == "none"
    call = gh.calls[0]
    assert call[:2] == ("pr", "list") and "--repo" in call and REPO in call
    assert call[call.index("--state") + 1] == "open"
    assert "statusCheckRollup" in call[call.index("--json") + 1]


@sync
async def test_recent_merged_pull_requests_and_closed_issues() -> None:
    def answer(args: tuple[str, ...]) -> object:
        if args[0] == "pr":
            return [{"number": 3, "title": "merged three", "mergedAt": "2026-01-02T03:04:05Z"}]
        return [issue(1), issue(2, pull=True), issue(3), issue(4)]

    gh = FakeGh(answer)
    merged = await client(gh).recent_merged_pull_requests(5)
    assert [(m.number, m.merged_at) for m in merged] == [(3, "2026-01-02T03:04:05Z")]
    assert gh.calls[0][gh.calls[0].index("--limit") + 1] == "5"
    assert gh.calls[0][gh.calls[0].index("--state") + 1] == "merged"
    closed = await client(gh).recent_closed_issues(2)
    assert [c.number for c in closed] == [1, 3]
    assert gh.calls[1][1] == (
        f"repos/{REPO}/issues?state=closed&sort=updated&direction=desc&per_page=12"
    )


@sync
async def test_sub_issue_progress_counts_closed_without_reading_children() -> None:
    gh = FakeGh(
        lambda args: [
            [{"number": 1, "state": "closed"}, {"number": 2, "state": "open"}],
            [{"number": 3, "state": "closed"}],
        ]
    )
    assert await client(gh).sub_issue_progress(40) == (2, 3)
    assert len(gh.calls) == 1
    assert gh.calls[0][1] == f"repos/{REPO}/issues/40/sub_issues"
