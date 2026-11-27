"""A GitHub issue as Fugue sees it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Ticket(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    body: str = ""
    state: Literal["open", "closed"]
    labels: tuple[str, ...] = Field(default_factory=tuple)
    url: str
