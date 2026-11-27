"""What a flow tells the world about, as data. Core says what happened; an interface decides
how to show it. Each event carries a `kind`, so it can be sent as JSON and read back."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from fugue.models import FieldChange, Ticket


class Event(BaseModel):
    """Something that happened. `run_id` and `flow` say which run it belongs to; the flow's
    reporter fills them in, so a step never has to know them."""

    model_config = ConfigDict(frozen=True)

    run_id: str = ""
    flow: str = ""


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


class RunStarted(Event):
    kind: Literal["run_started"] = "run_started"
    subject: str


class RunFinished(Event):
    kind: Literal["run_finished"] = "run_finished"


class RunFailed(Event):
    kind: Literal["run_failed"] = "run_failed"
    message: str


class Answered(Event):
    kind: Literal["answered"] = "answered"
    question: str
    answer: str
