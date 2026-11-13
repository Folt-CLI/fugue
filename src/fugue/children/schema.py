"""Validated TOML specifications for children of a decided Question."""

import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fugue.models import Component, IssueType, Priority, Size


class ChildSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    issue_type: IssueType = IssueType.FEATURE
    size: Size
    priority: Priority = Priority.MEDIUM
    components: list[Component]
    summary: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    agreed: list[str] = Field(min_length=1)
    you_get: list[str] = Field(min_length=1)
    changes: list[str] = Field(min_length=1)
    not_changed: list[str] = Field(min_length=1)
    watch_out: list[str] = Field(min_length=1)
    scope_file: Path | None = None
    blocked_by: list[str | int] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_values(self) -> Self:
        if self.size not in (Size.XS, Size.S, Size.M):
            raise ValueError("Children must have Size XS, S or M")
        if len(set(self.components)) != len(self.components):
            raise ValueError("Components must be distinct")
        for name in ("agreed", "you_get", "changes", "not_changed", "watch_out"):
            if any(not item.strip() for item in getattr(self, name)):
                raise ValueError(f"{name} must contain non-empty text")
        for blocker in self.blocked_by:
            if isinstance(blocker, bool) or (isinstance(blocker, int) and blocker <= 0):
                raise ValueError("blocked_by issue numbers must be positive")
        return self


class ChildrenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: int = Field(gt=0)
    milestone: str = Field(min_length=1)
    assignee: str = Field(min_length=1)
    children: list[ChildSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def check_keys(self) -> Self:
        keys = [child.key for child in self.children]
        if len(set(keys)) != len(keys):
            raise ValueError("Child keys must be unique")
        for child in self.children:
            for blocker in child.blocked_by:
                if isinstance(blocker, str) and blocker not in keys:
                    raise ValueError(f"Unknown blocked_by key: {blocker}")
                if blocker == child.key:
                    raise ValueError("A child cannot block itself")
        remaining = {child.key: set(child.blocked_by).intersection(keys) for child in self.children}
        while remaining:
            ready = {key for key, blockers in remaining.items() if not blockers}
            if not ready:
                raise ValueError("blocked_by keys contain a cycle")
            remaining = {
                key: blockers - ready for key, blockers in remaining.items() if key not in ready
            }
        return self


def load_children(path: Path) -> ChildrenSpec:
    """Resolve scope files relative to the TOML file, independent of the current directory."""
    with path.open("rb") as source:
        spec = ChildrenSpec.model_validate(tomllib.load(source))
    for child in spec.children:
        if child.scope_file is not None:
            child.scope_file = (path.resolve().parent / child.scope_file).resolve()
    return spec
