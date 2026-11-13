"""The GitHub operations consumed by the engine."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from fugue.github import CheckResult, PullRequest
from fugue.github.client import PostedReview, PrState, ReviewComment, ReviewThread
from fugue.models import Disposition, Status, Ticket


class GitHubPort(Protocol):
    repo: str

    async def get_ticket(self, number: int) -> Ticket: ...

    async def set_status(self, number: int, status: Status) -> None: ...

    async def comment(self, number: int, body: str) -> None: ...

    async def find_pr(self, branch: str) -> PullRequest | None: ...

    async def create_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        assignee: str,
        milestone: str,
        project_title: str,
    ) -> PullRequest: ...

    async def download_attachment(self, url: str, dest: Path) -> Path: ...


@runtime_checkable
class CiGitHubPort(GitHubPort, Protocol):
    """The GitHub operations needed to monitor and repair continuous integration."""

    async def checks(self, pr: PullRequest) -> list[CheckResult]: ...

    async def failed_log_tail(self, run_link: str, max_lines: int = 200) -> str: ...

    async def rerun_failed(self, run_link: str) -> None: ...


@runtime_checkable
class ReviewGitHubPort(CiGitHubPort, Protocol):
    """The GitHub operations needed to post reviews and answer their threads."""

    async def reviews(self, pr: PullRequest) -> list[PostedReview]: ...

    async def post_review(
        self,
        pr: PullRequest,
        head_sha: str,
        body: str,
        comments: Sequence[ReviewComment] = (),
        event: str = "COMMENT",
    ) -> PostedReview: ...

    async def review_threads(self, pr: PullRequest) -> list[ReviewThread]: ...

    async def unresolved_thread_count(self, pr: PullRequest) -> int: ...

    async def reply_to_thread(self, pr: PullRequest, comment_id: int, body: str) -> None: ...

    async def resolve_thread(self, thread_id: str) -> None: ...


@runtime_checkable
class MergeGitHubPort(CiGitHubPort, Protocol):
    """Live merge guards and closure mutations."""

    async def pr_head_sha(self, pr: PullRequest) -> str: ...

    async def pr_state(self, pr: PullRequest) -> PrState: ...

    async def unresolved_thread_count(self, pr: PullRequest) -> int: ...

    async def merge(self, pr: PullRequest, expected_head_sha: str) -> None: ...

    async def set_disposition(self, number: int, disposition: Disposition) -> None: ...

    async def children(self, number: int) -> list[Ticket]: ...

    async def delete_remote_branch(self, branch: str, repo_root: Path) -> None: ...
