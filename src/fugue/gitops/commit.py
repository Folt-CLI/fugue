"""Commit message formatting and validation of commit contents."""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase


@dataclass(frozen=True)
class CommitMessage:
    issue_type: str
    number: int
    explanation: str
    summary: str
    changes: tuple[str, ...] = ()
    test_change: str | None = None

    def render(self) -> str:
        message = (
            f"{self.issue_type}[#{self.number}]: {self.explanation}\n\n**Summary**\n{self.summary}"
        )
        if self.changes:
            message += "\n\n**Changes**\n" + "\n".join(f"- {change}" for change in self.changes)
        if self.test_change:
            message += f"\n\nTest-change: {self.test_change}"
        return message


def validate_message(
    m: CommitMessage, issue_types: Collection[str], max_subject: int = 72
) -> list[str]:
    problems: list[str] = []
    if m.issue_type not in issue_types:
        problems.append(f"Unknown issue type: {m.issue_type}")
    subject = f"{m.issue_type}[#{m.number}]: {m.explanation}"
    if len(subject) > max_subject:
        problems.append(f"Subject exceeds {max_subject} characters")
    if not m.explanation or not m.explanation[0].isupper():
        problems.append("Explanation must be capitalised")
    if m.explanation.rstrip().endswith("."):
        problems.append("Explanation must not end with '.'")
    if not m.summary.strip():
        problems.append("Summary must not be empty")
    for line in m.render().splitlines():
        if re.search(r"(?i)co-authored-by|generated with|signed-off-by", line):
            problems.append(f"Attribution is not allowed: {line}")
    return problems


PROTECTED_PATHS = (
    ".github/workflows/",
    ".fugue/",
    "scripts/test-integrity",
    ".env",
    "credentials",
)


def protected_paths(paths: Sequence[str], allowed: Sequence[str] = ()) -> list[str]:
    """Return the protected paths that were changed: workflows, integrity checks, state, secrets.

    A protected path the scope names explicitly in `allowed` is not returned: a ticket that
    exists to change a workflow says so in its scope.
    """
    return [
        path
        for path in paths
        if any(path.startswith(prefix) or path.endswith(prefix) for prefix in PROTECTED_PATHS)
        and path not in allowed
    ]


def validate_staging(staged: Sequence[str], allowed: Sequence[str]) -> list[str]:
    """Return staged paths outside the allowed globs or directory prefixes."""
    return [
        path
        for path in staged
        if not any(
            path.startswith(pattern) if pattern.endswith("/") else fnmatchcase(path, pattern)
            for pattern in allowed
        )
    ]
