"""Render implementation instructions and delivery text."""

from collections.abc import Sequence

from fugue.engine.facts import explanation
from fugue.gitops import CommitMessage
from fugue.models import Ticket


def implementation_prompt(scope_text: str) -> str:
    return "Implement only the scope below. No git commit, push or gh.\n\n" + scope_text


def repair_prompt(failure_text: str) -> str:
    return (
        "Repair the gate failure within the allowed scope. No git commit, push or gh.\n\n"
        + failure_text
    )


def pr_body(ticket: Ticket, summary: str, changes: Sequence[str], verification: str) -> str:
    assignee = ", ".join(ticket.assignees) or "Bojan-Ivanovski"
    return (
        f"## Summary\n\n{summary}\n\n## Changes\n\n"
        + "\n".join(f"- {change}" for change in changes)
        + f"\n\n## Related work\n\nCloses #{ticket.number}\n\n## Metadata\n\n"
        + f"Milestone: {ticket.milestone or '0.1.0'}\nAssignee: {assignee}\nProject: Fugue"
        + f"\n\n## Verification\n\n{verification}\n\n## Risks and follow-up\n\n"
        + "CI and required review remain pending. Merge requires approval for the exact head SHA."
    )


def commit_message(ticket: Ticket, summary: str, changes: Sequence[str]) -> CommitMessage:
    return CommitMessage(
        ticket.issue_type, ticket.number, explanation(ticket), summary, tuple(changes)
    )
