"""What a work run learns."""

from dataclasses import dataclass

from fugue.core.flow import WorktreeState
from fugue.core.work.size import Size
from fugue.models import GateResult


@dataclass
class WorkState(WorktreeState):
    """The ticket, its status, the worktree it is worked in and how the gate went.

    `kind` is the ticket's issue type and `size` its Size; together they choose the `pipeline`,
    the stages the run goes through after the branch is prepared."""

    gate: GateResult | None = None
    kind: str = ""
    size: Size | None = None
    pipeline: tuple[str, ...] = ()
