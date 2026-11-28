"""The size of a ticket, which decides what work it gets."""

from enum import StrEnum


class Size(StrEnum):
    """XS, S and M are worked by `fugue work`. L is always a Question and XL an Epic, so they
    are not implementation work."""

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


WORKED_SIZES = (Size.XS, Size.S, Size.M)
"""The sizes an implementation ticket can have."""

WORKED_TYPES = ("Task", "Feature", "Maintenance")
"""The issue types that follow the implementation pipelines."""
