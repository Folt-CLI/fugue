"""What a flow tells the world about, as data. Core says what happened; an interface decides
how to show it. Each event carries a `kind`, so it can be sent as JSON and read back."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from fugue.models import FieldChange, Ticket


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)


class StepStarted(Event):
    kind: Literal["step_started"] = "step_started"
    step: str


class StepFinished(Event):
    kind: Literal["step_finished"] = "step_finished"
    step: str
    detail: str = ""


class StepFailed(Event):
    kind: Literal["step_failed"] = "step_failed"
    step: str
    message: str


class Message(Event):
    kind: Literal["message"] = "message"
    text: str
    level: Literal["info", "warning"] = "info"


class TicketRead(Event):
    kind: Literal["ticket_read"] = "ticket_read"
    repository: str
    ticket: Ticket


class StatusMoved(Event):
    kind: Literal["status_moved"] = "status_moved"
    repository: str
    change: FieldChange


class WorktreeReady(Event):
    kind: Literal["worktree_ready"] = "worktree_ready"
    path: str
    branch: str
    base: str
    new_branch: bool
    reused: bool
