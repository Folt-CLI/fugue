"""Which flow works a ticket: it depends on the ticket's issue type."""

from enum import StrEnum
from typing import ClassVar

from fugue.errors import FlowError


class Tier(StrEnum):
    """The kind of work a type stands for. The tier decides the flow."""

    EPIC = "epic"  # XL: groups work, is not worked
    QUESTION = "question"  # L: Question, Incident
    LOGIC = "logic"  # M: Feature, Bug, Maintenance
    UNIT = "unit"  # S and XS: Task, Test, Documentation


class Route:
    """Maps an issue type to its tier.

    Route.tier("Feature")  # Tier.LOGIC
    Route.tier("Epic")  # raises FlowError: Epics are not worked
    """

    TIERS: ClassVar[dict[str, Tier]] = {
        "Epic": Tier.EPIC,
        "Question": Tier.QUESTION,
        "Incident": Tier.QUESTION,
        "Feature": Tier.LOGIC,
        "Bug": Tier.LOGIC,
        "Maintenance": Tier.LOGIC,
        "Task": Tier.UNIT,
        "Test": Tier.UNIT,
        "Documentation": Tier.UNIT,
    }

    @classmethod
    def tier(cls, number: int, issue_type: str) -> Tier:
        """The tier of a type, or a FlowError that says why the ticket cannot be worked."""
        if not issue_type:
            raise FlowError(f"#{number} has no issue type: give it one to work on it")
        tier = cls.TIERS.get(issue_type)
        if tier is None:
            known = ", ".join(cls.TIERS)
            raise FlowError(f"#{number} is a {issue_type}, which has no flow. Known: {known}")
        if tier is Tier.EPIC:
            raise FlowError(f"#{number} is an Epic: an Epic groups work and is not worked itself")
        return tier

    @classmethod
    def is_logic(cls, issue_type: str) -> bool:
        """Whether the type is an M type: the only parents a unit may have."""
        return cls.TIERS.get(issue_type) is Tier.LOGIC
