"""What a unit run learns."""

from dataclasses import dataclass

from fugue.core.flow import WorktreeState
from fugue.core.gitops import BranchLock
from fugue.core.work.size import Size
from fugue.models import GateResult, Ticket


@dataclass
class UnitState(WorktreeState):
    """The unit, the M it belongs to, the worktree it is worked in and how the gate went.

    The branch and worktree belong to the `parent`, not to the unit: every unit of an M is a
    commit on the M's branch. `size` (XS or S) chooses the `pipeline`, the stages the run goes
    through once the worktree is ready. `lock` is held while the run works in the worktree."""

    parent: Ticket | None = None
    lock: BranchLock | None = None
    gate: GateResult | None = None
    size: Size | None = None
    pipeline: tuple[str, ...] = ()

    @property
    def read_parent(self) -> Ticket:
        """The M ticket, once it has been read."""
        if self.parent is None:
            raise RuntimeError("the parent has not been read yet")
        return self.parent
