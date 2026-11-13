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

    def render(self) -> str:
        message = (
            f"{self.issue_type}[#{self.number}]: {self.explanation}\n\n**Summary**\n{self.summary}"
        )
        if self.changes:
            message += "\n\n**Changes**\n" + "\n".join(f"- {change}" for change in self.changes)
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
