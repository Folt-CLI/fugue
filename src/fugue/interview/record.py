"""Validated, redacted local interview records."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fugue.tickets.draft import redact

Speaker = Literal["person", "analyst", "stand_in"]


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker: Speaker
    text: str

    @field_validator("text")
    @classmethod
    def redact_text(cls, value: str) -> str:
        return redact(value)


class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str
    href: str


class InterviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    repo: str
    ticket: int = Field(gt=0)
    mode: Literal["human", "automatic"] = "human"
    standin_provider: str | None = None
    turns: list[Turn] = Field(default_factory=list)
    status: Literal["open", "proceeded"] = "open"
    ended_by: Literal["person", "stand_in", "turn_limit"] | None = None
    started: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished: datetime | None = None
    upload: Upload | None = None
    usage: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return sum(turn.speaker != "analyst" for turn in self.turns)

    def add_turn(self, speaker: Speaker, text: str, *, max_turns: int = 10) -> None:
        if self.status != "open":
            raise ValueError("Interview has already proceeded")
        if speaker != "analyst" and self.turn_count >= max_turns:
            raise ValueError("Interview turn limit reached")
        self.turns.append(Turn(speaker=speaker, text=text))
