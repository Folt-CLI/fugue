"""A GitHub project (v2): its fields and the issues and pull requests on it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Project(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    description: str = ""
    public: bool = False
    closed: bool = False
    url: str


class ProjectOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str = ""


class ProjectField(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    type: str
    options: tuple[ProjectOption, ...] = Field(default_factory=tuple)

    def option(self, name: str) -> ProjectOption | None:
        """The option called `name`, ignoring case."""
        return next((o for o in self.options if o.name.casefold() == name.casefold()), None)


class ProjectItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    kind: Literal["issue", "pull_request", "draft"]
    number: int | None = None
    title: str
    state: str = ""
    url: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


class ProjectOverview(BaseModel):
    """A project together with its fields."""

    model_config = ConfigDict(frozen=True)

    project: Project
    fields: tuple[ProjectField, ...]


class ProjectTable(BaseModel):
    """Items of a project with the fields that were read for them."""

    model_config = ConfigDict(frozen=True)

    project: Project
    columns: tuple[str, ...]
    items: tuple[ProjectItem, ...]


class FieldChange(BaseModel):
    """A field of an item after it was set, under the field's real name."""

    model_config = ConfigDict(frozen=True)

    issue: int
    field: str
    value: str
