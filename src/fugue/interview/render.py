"""Deterministic conversation attachment text."""

from fugue.interview.record import InterviewRecord
from fugue.tickets.draft import redact


def render_conversation(record: InterviewRecord) -> str:
    mode = (
        f"automatic (stand-in provider: {record.standin_provider})"
        if record.mode == "automatic"
        else "human"
    )
    parts = [
        f"# Interview for ticket #{record.ticket}",
        f"Date: {record.finished.isoformat() if record.finished else 'unfinished'}",
        f"Mode: {mode}",
    ]
    for turn in record.turns:
        speaker = "stand-in (automatic)" if turn.speaker == "stand_in" else turn.speaker
        parts.append(f"{speaker}: {turn.text}")
    ended = {"person": "person", "stand_in": "stand-in (automatic)", "turn_limit": "turn limit"}
    parts.extend(["## Decision", f"Ended by: {ended.get(record.ended_by or '', 'unfinished')}"])
    return redact("\n\n".join(parts) + "\n")
