"""The ticket draft: the saved state of one guided ticket creation."""

import re
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from fugue.models import Component, IssueType, Priority, Size

Owner = Literal["person", "agent"]
Mode = Literal["idea", "suggest"]
Speaker = Literal["you", "fugue"]

REDACTED = "[redacted]"
_SECRETS = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
)

# Every field a person or the analyst may write; `owners` records who wrote each.
PATCH_FIELDS = (
    "title",
    "issue_type",
    "size",
    "priority",
    "parent",
    "milestone",
    "assignee",
    "components",
    "blocked_by",
    "summary",
    "scope_text",
    "allowed_files",
    "new_files",
    "in_scope_scenarios",
    "out_of_scope_scenarios",
    "decision",
    "agreed",
    "you_get",
    "changes",
    "not_changed",
    "watch_out",
)


def redact(text: str) -> str:
    """Replace strings that look like tokens; only the listed patterns are detected."""
    return _SECRETS.sub(REDACTED, text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Speaker
    text: str

    @field_validator("text")
    @classmethod
    def redact_text(cls, value: str) -> str:
        return redact(value)


class TicketDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    mode: Mode = "idea"
    step: str = "start"
    idea: str = ""
    title: str = ""
    issue_type: IssueType = IssueType.TASK
    size: Size = Size.S
    priority: Priority = Priority.MEDIUM
    parent: int = Field(default=1, gt=0)
    parent_is_question: bool = False
    milestone: str = "Backlog"
    assignee: str = ""
    components: list[Component] = Field(default_factory=list)
    blocked_by: list[int] = Field(default_factory=list)
    summary: str = ""
    scope_text: str = ""
    allowed_files: list[str] = Field(default_factory=list)
    new_files: list[str] = Field(default_factory=list)
    in_scope_scenarios: list[str] = Field(default_factory=list)
    out_of_scope_scenarios: list[str] = Field(default_factory=list)
    decision: str = ""
    agreed: list[str] = Field(default_factory=list)
    you_get: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    not_changed: list[str] = Field(default_factory=list)
    watch_out: list[str] = Field(default_factory=list)
    owners: dict[str, Owner] = Field(default_factory=dict)
    turns: list[Turn] = Field(default_factory=list)
    usage: list[dict[str, Any]] = Field(default_factory=list)
    confirmed: bool = False
    journal: dict[str, Any] = Field(default_factory=dict)
    created_number: int | None = None

    @property
    def effective_type(self) -> IssueType:
        """Size L is always a Question."""
        return IssueType.QUESTION if self.size == Size.L else self.issue_type

    def add_turn(self, speaker: Speaker, text: str) -> None:
        self.turns.append(Turn(speaker=speaker, text=redact(text)))

    def apply_patch(self, patch: Mapping[str, Any], source: Owner) -> list[str]:
        """Write the patch fields; return the fields an agent patch was not allowed to write.

        A field written by the person is person-owned and an agent patch never replaces
        it. The patch applies as a whole or, when a value is invalid, not at all.
        """
        unknown = sorted(set(patch) - set(PATCH_FIELDS))
        if unknown:
            raise ValueError(f"Unknown draft field: {', '.join(unknown)}")
        ignored: list[str] = []
        updates: dict[str, Any] = {}
        for name, value in patch.items():
            if source == "agent" and self.owners.get(name) == "person":
                ignored.append(name)
                continue
            updates[name] = _redact_value(value)
        if "size" in updates:
            try:
                wanted = Size(updates["size"])
            except ValueError as error:
                raise ValueError(f"Unknown Size: {updates['size']}") from error
            if wanted == Size.XL:
                raise ValueError("Size XL is not supported; Epic work is out of scope")
        try:
            candidate = TicketDraft.model_validate({**self.model_dump(), **updates})
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise ValueError(f"Invalid value for {where}: {first['msg']}") from error
        for name in updates:
            setattr(self, name, getattr(candidate, name))
            self.owners[name] = source
        return ignored
