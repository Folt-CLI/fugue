"""Journalled creation against a fake GitHub client; no call reaches GitHub."""

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from fugue.engine.facts import allowed_files, scope_url
from fugue.github.attachments import (
    AttachmentUnavailable,
    AttachmentVerificationError,
    UploadedAttachment,
)
from fugue.github.client import GitHubError
from fugue.github.creation import IssueHit
from fugue.github.overview import ClosedIssue, MergedPr, OverviewPr
from fugue.github.parse import parse_body_sections
from fugue.models import Component, IssueType, Priority, Size, Status, Ticket
from fugue.tickets import DraftStore, TicketDraft, create_ticket
from fugue.tickets.draft import Turn

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"


def epic(number: int = 1) -> Ticket:
    return Ticket(
        number=number,
        title="0.1.0 -- Fugue",
        issue_type=IssueType.EPIC,
        size=Size.XL,
        status=Status.IN_IMPLEMENTATION,
        priority=Priority.MEDIUM,
    )


class FakeGitHub:
    """Records every write; reads come from plain attributes."""

    repo = "o/r"

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.issues: dict[int, dict[str, Any]] = {}
        self.uploads: dict[str, bytes] = {}
        self.fail: dict[str, BaseException] = {}
        self.states: dict[int, str] = {1: "open"}
        self.tickets: dict[int, Ticket] = {}
        self.hits: list[IssueHit] = []
        self.milestones = ["Backlog", "0.1.0"]
        self.next_number = 100

    def _step(self, name: str, *details: Any) -> None:
        self.calls.append((name, *details))
        error = self.fail.pop(name, None)
        if error is not None:
            raise error

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    # Creation port ------------------------------------------------------------------

    async def find_child(self, parent: int, marker: str) -> int | None:
        for number, issue in self.issues.items():
            if issue["parent"] == parent and marker in issue["body"]:
                return number
        return None

    async def create_issue(
        self,
        title: str,
        body: str,
        issue_type: IssueType,
        parent: int,
        milestone: str,
        assignee: str,
        *,
        add_to_project: bool = True,
    ) -> int:
        self._step("create_issue", title)
        number = self.next_number
        self.next_number += 1
        self.issues[number] = {
            "title": title,
            "body": body,
            "type": issue_type,
            "parent": parent,
            "milestone": milestone,
            "assignee": assignee,
            "add_to_project": add_to_project,
            "blockers": [],
        }
        return number

    async def ensure_project(self, number: int) -> None:
        self._step("ensure_project", number)

    async def set_size(self, number: int, size: Size) -> None:
        self._step("set_size", number)
        self.issues[number]["size"] = size

    async def set_status(self, number: int, status: Status) -> None:
        self._step("set_status", number)
        self.issues[number]["status"] = status

    async def set_priority(self, number: int, priority: Priority) -> None:
        self._step("set_priority", number)
        self.issues[number]["priority"] = priority

    async def set_components(self, number: int, components: Sequence[Component]) -> None:
        self._step("set_components", number)
        self.issues[number]["components"] = list(components)

    async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]:
        self._step("upload_attachments", [path.name for path in paths])
        uploaded = []
        for path in paths:
            data = path.read_bytes()
            self.uploads[path.name] = data
            href = f"https://github.com/user-attachments/files/{len(self.uploads)}/{path.name}"
            uploaded.append(UploadedAttachment(path.name, href, hashlib.sha256(data).hexdigest()))
        return uploaded

    async def edit_issue_body(self, number: int, body: str) -> None:
        self._step("edit_issue_body", number)
        self.issues[number]["body"] = body

    async def block(self, number: int, blocker: int) -> None:
        self._step("block", number, blocker)
        self.issues[number]["blockers"].append(blocker)

    # Reads for the session ----------------------------------------------------------

    async def current_login(self) -> str:
        return "me"

    async def milestone_titles(self) -> list[str]:
        return list(self.milestones)

    async def search_issues(self, query: str, limit: int) -> list[IssueHit]:
        return self.hits[:limit]

    async def issue_state(self, number: int) -> str | None:
        return self.states.get(number)

    async def list_open_issue_numbers(self, limit: int) -> tuple[list[int], bool]:
        return list(self.tickets)[:limit], len(self.tickets) > limit

    async def get_ticket(self, number: int) -> Ticket:
        if number in self.tickets:
            return self.tickets[number]
        if number == 1:
            return epic()
        raise ValueError(f"No ticket {number}")

    async def open_pull_requests(self) -> list[OverviewPr]:
        return []

    async def recent_merged_pull_requests(self, count: int) -> list[MergedPr]:
        return []

    async def recent_closed_issues(self, count: int) -> list[ClosedIssue]:
        return []

    async def sub_issue_progress(self, number: int) -> tuple[int, int]:
        return 0, 0


