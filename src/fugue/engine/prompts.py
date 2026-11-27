"""Render implementation instructions and delivery text."""

import re
from collections.abc import Sequence
from pathlib import Path

from fugue.engine.facts import explanation
from fugue.gitops import CommitMessage
from fugue.models import Ticket

MAX_CHANGES = 20
MAX_CHANGE_LENGTH = 200

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_CHANGE_RE = re.compile(r"^[^:\s][^:]*?\s*:\s+\S")

CHANGES_INSTRUCTION = (
    "In your final message, end with a `## Changes` section holding one bullet per change in "
    "the form `- what changed: why it changed`. Name files and behaviour, never commands."
)


def _worktree_preamble(workdir: Path, branch: str) -> str:
    return (
        f"Your working directory is {workdir} on branch {branch}. Work only there. "
        "If the scope below names a different worktree path or branch, treat that as a "
        "placeholder and use this directory and branch.\n\n"
    )


STANDING_RULES = (
    "Read GLOSSARY.md first and use its terms exactly as defined. Use no emojis and no "
    "attribution lines. The scope lists the files you are expected to change. Change any other "
    "file the work needs and name it in your Changes section. Never change .github/workflows, "
    "scripts/test-integrity*, .fugue or credential files. This overrides any rule in AGENTS.md "
    "or a role file that limits you to the files the scope lists. Do not stop to ask: decide, "
    "do the work and report it. When an existing test assertion has to change, end your "
    "final message with one line `Test-change: <why the assertion changes>`."
)

_ORCHESTRATOR_NOTES_RE = re.compile(
    r"^##[ \t]+Notes for the orchestrator\b.*?(?=^##[ \t]|\Z)", re.MULTILINE | re.DOTALL
)


def without_orchestrator_notes(scope_text: str) -> str:
    """The scope the implementation agent reads: the orchestrator's notes are not for it."""
    match = _ORCHESTRATOR_NOTES_RE.search(scope_text)
    if match is None:
        return scope_text
    return (scope_text[: match.start()] + scope_text[match.end() :]).rstrip() + "\n"


def implementation_prompt(scope_text: str, *, workdir: Path, branch: str) -> str:
    return (
        _worktree_preamble(workdir, branch)
        + "Implement only the scope below. No git commit, push or gh.\n\n"
        + STANDING_RULES
        + "\n\n"
        + CHANGES_INSTRUCTION
        + "\n\n"
        + _expected_files(without_orchestrator_notes(scope_text))
    )


def feature_repair_prompt(task: str, *, workdir: Path, branch: str) -> str:
    """A repair on the feature branch: every child is merged there, so no scope is implemented."""
    preamble = _worktree_preamble(workdir, branch)
    return (
        preamble + "This is the feature branch: every child is already merged into it. Do only the "
        "repair below, not any child's scope. No git commit, push or gh.\n\n"
        + STANDING_RULES
        + "\n\n"
        + CHANGES_INSTRUCTION
        + "\n\n"
        + task.removeprefix(preamble)
    )


def _expected_files(scope_text: str) -> str:
    """Present the scope's file list as the expected files, not as a limit."""
    return scope_text.replace(
        "You may touch only these files",
        "Files this work is expected to change (change others when the work needs them)",
    )


def merge_conflict_prompt(feature: str, files: Sequence[str], *, workdir: Path, branch: str) -> str:
    listed = "\n".join(f"- {path}" for path in files)
    return (
        _worktree_preamble(workdir, branch)
        + f"A merge of {feature} into this branch stopped on conflicts. Resolve the files "
        "below. Keep the intent of both sides: this branch's change and the work already "
        f"merged into {feature}. Remove every conflict marker. Where one side deleted or moved "
        "a file, carry this branch's change to the file's new home and delete the old file. "
        "Change any other file the result needs to compile. No git commit, merge, push or "
        "gh; the orchestrator concludes the merge.\n\n" + listed
    )


def parse_changes(final_text: str) -> list[str]:
    """Return the bullets of the last Changes section, or [] when absent or malformed."""
    section: list[str] | None = None
    current: list[str] | None = None
    for line in final_text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading is not None:
            current = [] if heading[1].casefold() == "changes" else None
            if current is not None:
                section = current
        elif current is not None:
            current.append(line)
    if not section:
        return []
    changes: list[str] = []
    for line in section:
        if not line.strip():
            continue
        bullet = _BULLET_RE.match(line)
        if bullet is None or _CHANGE_RE.match(bullet[1]) is None:
            return []
        text = " ".join(bullet[1].split())
        if len(text) > MAX_CHANGE_LENGTH:
            text = text[: MAX_CHANGE_LENGTH - 3].rstrip() + "..."
        changes.append(text)
    return changes[:MAX_CHANGES]


def fallback_changes(paths: Sequence[str]) -> list[str]:
    """One bullet per changed path, stating only the path."""
    return [f"{path}: updated" for path in dict.fromkeys(paths)][:MAX_CHANGES]


def repair_prompt(failure_text: str, *, workdir: Path, branch: str) -> str:
    return (
        _worktree_preamble(workdir, branch)
        + "Repair the gate failure. Change any file the repair needs. No git commit, push or "
        "gh. End with `Test-change: <why>` when a test assertion has to change.\n\n" + failure_text
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
