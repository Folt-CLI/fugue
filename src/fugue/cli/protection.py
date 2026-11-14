"""Read branch protection without changing repository settings."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from fugue.github.client import GitHubError


class ProtectionClient(Protocol):
    repo: str

    async def _json(self, *args: str) -> Any: ...


@dataclass(frozen=True)
class BranchStatus:
    branch: str
    required_approving_review_count: bool = False
    dismiss_stale_reviews: bool = False
    required_conversation_resolution: bool = False
    reason: str | None = None
    unreadable: bool = False

    @property
    def satisfied(self) -> bool:
        return (
            self.reason is None
            and self.required_approving_review_count
            and self.dismiss_stale_reviews
            and self.required_conversation_resolution
        )


async def check_protection(client: ProtectionClient, branches: Sequence[str]) -> list[BranchStatus]:
    """Check the three recommended rules, preserving requested branch order."""
    statuses: list[BranchStatus] = []
    for branch in branches:
        endpoint = f"repos/{client.repo}/branches/{quote(branch, safe='')}/protection"
        try:
            data = await client._json("api", endpoint, "--method", "GET")
        except GitHubError as error:
            message = error.stderr_tail.lower()
            if "404" in message and "branch not protected" in message:
                statuses.append(BranchStatus(branch, reason="Branch not protected"))
            elif "403" in message:
                statuses.append(
                    BranchStatus(branch, reason="Permission refused (403)", unreadable=True)
                )
            elif "404" in message:
                statuses.append(
                    BranchStatus(
                        branch,
                        reason="Protection inaccessible or branch/repository missing (404)",
                        unreadable=True,
                    )
                )
            else:
                raise
            continue
        if not isinstance(data, dict):
            raise ValueError(f"Invalid protection response for branch {branch}")
        reviews = data.get("required_pull_request_reviews") or {}
        resolution = data.get("required_conversation_resolution") or {}
        if not isinstance(reviews, dict) or not isinstance(resolution, dict):
            raise ValueError(f"Invalid protection rules for branch {branch}")
        count = reviews.get("required_approving_review_count")
        statuses.append(
            BranchStatus(
                branch,
                required_approving_review_count=type(count) is int and count >= 1,
                dismiss_stale_reviews=reviews.get("dismiss_stale_reviews") is True,
                required_conversation_resolution=resolution.get("enabled") is True,
            )
        )
    return statuses
