"""Core ticket data and per-Size policy."""

from pydantic import BaseModel, ConfigDict, Field

from fugue.models.enums import (
    AnalysisDepth,
    Component,
    IssueType,
    Priority,
    Selection,
    Size,
    Status,
)


class SizeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: AnalysisDepth
    review: bool
    max_components: int | None = Field(ge=0)
    selection: Selection = Selection.DEVELOPER


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    title: str = Field(min_length=1)
    issue_type: IssueType
    size: Size
    status: Status
    priority: Priority
    milestone: str | None = None
    assignees: list[str] = Field(default_factory=list)
    parent: int | None = Field(default=None, gt=0)
    blocked_by: list[int] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    body_sections: dict[str, str] = Field(default_factory=dict)
