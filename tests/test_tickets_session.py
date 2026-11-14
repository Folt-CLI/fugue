"""Scripted `new` sessions: fake input, fake GitHub and a scripted analyst."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from test_tickets_create import FakeGitHub, body_ticket

from fugue.agents import (
    AccessMode,
    AgentResult,
    Capability,
    ScriptedAdapter,
    ScriptStep,
    Unavailable,
    UsageRecord,
)
from fugue.engine.facts import scope_url
from fugue.github.attachments import AttachmentUnavailable
from fugue.github.creation import IssueHit
from fugue.models import (
    AuthenticationMode,
    Component,
    IssueType,
    Priority,
    Role,
    Size,
    Status,
    Ticket,
)
from fugue.tickets import DraftStore, TicketDraft
from fugue.tickets.session import PATCH_SCHEMA, NewSession, parse_field

IDEA = "Add an export command"
FULL = [IDEA, "Export tickets as CSV", "Maintainers", "CSV only", "S"]
FILL: dict[str, Any] = {
    "title": "Add export command",
    "issue_type": "Feature",
    "priority": "High",
    "components": ["Source Code"],
    "allowed_files": ["src/export.py", "tests/test_export.py"],
    "new_files": ["src/export.py", "tests/test_export.py"],
    "decision": "Add the command.",
    "agreed": ["CSV only."],
    "you_get": ["An export command."],
    "changes": ["New module."],
    "not_changed": ["Import."],
    "watch_out": ["Large repositories."],
    "in_scope_scenarios": ["Export writes a file."],
    "out_of_scope_scenarios": ["Import."],
}


def reply(
    patch: dict[str, Any] | None = None,
    text: str = "Drafted.",
    *,
    candidates: list[dict[str, Any]] | None = None,
    usage: UsageRecord | None = None,
    **changes: Any,
) -> AgentResult:
    data: dict[str, Any] = {"reply": text, "patch": patch or {}}
    if candidates is not None:
        data["candidates"] = candidates
    values: dict[str, Any] = {
        "ok": True,
        "final_text": "",
        "structured_output": data,
        "session_id": None,
        "usage": usage,
        "provider": "fake",
        "authentication_mode": AuthenticationMode.SUBSCRIPTION_LOGIN,
        "error": None,
        "timed_out": False,
        "cancelled": False,
        "log_path": None,
        "unsupported": None,
    }
    values.update(changes)
    return AgentResult(**values)


def failed(**changes: Any) -> AgentResult:
    return reply(ok=False, structured_output=None, **changes)


def known_ticket(number: int, title: str) -> Ticket:
    return Ticket(
        number=number,
        title=title,
        issue_type=IssueType.TASK,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.MEDIUM,
        body_sections={"Summary": "s", "Scope": "s", "Solution": "s"},
    )


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        answers: Sequence[str],
        *,
        steps: Sequence[AgentResult] | None = None,
        adapter: ScriptedAdapter | None = None,
        github: FakeGitHub | None = None,
        draft: TicketDraft | None = None,
    ) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir(exist_ok=True)
        self.github = github or FakeGitHub()
        self.store = DraftStore(tmp_path / "state")
        self.draft = draft or TicketDraft()
        self.adapter = adapter
        if adapter is None and steps is not None:
            self.adapter = ScriptedAdapter([ScriptStep(result=r) for r in steps])
        self.out: list[str] = []
        pending = iter(answers)

        def read(_: str) -> str:
            try:
                return next(pending)
            except StopIteration:
                raise EOFError from None

        self.session = NewSession(
            self.github,
            self.store,
            self.repo,
            self.draft,
            adapter=self.adapter,
            repo="o/r",
            prompt=read,
            print_fn=self.out.append,
        )

    def run(self) -> int:
        return asyncio.run(self.session.run())

    @property
    def text(self) -> str:
        return "\n".join(self.out)

    def saved(self) -> TicketDraft:
        return self.store.load(self.draft.id)


def test_idea_start_creates_an_s_ticket_after_yes(tmp_path: Path) -> None:
    h = Harness(tmp_path, [*FULL, "/create", "yes"], steps=[reply(FILL)])
    assert h.run() == 0
    issue = h.github.issues[100]
    assert issue["type"] == IssueType.FEATURE
    assert (issue["parent"], issue["milestone"], issue["assignee"]) == (1, "Backlog", "me")
    assert (issue["size"], issue["status"], issue["priority"]) == (
        Size.S,
        Status.TO_DO,
        Priority.HIGH,
    )
    assert issue["components"] == [Component.SOURCE_CODE]
    assert set(h.github.uploads) == {"100-solution-1.md", "100-conversation.md"}
    for section in ("## Summary", "## Scope", "## Analysis", "## Solution", "## Details"):
        assert section in h.text
    assert "next: fugue run 100" in h.text
    assert "https://github.com/o/r/issues/100" in h.text
    assert scope_url(body_ticket(100, issue["body"])) is not None
    assert "100-conversation.md" in issue["body"]
    assert h.saved().created_number == 100
    assert "will be uploaded" in h.text


def test_answers_become_person_owned_fields(tmp_path: Path) -> None:
    h = Harness(tmp_path, [*FULL, "/quit"], steps=[reply(FILL)])
    assert h.run() == 0
    draft = h.draft
    assert draft.summary == "Export tickets as CSV\n\nBenefit: Maintainers"
    assert draft.scope_text == "CSV only"
    assert draft.size == Size.S
    assert {draft.owners[name] for name in ("summary", "scope_text", "size")} == {"person"}
    assert draft.owners["title"] == "agent"


def test_initial_idea_summary_keeps_person_wording_when_followups_are_skipped(
    tmp_path: Path,
) -> None:
    """Scenario 7: keeping the initial Summary preserves its ownership and wording."""
    idea = "Export CSV without overwriting existing files"
    h = Harness(
        tmp_path,
        [idea, "", "/skip", "CSV only", "S", "/quit"],
        steps=[reply({"summary": "Export CSV"})],
    )
    assert h.run() == 0
    saved = h.saved()
    assert saved.summary == idea
    assert saved.owners["summary"] == "person"
    assert "kept your wording for summary" in h.text
    assert any(turn.speaker == "you" and turn.text == idea for turn in saved.turns)
    assert h.github.calls == []


def test_every_analyst_request_is_read_only_in_the_repository(tmp_path: Path) -> None:
    h = Harness(tmp_path, [*FULL, "/quit"], steps=[reply(FILL)])
    h.run()
    assert h.adapter is not None
    request = h.adapter.requests[0]
    assert request.access == AccessMode.READ_ONLY
    assert request.role == Role.ANALYST
    assert request.cwd == h.repo
    assert request.output_schema == PATCH_SCHEMA
    assert request.timeout_seconds is not None and request.timeout_seconds <= 300
    assert "BEGIN FACTS" in request.prompt
    assert IDEA in request.prompt
    assert "Export tickets as CSV" in request.prompt
    assert "Person-owned fields" in request.prompt


def test_prompt_is_the_draft_and_facts_not_the_history(tmp_path: Path) -> None:
    answers = [*FULL, "first edit request", "second edit request", "/quit"]
    h = Harness(tmp_path, answers, steps=[reply(FILL), reply(), reply()])
    h.run()
    assert h.adapter is not None
    last = h.adapter.requests[-1].prompt
    assert "second edit request" in last
    assert "first edit request" not in last
    assert "Describe your idea:" not in last


def test_suggest_start_drops_ungrounded_candidates_and_prefills_the_pick(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.tickets[7] = known_ticket(7, "Add export command")
    candidates = [
        {"title": "Mentions seven #7", "summary": "Grounded."},
        {"title": "Mentions a ghost #999", "summary": "Not grounded."},
        {"title": "Alpha", "summary": "Plain alpha."},
        {"title": "Beta", "summary": "Plain beta."},
        {"title": "Gamma", "summary": "Plain gamma."},
        {"title": "Delta", "summary": "Plain delta."},
        {"title": "Epsilon", "summary": "Plain epsilon."},
    ]
    fill = {key: value for key, value in FILL.items() if key != "title"}
    fill["scope_text"] = "Do the alpha work."
    h = Harness(
        tmp_path,
        ["2", "", "", "", "", "/create", "yes"],
        steps=[reply(candidates=candidates), reply(fill)],
        github=github,
        draft=TicketDraft(mode="suggest"),
    )
    assert h.run() == 0
    assert h.adapter is not None
    suggest_prompt = h.adapter.requests[0].prompt
    assert "#7 Task size S" in suggest_prompt
    shown = [line for line in h.out if line[:2] in {f"{n}:" for n in range(1, 8)}]
    assert [line[0] for line in shown] == ["1", "2", "3", "4", "5"]
    assert "Mentions seven #7" in shown[0]
    assert all("ghost" not in line for line in shown)
    assert "Dropped a candidate" in h.text
    issue = h.github.issues[100]
    assert issue["title"] == "Alpha"
    assert issue["type"] == IssueType.FEATURE
    assert "Plain alpha." in issue["body"]


def test_suggest_without_an_adapter_falls_back_to_an_idea(tmp_path: Path) -> None:
    h = Harness(tmp_path, [IDEA, "/quit"], draft=TicketDraft(mode="suggest"))
    assert h.run() == 0
    assert "no analyst adapter" in h.text
    assert h.draft.mode == "idea"
    assert h.draft.idea == IDEA


def test_related_work_is_shown_and_the_person_may_continue(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.tickets[7] = known_ticket(7, "Add export command")
    github.hits = [
        IssueHit(5, "Export tickets command", "closed"),
        IssueHit(6, "Export tickets command again", "open"),
    ]
    h = Harness(tmp_path, [IDEA, "", "/quit"], github=github)
    assert h.run() == 0
    assert "#7 (open): Add export command" in h.text
    assert "#5 (closed): Export tickets command" in h.text
    assert h.draft.step == "outcome"
    assert h.github.calls == []


def test_quitting_at_the_related_work_prompt_creates_nothing(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.hits = [IssueHit(5, "Export tickets command", "closed")]
    h = Harness(tmp_path, [IDEA, "/quit"], github=github)
    assert h.run() == 0
    assert github.calls == []
    saved = h.saved()
    assert saved.step == "start"
    assert saved.idea == IDEA


def test_an_analyst_patch_cannot_rewrite_the_persons_wording(tmp_path: Path) -> None:
    fill = {**FILL, "summary": "Rewritten", "title": "Better title"}
    steps = [reply(fill), reply({"title": "Agent again"})]
    answers = ["Add export", "My exact words", "", "", "S", "/set title Mine", "rename it", "/quit"]
    h = Harness(tmp_path, answers, steps=steps)
    assert h.run() == 0
    assert h.draft.summary == "My exact words"
    assert "kept your wording for summary" in h.text
    assert "kept your wording for title" in h.text
    assert h.draft.title == "Mine"
    assert ("you", "My exact words") in [(t.speaker, t.text) for t in h.draft.turns]
    assert ("you", "/set title Mine") in [(t.speaker, t.text) for t in h.draft.turns]
    assert h.draft.owners["title"] == "person"


@pytest.mark.parametrize("answer", ["no", "YES", "y", "yes please", "", "/quit"])
def test_nothing_is_created_without_the_exact_yes(tmp_path: Path, answer: str) -> None:
    h = Harness(tmp_path, [*FULL, "/create", answer], steps=[reply(FILL)])
    assert h.run() == 0
    assert h.github.calls == []
    assert h.saved().created_number is None
    assert h.saved().journal == {}
    assert "Not created. The draft is saved." in h.text


def test_end_of_input_at_the_confirmation_creates_nothing(tmp_path: Path) -> None:
    h = Harness(tmp_path, [*FULL, "/create"], steps=[reply(FILL)])
    assert h.run() == 0
    assert h.github.calls == []
    assert "Draft saved; resume with: fugue new --resume" in h.text
    assert h.saved().title == "Add export command"


def test_end_of_input_at_the_start_saves_the_draft(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])
    assert h.run() == 0
    assert h.github.calls == []
    assert h.saved().step == "start"


def test_create_is_refused_with_the_problems_listed(tmp_path: Path) -> None:
    h = Harness(tmp_path, [*FULL, "/create"], steps=[reply({"title": "Only a title"})])
    assert h.run() == 0
    assert h.github.calls == []
    assert "Problems:" in h.text
    assert "requires exactly one Component" in h.text
    assert "Size S requires allowed files" in h.text
    assert "Not created: fix the problems above first." in h.text
    assert "Create this ticket?" not in h.text


def test_blockers_and_parents_must_exist_and_blockers_be_open(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.states[9] = "closed"
    answers = [*FULL, "/set blocked_by 9, 77", "/show", "/set parent 5", "/show", "/quit"]
    h = Harness(tmp_path, answers, steps=[reply(FILL)], github=github)
    h.run()
    assert "Blocker #9 is not open" in h.text
    assert "Blocker #77 does not exist" in h.text
    assert "Parent #5 does not exist" in h.text
    assert h.github.calls == []


def test_a_parent_and_an_open_blocker_are_linked(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.states.update({5: "open", 9: "open"})
    draft = TicketDraft(parent=5)
    answers = [*FULL, "/set blockers #9", "/create", "yes"]
    h = Harness(tmp_path, answers, steps=[reply(FILL)], github=github, draft=draft)
    assert h.run() == 0
    assert github.issues[100]["parent"] == 5
    assert github.issues[100]["blockers"] == [9]


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (failed(unavailable=Unavailable.RATE_LIMITED), "unavailable (rate_limited)"),
        (failed(provider_limited=True), "provider is limited"),
        (failed(timed_out=True), "timed out"),
        (failed(cancelled=True), "was cancelled"),
        (failed(error="boom"), "failed: boom"),
        (reply({"title": "Ghost"}, "See #999 for details."), "not in the gathered facts"),
        (reply(structured_output=None, final_text="not json"), "no structured output"),
    ],
)
def test_agent_trouble_is_printed_changes_nothing_and_the_session_continues(
    tmp_path: Path, result: AgentResult, reason: str
) -> None:
    h = Harness(tmp_path, [*FULL, "/quit"], steps=[result])
    assert h.run() == 0
    assert reason in h.text
    assert "continuing by hand" in h.text
    assert h.draft.title == IDEA
    assert h.draft.components == []
    assert h.draft.owners["title"] == "agent"
    assert "Preview:" in h.text


@pytest.mark.parametrize("source", ["turn", "field", "parent"])
def test_draft_citations_do_not_ground_analyst_output(tmp_path: Path, source: str) -> None:
    """Scenario 12: only gathered facts can ground an analyst citation."""
    draft = TicketDraft()
    if source == "turn":
        draft.add_turn("you", "refer to #999")
    elif source == "field":
        draft.apply_patch({"watch_out": ["refer to #999"]}, "person")
    else:
        draft.parent = 999
    h = Harness(
        tmp_path,
        [*FULL, "/quit"],
        draft=draft,
        steps=[reply({"decision": "Follow #999", "components": ["Source Code"]})],
    )
    assert h.run() == 0
    assert "not in the gathered facts" in h.text
    assert h.saved().decision == ""
    assert h.saved().components == []
    assert h.github.calls == []


def test_gathered_ticket_citations_remain_usable(tmp_path: Path) -> None:
    """Scenario 12: a citation present in gathered facts permits the patch."""
    github = FakeGitHub()
    github.tickets[999] = known_ticket(999, "Existing work")
    h = Harness(
        tmp_path,
        [*FULL, "/quit"],
        github=github,
        steps=[reply({"decision": "Follow #999"})],
    )
    assert h.run() == 0
    assert h.saved().decision == "Follow #999"
    assert "not in the gathered facts" not in h.text


def test_manual_drafting_works_when_the_adapter_lacks_a_capability(tmp_path: Path) -> None:
    adapter = ScriptedAdapter([], capabilities=frozenset({Capability.STRUCTURED_OUTPUT}))
    answers = [
        *FULL,
        "/set title Add export",
        "/set components Source Code",
        "/set decision Add it",
        "/set allowed_files src/export.py",
        "/set new_files src/export.py",
        "/create",
        "yes",
    ]
    h = Harness(tmp_path, answers, adapter=adapter)
    assert h.run() == 0
    assert adapter.requests == []
    assert "lacks read-only mode or structured output" in h.text
    assert h.github.issues[100]["title"] == "Add export"
    assert h.github.issues[100]["components"] == [Component.SOURCE_CODE]


def test_without_an_adapter_the_session_still_works(tmp_path: Path) -> None:
    h = Harness(tmp_path, [*FULL, "free text request", "/quit"])
    assert h.run() == 0
    assert "no analyst adapter is configured" in h.text


def test_facts_are_bounded_even_with_many_tickets(tmp_path: Path) -> None:
    github = FakeGitHub()
    for number in range(2, 160):
        github.tickets[number] = known_ticket(number, "A long ticket title " + "word " * 20)
    h = Harness(tmp_path, [*FULL, "/quit"], steps=[reply(FILL)], github=github)
    h.run()
    assert h.adapter is not None
    prompt = h.adapter.requests[0].prompt
    facts = prompt[prompt.rindex("BEGIN FACTS") : prompt.rindex("END FACTS") + len("END FACTS")]
    assert 0 < len(facts) <= 6000


def test_analyst_calls_are_capped_at_twenty_per_session(tmp_path: Path) -> None:
    edits = [f"request {n}" for n in range(25)]
    h = Harness(tmp_path, [*FULL, *edits, "/quit"], steps=[reply() for _ in range(30)])
    assert h.run() == 0
    assert h.adapter is not None
    assert len(h.adapter.requests) == 20
    assert "limit of 20 analyst calls" in h.text
    assert "/set FIELD TEXT" in h.text


def test_usage_is_shown_as_reported_values_and_stored(tmp_path: Path) -> None:
    usage = UsageRecord(10, None, 5, None, 0.01, 1200)
    h = Harness(tmp_path, [*FULL, "/quit"], steps=[reply(FILL, usage=usage)])
    h.run()
    assert "usage (reported values, not a bill): input_tokens=10" in h.text
    assert h.saved().usage == [
        {
            "input_tokens": 10,
            "cached_input_tokens": None,
            "output_tokens": 5,
            "reasoning_tokens": None,
            "cost_estimate_usd": 0.01,
            "duration_ms": 1200,
        }
    ]


def test_the_draft_is_saved_after_every_step(tmp_path: Path) -> None:
    h = Harness(tmp_path, [IDEA, "Outcome", "/quit"])
    h.run()
    saved = h.saved()
    assert saved.step == "benefit"
    assert saved.summary == "Outcome"
    assert saved.owners["summary"] == "person"


def test_resume_continues_at_the_saved_step(tmp_path: Path) -> None:
    first = Harness(tmp_path, [IDEA, "Outcome", "/quit"])
    first.run()
    second = Harness(tmp_path, ["Who", "Scope", "M", "/quit"], draft=first.saved())
    assert second.run() == 0
    assert second.draft.summary == "Outcome\n\nBenefit: Who"
    assert second.draft.size == Size.M
    assert second.draft.step == "review"
    assert second.draft.turns[0].text == "Describe your idea:"


def test_size_xl_is_refused_in_the_size_question(tmp_path: Path) -> None:
    h = Harness(tmp_path, [IDEA, "o", "b", "s", "XL", "huge", "L", "/quit"], steps=[reply()])
    assert h.run() == 0
    assert "Size XL is not supported" in h.text
    assert "Unknown Size value: huge" in h.text
    assert h.draft.size == Size.L


def test_commands_help_show_and_unknown(tmp_path: Path) -> None:
    answers = [*FULL, "/help", "/frobnicate", "/set", "/set nope x", "/quit"]
    h = Harness(tmp_path, answers, steps=[reply()])
    h.run()
    assert "/help, /show, /set FIELD TEXT, /skip, /save PATH, /create, /quit" in h.text
    assert "Unknown command /frobnicate" in h.text
    assert "Use /set FIELD TEXT" in h.text
    assert "Unknown field nope" in h.text


def test_save_writes_a_redacted_transcript_and_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "talk.md"
    secret = "ghp_" + "Z" * 30
    answers = [*FULL, f"note {secret}", f"/save {target}", f"/save {target}", "/quit"]
    h = Harness(tmp_path, answers, steps=[reply(FILL), reply()])
    h.run()
    text = target.read_text(encoding="utf-8")
    assert "you: Export tickets as CSV" in text
    assert secret not in text
    assert "[redacted]" in text
    assert "already exists" in h.text
    assert secret not in "".join(path.read_text() for path in (tmp_path / "state").rglob("*.json"))


def test_a_create_that_fails_on_attachments_prints_the_resume_command(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.fail["upload_attachments"] = AttachmentUnavailable(["gh", "attach"], "not signed in")
    h = Harness(tmp_path, [*FULL, "/create", "yes"], steps=[reply(FILL)], github=github)
    assert h.run() == 4
    assert "error: creation stopped: not signed in" in h.text
    assert "Issue #100 exists but is incomplete" in h.text
    assert f"next: fugue new --resume {h.draft.id}" in h.text
    assert scope_url(body_ticket(100, github.issues[100]["body"])) is None
    resumed = Harness(tmp_path, ["/create", "yes"], github=github, draft=h.saved())
    assert resumed.run() == 0
    assert scope_url(body_ticket(100, github.issues[100]["body"])) is not None
    assert len(github.issues) == 1


def test_parse_field_converts_each_kind() -> None:
    assert parse_field("size", "xs") == ("size", Size.XS)
    assert parse_field("type", "bug") == ("issue_type", IssueType.BUG)
    assert parse_field("components", "Tests, Build") == (
        "components",
        [Component.TESTS, Component.BUILD],
    )
    assert parse_field("agreed", "a; b") == ("agreed", ["a", "b"])
    assert parse_field("scope", "text") == ("scope_text", "text")
    assert parse_field("parent", "#4") == ("parent", 4)
    with pytest.raises(ValueError, match="Unknown Component"):
        parse_field("components", "Nope")
