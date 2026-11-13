"""Read-only GitHub operations for the repository overview."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fugue.github.client import GitHubClient

_PAGE_SIZE = 100
_PASS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
_FAIL = frozenset({"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR"})
_PR_FIELDS = (
    "number,title,headRefOid,headRefName,baseRefName,isDraft,reviewDecision,statusCheckRollup"
)


@dataclass(frozen=True)
class OverviewPr:
    number: int
    title: str
    head_sha: str
    head_ref: str
    base_ref: str
    draft: bool
    review_decision: str
    passing: int
    failing: int
    pending: int


@dataclass(frozen=True)
class MergedPr:
    number: int
    title: str
    merged_at: str


@dataclass(frozen=True)
class ClosedIssue:
    number: int
    title: str


def check_bucket(entry: Mapping[str, Any]) -> str:
    """Classify one status check rollup entry as pass, fail or pending."""
    conclusion = str(entry.get("conclusion") or "").upper()
    state = str(entry.get("state") or "").upper()
    if conclusion in _PASS or state == "SUCCESS":
        return "pass"
    if conclusion in _FAIL or state in {"FAILURE", "ERROR"}:
        return "fail"
    return "pending"


def _overview_pr(item: Mapping[str, Any]) -> OverviewPr:
    buckets = [check_bucket(entry) for entry in item.get("statusCheckRollup") or []]
    return OverviewPr(
        number=int(item["number"]),
        title=str(item.get("title") or ""),
        head_sha=str(item.get("headRefOid") or ""),
        head_ref=str(item.get("headRefName") or ""),
        base_ref=str(item.get("baseRefName") or ""),
        draft=bool(item.get("isDraft")),
        review_decision=str(item.get("reviewDecision") or "none"),
        passing=buckets.count("pass"),
        failing=buckets.count("fail"),
        pending=buckets.count("pending"),
    )


class OverviewGitHubClient(GitHubClient):
    """Reads for the repository overview; no method here changes anything on GitHub."""

    async def list_open_issue_numbers(self, limit: int) -> tuple[list[int], bool]:
        """Open issue numbers up to limit, and whether more exist.

        Paging stops once limit + 1 issues are seen. The issues endpoint also returns pull
        requests; those are dropped.
        """
        numbers: list[int] = []
        page = 1
        while len(numbers) <= limit:
            items = await self._json(
                "api", f"repos/{self.repo}/issues?state=open&per_page={_PAGE_SIZE}&page={page}"
            )
            numbers.extend(int(item["number"]) for item in items if "pull_request" not in item)
            if len(items) < _PAGE_SIZE:
                break
            page += 1
        return numbers[:limit], len(numbers) > limit

    async def open_pull_requests(self) -> list[OverviewPr]:
        items = await self._json(
            "pr",
            "list",
            "--repo",
            self.repo,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            _PR_FIELDS,
        )
        return [_overview_pr(item) for item in items]

    async def recent_merged_pull_requests(self, count: int) -> list[MergedPr]:
        items = await self._json(
            "pr",
            "list",
            "--repo",
            self.repo,
            "--state",
            "merged",
            "--limit",
            str(count),
            "--json",
            "number,title,mergedAt",
        )
        return [
            MergedPr(int(item["number"]), str(item.get("title") or ""), str(item["mergedAt"] or ""))
            for item in items
        ]

    async def recent_closed_issues(self, count: int) -> list[ClosedIssue]:
        items = await self._json(
            "api",
            f"repos/{self.repo}/issues?state=closed&sort=updated&direction=desc"
            f"&per_page={count + 10}",
        )
        issues = [item for item in items if "pull_request" not in item]
        return [ClosedIssue(int(item["number"]), str(item.get("title") or "")) for item in issues][
            :count
        ]

    async def sub_issue_progress(self, number: int) -> tuple[int, int]:
        """Closed and total children, read from one listing without reading each child."""
        items = await self._paginated(f"repos/{self.repo}/issues/{number}/sub_issues")
        return sum(1 for item in items if item.get("state") == "closed"), len(items)
