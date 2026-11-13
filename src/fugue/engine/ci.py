"""Pure helpers that summarize checks and classify check failures."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from fugue.github import CheckResult

GREEN_BUCKETS = frozenset({"pass", "skipping"})
FAILED_BUCKETS = frozenset({"fail", "cancel"})


_RUN_SEGMENT = re.compile(r"/actions/runs/(\d+)")


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
        "Repair the CI failure within the allowed scope. No git commit, push or gh.\n\n" + sections
    )
