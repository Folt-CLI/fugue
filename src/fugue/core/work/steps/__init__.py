"""The steps of the work flow. Each is a node of the graph; its return type names the next."""

from fugue.core.work.steps.finish import Finish
from fugue.core.work.steps.move_status import MoveStatus
from fugue.core.work.steps.prepare_branch import PrepareBranch
from fugue.core.work.steps.read_ticket import ReadTicket
from fugue.core.work.steps.step import Step

__all__ = ["Finish", "MoveStatus", "PrepareBranch", "ReadTicket", "Step"]
