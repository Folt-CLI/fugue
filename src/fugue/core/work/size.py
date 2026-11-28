"""The size of a ticket, which decides what work it gets."""

from enum import StrEnum


class Size(StrEnum):
    """Only XS and S are told apart by `fugue work`: they are the sizes of a unit. The other
    tiers are chosen by issue type, not by Size."""

    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"

    @classmethod
    def parse(cls, text: str | None) -> "Size | None":
        """The size written in a project field, or None when it is empty or unknown."""
        wanted = (text or "").strip().upper()
        return next((size for size in cls if size.value == wanted), None)


UNIT_SIZES = (Size.XS, Size.S)
"""The sizes a unit (a Task, Test or Documentation) can have."""