def draft_s(**changes: Any) -> TicketDraft:
    values: dict[str, Any] = {
        "title": "Add export command",
        "issue_type": IssueType.FEATURE,
        "size": Size.S,
        "priority": Priority.HIGH,
        "summary": "Export tickets as CSV.",
        "scope_text": "Add one export command.",
        "allowed_files": ["src/export.py", "tests/test_export.py"],
        "new_files": ["src/export.py", "tests/test_export.py"],
        "components": [Component.SOURCE_CODE],
        "decision": "Add the command.",
        "agreed": ["CSV only."],
        "you_get": ["An export command."],
        "changes": ["New module."],
        "not_changed": ["Import."],
        "watch_out": ["Large repositories."],
        "in_scope_scenarios": ["Export writes a file."],
        "out_of_scope_scenarios": ["Import."],
        "milestone": "Backlog",
        "assignee": "me",
        "confirmed": True,
    }
    values.update(changes)
    draft = TicketDraft(**values)
    draft.add_turn("fugue", "Describe your idea:")
    draft.add_turn("you", "Export tickets")
    return draft


def make(tmp_path: Path, **changes: Any) -> tuple[TicketDraft, DraftStore, FakeGitHub]:
    return draft_s(**changes), DraftStore(tmp_path / "state"), FakeGitHub()


def run(draft: TicketDraft, github: FakeGitHub, store: DraftStore) -> Any:
    return asyncio.run(create_ticket(draft, github, store))


def body_ticket(number: int, body: str) -> Ticket:
    return Ticket(
        number=number,
        title="t",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        body_sections=parse_body_sections(body),
    )


def test_s_creation_sets_every_field_and_round_trips_the_body(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path)
    created = run(draft, github, store)
    issue = github.issues[100]
    assert (issue["type"], issue["parent"], issue["milestone"], issue["assignee"]) == (
        IssueType.FEATURE,
        1,
        "Backlog",
        "me",
    )
    assert issue["add_to_project"] is False
    assert (issue["size"], issue["status"], issue["priority"]) == (
        Size.S,
        Status.TO_DO,
        Priority.HIGH,
    )
    assert issue["components"] == [Component.SOURCE_CODE]
    assert set(github.uploads) == {"100-solution-1.md", "100-conversation.md"}
    assert github.names()[-1] == "edit_issue_body"
    assert created.number == 100
    assert created.next_command == "fugue run 100"
    assert created.url == "https://github.com/o/r/issues/100"
    ticket = body_ticket(100, issue["body"])
    assert list(ticket.body_sections) == ["Summary", "Scope", "Analysis", "Solution", "Details"]
    assert ticket.body_sections["Analysis"] == "No analysis needed."
    url = scope_url(ticket)
    assert url is not None and url.endswith("/100-solution-1.md")
    assert "100-conversation.md" in ticket.body_sections["Details"]
    assert f"<!-- fugue:new:{draft.id} -->" in issue["body"]
    scope = github.uploads["100-solution-1.md"].decode()
    assert allowed_files(scope) == draft.allowed_files
    assert draft.created_number == 100


@pytest.mark.parametrize("size", [Size.XS, Size.S])
def test_uploaded_scope_preserves_confirmed_requirements(tmp_path: Path, size: Size) -> None:
    """Scenario 1: the implementer receives the confirmed behavior and boundaries."""
    draft, store, github = make(
        tmp_path,
        size=size,
        title="Add export",
        scope_text="CSV only; never overwrite existing output",
        decision="Add a --csv command",
        changes=["src/export.py: refuse an existing output path before writing CSV"],
    )
    run(draft, github, store)
    scope = github.uploads["100-solution-1.md"].decode("utf-8")
    assert draft.scope_text in scope
    assert draft.decision in scope
    for requirement in (
        *draft.changes,
        *draft.agreed,
        *draft.you_get,
        *draft.not_changed,
        *draft.watch_out,
    ):
        assert requirement in scope
    assert allowed_files(scope) == draft.allowed_files


