"""What a flow tells the world about, as data. Core says what happened; an interface decides
how to show it. Each event carries a `kind`, so it can be sent as JSON and read back."""

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from fugue.models import FieldChange, Ticket


class RunState(StrEnum):
    """The three states a run is in. It is Running, or Stopped, or Finished, and nothing else.

    A run only becomes Finished by finishing, or by being finished from outside on purpose. Any
    other end of work is a Stop, which can be continued: that is the point of the state."""

    RUNNING = "running"
    STOPPED = "stopped"
    FINISHED = "finished"


class StopReason(StrEnum):
    """Why a run stopped. There will be more ways than these."""

    DECLINED = "declined"  # the person said no to going on
    FAILED = "failed"  # a step raised an error


class Event(BaseModel):
    """Something that happened. `run_id` and `flow` say which run it belongs to; the flow's
    reporter fills them in, so a step never has to know them."""

    model_config = ConfigDict(frozen=True)

    state: ClassVar[RunState | None] = (
        None  # the state a run is in after this event, if it sets one
    )

    run_id: str = ""
    flow: str = ""


class PlannedStep(BaseModel):
    """A step in the list of what a run will do: its name and the parts it is made of."""

    model_config = ConfigDict(frozen=True)

    name: str
    parts: tuple[str, ...] = ()


class OutlineChanged(Event):
    """The steps the run will go through are now these. Sent when the ticket's Size is known, and
    again if analysis promotes the ticket, so a checklist always shows what is really ahead;
    steps that already ran keep their state."""

    kind: Literal["outline_changed"] = "outline_changed"
    steps: tuple[PlannedStep, ...]


class StepStarted(Event):
    kind: Literal["step_started"] = "step_started"
    step: str


class StepFinished(Event):
    kind: Literal["step_finished"] = "step_finished"
    step: str
    detail: str = ""


class PartStarted(Event):
    """A part of a step began: one check of a gate, say. A step names its parts up front
    (`Step.parts`), so an interface can list them before they run."""

    kind: Literal["part_started"] = "part_started"
    step: str
    part: str


class PartFinished(Event):
    kind: Literal["part_finished"] = "part_finished"
    step: str
    part: str
    ok: bool = True
    detail: str = ""


class StepFailed(Event):
    kind: Literal["step_failed"] = "step_failed"
    step: str
    message: str


class Message(Event):
    kind: Literal["message"] = "message"
    text: str
    level: Literal["info", "warning"] = "info"


class Output(Event):
    """A line a process or an agent wrote. `stdout` and `stderr` are what the commands a flow
    runs print (git, the gate); `ai` is what an agent says as it works, and `agent` names which
    one, since several can run at once."""

    kind: Literal["output"] = "output"
    stream: Literal["stdout", "stderr", "ai"]
    text: str
    agent: str = ""


class AgentStarted(Event):
    """An agent session began. `agent` is its name, unique within the run (`implementer`,
    `reviewer 2`); `role` says what it is for."""

    kind: Literal["agent_started"] = "agent_started"
    agent: str
    role: str = ""


class AgentFinished(Event):
    kind: Literal["agent_finished"] = "agent_finished"
    agent: str
    failed: bool = False


class TicketRead(Event):
    kind: Literal["ticket_read"] = "ticket_read"
    repository: str
    ticket: Ticket


class TicketDetails(Event):
    """What the project knows about the ticket: its status, size, type, priority and the other
    fields, in the order they are worth reading."""

    kind: Literal["ticket_details"] = "ticket_details"
    repository: str
    number: int
    fields: dict[str, str]


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
    state: ClassVar[RunState | None] = RunState.RUNNING

    kind: Literal["run_started"] = "run_started"
    subject: str


class RunResumed(Event):
    """A stopped run goes on."""

    state: ClassVar[RunState | None] = RunState.RUNNING

    kind: Literal["run_resumed"] = "run_resumed"
    step: str = ""


class RunStopped(Event):
    """The run stopped, for the `reason`. For a declined stop the run waits, and the question the
    person said no to comes back when they ask for it."""

    state: ClassVar[RunState | None] = RunState.STOPPED

    kind: Literal["run_stopped"] = "run_stopped"
    reason: StopReason
    message: str
    step: str = ""


class RunFinished(Event):
    state: ClassVar[RunState | None] = RunState.FINISHED

    kind: Literal["run_finished"] = "run_finished"


class Answered(Event):
    kind: Literal["answered"] = "answered"
    question: str
    answer: str
    other: bool = False
    auto: bool = False
