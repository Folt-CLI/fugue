"""The draft model, field ownership, redaction and the draft files."""

import json
import os
import stat
from pathlib import Path

import pytest
from test_tickets_create import TOKEN, draft_s

from fugue.models import Component, IssueType, Priority, Size
from fugue.tickets import DraftStore, TicketDraft, redact


def test_agent_patch_cannot_rewrite_a_person_owned_field() -> None:
    draft = TicketDraft()
    assert draft.apply_patch({"summary": "My own words"}, "person") == []
    ignored = draft.apply_patch({"summary": "Rewritten", "title": "New title"}, "agent")
    assert ignored == ["summary"]
    assert draft.summary == "My own words"
    assert draft.title == "New title"
    assert draft.owners == {"summary": "person", "title": "agent"}


def test_person_can_overwrite_an_agent_field_and_takes_ownership() -> None:
    draft = TicketDraft()
    draft.apply_patch({"title": "Agent title"}, "agent")
    assert draft.apply_patch({"title": "Mine"}, "person") == []
    assert draft.owners["title"] == "person"
    assert draft.apply_patch({"title": "Again"}, "agent") == ["title"]
    assert draft.title == "Mine"


def test_size_xl_is_rejected_and_the_patch_is_not_partly_applied() -> None:
    draft = TicketDraft(title="Keep")
    with pytest.raises(ValueError, match="Size XL"):
        draft.apply_patch({"title": "Changed", "size": "XL"}, "person")
    assert draft.title == "Keep"
    assert draft.size == Size.S
    assert draft.owners == {}


@pytest.mark.parametrize(
    "patch", [{"nonsense": 1}, {"parent": 0}, {"size": "huge"}, {"components": ["Nope"]}]
)
def test_invalid_patch_raises_and_changes_nothing(patch: dict[str, object]) -> None:
    draft = TicketDraft(title="Keep")
    with pytest.raises(ValueError):
        draft.apply_patch({"title": "Changed", **patch}, "agent")
    assert draft.title == "Keep"


def test_patch_coerces_enums_and_redacts_text() -> None:
    draft = TicketDraft()
    draft.apply_patch(
        {
            "issue_type": "Bug",
            "priority": "Low",
            "components": ["Tests"],
            "summary": f"leaked {TOKEN}",
            "watch_out": [f"also {TOKEN}"],
        },
        "person",
    )
    assert draft.issue_type == IssueType.BUG
    assert draft.priority == Priority.LOW
    assert draft.components == [Component.TESTS]
    assert TOKEN not in draft.summary
    assert "[redacted]" in draft.summary
    assert draft.watch_out == ["also [redacted]"]


def test_size_l_is_always_a_question() -> None:
    draft = TicketDraft(issue_type=IssueType.FEATURE, size=Size.L)
    assert draft.effective_type == IssueType.QUESTION
    assert TicketDraft(issue_type=IssueType.FEATURE).effective_type == IssueType.FEATURE


@pytest.mark.parametrize(
    "text",
    [
        "ghp_" + "A" * 30,
        "gho_" + "a" * 25,
        "github_pat_" + "Z9_" * 10,
        "sk-" + "q" * 24,
        "AKIA" + "0" * 16,
        "Bearer abcdefghijkl",
    ],
)
def test_redact_replaces_known_token_patterns(text: str) -> None:
    assert redact(f"before {text} after") == "before [redacted] after"


def test_redact_keeps_ordinary_text() -> None:
    assert redact("sk-short and ghp_short") == "sk-short and ghp_short"


def test_turns_are_redacted_when_added() -> None:
    draft = TicketDraft()
    draft.add_turn("you", f"here {TOKEN}")
    assert draft.turns[0].text == "here [redacted]"


def test_store_round_trips_fields_owners_and_turns_with_mode_0600(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = draft_s()
    draft.apply_patch({"summary": "Mine"}, "person")
    path = store.save(draft)
    assert path == tmp_path / "drafts" / f"{draft.id}.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    loaded = store.load(draft.id)
    assert loaded == draft
    assert loaded.owners["summary"] == "person"
    assert [turn.text for turn in loaded.turns] == ["Describe your idea:", "Export tickets"]
    assert store.list() == [draft.id]
    assert not list((tmp_path / "drafts").glob("*.tmp"))


def test_store_file_holds_only_the_draft(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = draft_s()
    data = json.loads(store.save(draft).read_text())
    assert set(data) == set(TicketDraft.model_fields)


def test_store_overwrite_stays_private(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = draft_s()
    store.save(draft)
    draft.title = "Changed"
    path = store.save(draft)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert store.load(draft.id).title == "Changed"


@pytest.mark.parametrize("draft_id", ["missing", "../escape", "", "a/b"])
def test_unknown_id_raises_naming_the_id(tmp_path: Path, draft_id: str) -> None:
    with pytest.raises(ValueError, match="Unknown draft id"):
        DraftStore(tmp_path).load(draft_id)


@pytest.mark.parametrize("content", ["not json", '{"title": 3}', '{"id": "x", "extra": 1}'])
def test_corrupt_draft_raises_naming_the_id(tmp_path: Path, content: str) -> None:
    store = DraftStore(tmp_path)
    store.directory.mkdir(parents=True)
    (store.directory / "abc123.json").write_text(content)
    with pytest.raises(ValueError, match="abc123"):
        store.load("abc123")


def test_a_draft_file_naming_another_id_is_refused(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = draft_s()
    store.save(draft)
    (store.directory / "other.json").write_text((store.directory / f"{draft.id}.json").read_text())
    with pytest.raises(ValueError, match="other"):
        store.load("other")
