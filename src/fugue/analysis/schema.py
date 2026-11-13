"""Validated analyst handoff and its provider-facing JSON schema."""

from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Items = Annotated[list[Text], Field(min_length=1)]


class HandoffModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(HandoffModel):
    standing_rules: Items
    source_of_truth: Items
    file_changes: Items
    in_scope: Items
    out_of_scope: Items
    do_not_change: Items
    verification: Items
    final_message: Items
    orchestrator_notes: Items


class Option(HandoffModel):
    name: Text
    you_get: Items
    changes: Items
    watch_out: Items
    scope: Scope


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
        return self


def handoff_schema() -> dict[str, Any]:
    """Return a fresh JSON schema for structured analyst output."""
    return Handoff.model_json_schema()
