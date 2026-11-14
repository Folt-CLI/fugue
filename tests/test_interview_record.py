"""Private, deterministic interview records."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fugue.interview.record import InterviewRecord
from fugue.interview.render import render_conversation
from fugue.interview.store import InterviewStore


def test_private_record_round_trip_redacts_and_limits(tmp_path: Path) -> None:
    record = InterviewRecord(repo="owner/name", ticket=90)
    record.add_turn("person", "  keep spaces\n" + "ghp_" + "x" * 30, max_turns=1)
    record.add_turn("analyst", "Why?", max_turns=1)
    with pytest.raises(ValueError, match="turn limit"):
        record.add_turn("person", "again", max_turns=1)
    store = InterviewStore(tmp_path)
    path = store.save(record)
    assert path == tmp_path / "interviews/owner__name__90.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert store.load("owner/name", 90) == record
    assert record.turns[0].text == "  keep spaces\n[redacted]"
    assert store.finished("owner/name", 90) is None
    assert not list(path.parent.glob("*.tmp"))
    with pytest.raises(ValidationError):
        InterviewRecord.model_validate({"repo": "owner/name", "ticket": 90, "unknown": True})
    with pytest.raises(ValueError):
        store.path("../name", 90)


def test_finished_render_is_deterministic(tmp_path: Path) -> None:
    record = InterviewRecord(
        repo="owner/name", ticket=90, mode="automatic", standin_provider="codex"
    )
    record.add_turn("stand_in", "Original\nturn")
    record.finished = datetime(2020, 1, 2, tzinfo=UTC)
    record.status = "proceeded"
    record.ended_by = "stand_in"
    store = InterviewStore(tmp_path)
    store.save(record)
    saved = store.finished("owner/name", 90)
    assert saved is not None
    text = render_conversation(saved)
    assert text == render_conversation(record)
    assert "Date: 2020-01-02T00:00:00+00:00" in text
    assert "Mode: automatic (stand-in provider: codex)" in text
    assert "stand-in (automatic): Original\nturn" in text
    assert "Ended by: stand-in (automatic)" in text


def test_needs_human_request_reserves_turn_for_person(tmp_path: Path) -> None:
    record = InterviewRecord(repo="owner/name", ticket=90)
    record.add_turn("stand_in", "Need the intended audience", max_turns=1, needs_human=True)
    store = InterviewStore(tmp_path)
    store.save(record)
    saved = store.load("owner/name", 90)
    assert saved is not None
    assert saved.turns[0].needs_human
    assert saved.turn_count == 0
    saved.add_turn("person", "Internal developers", max_turns=1)
    assert saved.turn_count == 1
    with pytest.raises(ValueError, match="turn limit"):
        saved.add_turn("person", "Extra answer", max_turns=1)
    assert "stand-in (automatic): Need the intended audience" in render_conversation(saved)


def test_existing_turns_still_count_without_needs_human_metadata() -> None:
    record = InterviewRecord.model_validate(
        {
            "repo": "owner/name",
            "ticket": 90,
            "turns": [{"speaker": "stand_in", "text": "Known audience"}],
        }
    )
    assert record.turn_count == 1
    assert not record.turns[0].needs_human
