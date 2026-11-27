"""What a work run learns."""

from dataclasses import dataclass

from fugue.core.flow import WorktreeState
from fugue.models import GateResult


@dataclass
class WorkState(WorktreeState):
    """The ticket, its status, the worktree it is worked in and how the gate went."""

    gate: GateResult | None = None
