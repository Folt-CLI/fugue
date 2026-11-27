"""Validated analyst handoff and its provider-facing JSON schema."""

from collections.abc import Collection
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Items = Annotated[list[Text], Field(min_length=1)]


class HandoffModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(HandoffModel):
    """A scope; only the orchestrator reads its notes, the agent does not."""

    allowed_files: Items
    source_of_truth: list[Text] = Field(default_factory=list)
    file_changes: Items
    in_scope: Items
    out_of_scope: list[Text] = Field(default_factory=list)
    do_not_change: list[Text] = Field(default_factory=list)
    verification: Items
    final_message: Items
    orchestrator_notes: list[Text] = Field(default_factory=list)


class TriggerName(StrEnum):
    """The four observable reasons to ask the developer during analysis."""

    AMBIGUITY = "ambiguity"
    BEHAVIOUR = "behaviour"
    ARCHITECTURE = "architecture"
    IRREVERSIBLE = "irreversible"


TRIGGER_LABELS: dict[TriggerName, str] = {
    TriggerName.AMBIGUITY: "Requirements materially ambiguous",
    TriggerName.BEHAVIOUR: "Meaningfully different user-visible behaviour",
    TriggerName.ARCHITECTURE: "Significant architectural commitment",
    TriggerName.IRREVERSIBLE: "Destructive or hard to reverse",
}


class TriggerCheck(HandoffModel):
    name: TriggerName
    fired: bool
    evidence: Text


class ScopingResult(HandoffModel):
    """The scoping of an S ticket: it decides nothing and may ask to re-size."""

    fits: bool
    resize_reason: str = ""
    scope: Scope | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.fits and self.scope is None:
            raise ValueError("A ticket that fits needs a scope")
        if not self.fits and not self.resize_reason.strip():
            raise ValueError("A ticket that does not fit needs a reason to re-size")
        return self


class Option(HandoffModel):
    """An option as the developer compares it; only the recommended one has a scope."""

    name: Text
    you_get: Items
    changes: Items
    watch_out: Items


class Comparison(HandoffModel):
    label: Text
    values: Items


class Handoff(HandoffModel):
    decision: Text
    where_we_are: Items
    out_of_scope: Items
    terms: Items
    comparison: list[Comparison]
    options: Annotated[list[Option], Field(min_length=1)]
    recommendation: int = Field(gt=0)
    reason: Text
    scope: Scope
    triggers: Annotated[list[TriggerCheck], Field(min_length=4, max_length=4)]
    findings: Text
    key_points: Annotated[list[Text], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def consistent_options(self) -> Self:
        if self.recommendation > len(self.options):
            raise ValueError("Recommendation must identify an option")
        if len(self.options) > 1 and not self.comparison:
            raise ValueError("Multiple options require a comparison")
        if any(len(row.values) != len(self.options) for row in self.comparison):
            raise ValueError("Comparison rows must have one value per option")
        if {check.name for check in self.triggers} != set(TriggerName):
            raise ValueError("Triggers must cover each trigger exactly once")
        return self

    @property
    def fired(self) -> list[TriggerCheck]:
        return [check for check in self.triggers if check.fired]


class BatchEntry(HandoffModel):
    """One ticket's complete handoff within an analysis batch, tagged with its number."""

    ticket: int = Field(gt=0)
    handoff: Handoff


class BatchHandoff(HandoffModel):
    """The structured result of an analysis batch: one complete handoff per ticket.

    Only the provider-facing schema uses it; each entry is validated on its own by
    `split_batch`, so one invalid handoff never discards the others.
    """

    analyses: list[BatchEntry]


def split_batch(output: object, numbers: Collection[int]) -> dict[int, Handoff]:
    """The valid handoff of each requested ticket; a missing, repeated or invalid one is left out.

    An entry for a number outside `numbers` is ignored.
    """
    entries = output.get("analyses") if isinstance(output, dict) else None
    if not isinstance(entries, list):
        return {}
    found: dict[int, list[object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number = entry.get("ticket")
        if isinstance(number, bool) or not isinstance(number, int) or number not in numbers:
            continue
        found.setdefault(number, []).append(entry.get("handoff"))
    handoffs: dict[int, Handoff] = {}
    for number, values in found.items():
        if len(values) != 1:
            continue
        try:
            handoffs[number] = Handoff.model_validate(values[0])
        except ValidationError:
            continue
    return handoffs


def handoff_schema() -> dict[str, Any]:
    """Return a fresh JSON schema for structured analyst output."""
    return Handoff.model_json_schema()


def scoping_schema() -> dict[str, Any]:
    """Return a fresh JSON schema for structured scoping output."""
    return ScopingResult.model_json_schema()


def batch_schema() -> dict[str, Any]:
    """Return a fresh JSON schema for the structured output of an analysis batch."""
    return BatchHandoff.model_json_schema()