def test_agreed_in_names_a_question_parent(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path, parent=89, parent_is_question=True)
    github.states[89] = "open"
    run(draft, github, store)
    assert "**Agreed in #89**" in github.issues[100]["body"]
    other, store2, github2 = make(tmp_path)
    run(other, github2, store2)
    assert "**Agreed in the ticket conversation**" in github2.issues[100]["body"]


def test_m_has_pending_sections_no_components_and_no_scope(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path, size=Size.M, components=[Component.TESTS])
    created = run(draft, github, store)
    issue = github.issues[100]
    sections = parse_body_sections(issue["body"])
    assert sections["Analysis"] == "_Pending._"
    assert sections["Solution"] == "_Pending._"
    assert sections["Summary"] == "Export tickets as CSV."
    assert "set_components" not in github.names()
    assert "components" not in issue
    assert issue["status"] == Status.TO_DO
    assert set(github.uploads) == {"100-conversation.md"}
    assert created.next_command == "fugue run 100"


def test_l_is_created_as_a_question(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path, size=Size.L, issue_type=IssueType.FEATURE)
    run(draft, github, store)
    assert github.issues[100]["type"] == IssueType.QUESTION
    assert github.issues[100]["size"] == Size.L
    assert set(github.uploads) == {"100-conversation.md"}


def test_blockers_are_linked_natively(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path, blocked_by=[7, 8])
    run(draft, github, store)
    assert github.issues[100]["blockers"] == [7, 8]
    assert github.names()[-2:] == ["block", "block"]


def test_restart_after_issue_creation_does_not_create_a_second_issue(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path)
    github.fail["edit_issue_body"] = GitHubError(["gh"], "boom")
    with pytest.raises(GitHubError):
        run(draft, github, store)
    saved = store.load(draft.id)
    assert "issue" in saved.journal and "body" not in saved.journal
    github.calls.clear()
    created = run(saved, github, store)
    assert created.number == 100
    assert github.names() == ["edit_issue_body"]
    assert len(github.issues) == 1
    assert scope_url(body_ticket(100, github.issues[100]["body"])) is not None


def test_restart_before_the_journal_finds_the_issue_by_marker(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path)
    run(draft, github, store)
    draft.journal.clear()
    draft.created_number = None
    github.calls.clear()
    run(draft, github, store)
    assert "create_issue" not in github.names()
    assert len(github.issues) == 1


@pytest.mark.parametrize(
    "error",
    [
        AttachmentUnavailable(["gh", "attach"], "no signed in session"),
        AttachmentVerificationError("Attachment bytes differ from source: x"),
    ],
)
def test_attachment_failure_leaves_the_ticket_without_a_scope_link(
    tmp_path: Path, error: BaseException
) -> None:
    draft, store, github = make(tmp_path)
    github.fail["upload_attachments"] = error
    with pytest.raises(type(error)):
        run(draft, github, store)
    ticket = body_ticket(100, github.issues[100]["body"])
    assert scope_url(ticket) is None
    assert "attachments" not in store.load(draft.id).journal
    run(store.load(draft.id), github, store)
    assert scope_url(body_ticket(100, github.issues[100]["body"])) is not None
    assert len(github.issues) == 1


def test_conversation_attachment_redacts_secrets_and_keeps_turns(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path)
    draft.add_turn("you", f"my token is {TOKEN} and Bearer abcdefgh12345678")
    draft.add_turn("you", "key AKIA" + "ABCDEFGHIJKLMNOP and sk-" + "x" * 24)
    draft.turns.append(Turn(speaker="you", text=f"raw {TOKEN}"))
    run(draft, github, store)
    text = github.uploads["100-conversation.md"].decode("utf-8")
    assert TOKEN not in text
    assert "abcdefgh12345678" not in text
    assert "AKIAABCDEFGHIJKLMNOP" not in text
    assert "[redacted]" in text
    assert "you: Export tickets" in text
    assert "fugue: Describe your idea:" in text
    assert "# Conversation for ticket #100" in text
    assert "confirmed with yes" in text
    assert "## Final preview" in text


def test_journal_is_saved_after_each_step(tmp_path: Path) -> None:
    draft, store, github = make(tmp_path)
    github.fail["set_priority"] = GitHubError(["gh"], "boom")
    with pytest.raises(GitHubError):
        run(draft, github, store)
    journal = store.load(draft.id).journal
    assert set(journal) == {"issue", "project", "size", "status"}
