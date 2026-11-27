"""A pull request, and the checks that ran on it."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PullRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    body: str = ""
    state: Literal["open", "closed", "merged"]
    draft: bool = False
    head: str
    base: str
    sha: str
    labels: tuple[str, ...] = Field(default_factory=tuple)
    author: str = ""
    mergeable: bool | None = None
    updated_at: datetime
    url: str


class Check(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: str
    conclusion: str | None = None
    url: str = ""

    @property
    def outcome(self) -> Literal["passed", "failed", "running", "skipped"]:
        if self.status != "completed":
            return "running"
        if self.conclusion in ("success", "neutral"):
            return "passed"
        if self.conclusion in ("skipped", "cancelled"):
            return "skipped"
        return "failed"
