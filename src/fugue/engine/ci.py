"""Helpers that read and summarize checks and classify check failures."""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from fugue.github import CheckResult
from fugue.github.client import EmptyJSONReply

GREEN_BUCKETS = frozenset({"pass", "skipping"})
FAILED_BUCKETS = frozenset({"fail", "cancel"})


_RUN_SEGMENT = re.compile(r"/actions/runs/(\d+)")


async def read_checks(read: Callable[[], Awaitable[list[CheckResult]]]) -> list[CheckResult]:
    """Try a monitor read at most three times, retrying only empty JSON replies."""
    for _ in range(2):
        try:
            return await read()
        except EmptyJSONReply:
            pass
    return await read()


def actions_run_key(link: str) -> str:
    """The Actions run id in a check link, or the link itself when it names no run."""
    match = _RUN_SEGMENT.search(link)
    return link if match is None else match.group(1)


class FailureKind(StrEnum):
    INFRASTRUCTURE = "infrastructure"
    SUBSTANTIVE = "substantive"


@dataclass(frozen=True)
class CheckSummary:
    green: tuple[CheckResult, ...]
    failed: tuple[CheckResult, ...]
    pending: tuple[CheckResult, ...]

    @property
    def is_green(self) -> bool:
        """True when checks exist and every one passed or was skipped."""
        return bool(self.green) and not self.failed and not self.pending


def without_ignored(checks: Sequence[CheckResult], ignored: Sequence[str]) -> list[CheckResult]:
    """Drop checks whose name contains an ignored fragment (case-insensitive)."""
    return [
        check
        for check in checks
        if not any(fragment in check.name.casefold() for fragment in ignored)
    ]


def summarize_checks(checks: Sequence[CheckResult]) -> CheckSummary:
    """Split checks by bucket; any bucket not known to be green or failed is still pending."""
    green: list[CheckResult] = []
    failed: list[CheckResult] = []
    pending: list[CheckResult] = []
    for check in checks:
        bucket = check.bucket.strip().casefold()
        if bucket in GREEN_BUCKETS:
            green.append(check)
        elif bucket in FAILED_BUCKETS:
            failed.append(check)
        else:
            pending.append(check)
    return CheckSummary(tuple(green), tuple(failed), tuple(pending))


def classify_failure(excerpt: str, markers: Sequence[str]) -> FailureKind:
    """Infrastructure when the log excerpt contains any marker, ignoring case."""
    text = excerpt.casefold()
    if any(marker.strip().casefold() in text for marker in markers if marker.strip()):
        return FailureKind.INFRASTRUCTURE
    return FailureKind.SUBSTANTIVE


def ci_repair_prompt(failures: Sequence[tuple[str, str]]) -> str:
    """Render a repair prompt from (check name, log excerpt) pairs."""
    sections = "\n\n".join(f"Check: {name}\n{excerpt}" for name, excerpt in failures)
    return (
        "Repair the CI failure. Change any file the repair needs. No git commit, push or gh. "
        "A check that asks for a `Test-change` trailer is satisfied by ending your final "
        "message with one line `Test-change: <why the assertion changes>`; the orchestrator "
        "writes the trailer.\n\n" + sections
    )
