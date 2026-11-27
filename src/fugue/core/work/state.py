"""What a work run learns."""

from dataclasses import dataclass

from fugue.core.flow import WorktreeState


@dataclass
class WorkState(WorktreeState):
    """The ticket, its status and the worktree it is worked in."""
