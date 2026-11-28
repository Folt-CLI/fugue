"""The fixed choices a command option accepts."""

from enum import StrEnum


class StateFilter(StrEnum):
    open = "open"
    closed = "closed"
    all = "all"


class MergeMethod(StrEnum):
    merge = "merge"
    squash = "squash"
    rebase = "rebase"
