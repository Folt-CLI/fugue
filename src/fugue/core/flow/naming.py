"""How a ticket's branch and worktree are named.

The branch is `<issue type>/<number>-<slug>` and the worktree folder `<number>-<slug>`, the
way the repository's existing branches are named.
"""

import re

from fugue.models import Ticket


class Naming:
    FALLBACK_TYPE = "work"

    def __init__(self, slug_length: int = 40) -> None:
        self._slug_length = slug_length

    def slug(self, title: str) -> str:
        """The title as lowercase words joined by dashes, cut to the slug length."""
        words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return words[: self._slug_length].strip("-")

    def folder(self, ticket: Ticket) -> str:
        """`246-start-the-ready-children-of-a-parent`."""
        return f"{ticket.number}-{self.slug(ticket.title)}"

    def branch(self, ticket: Ticket) -> str:
        """`feature/246-start-the-ready-children-of-a-parent`."""
        kind = re.sub(r"[^a-z0-9]+", "-", ticket.issue_type.lower()).strip("-")
        return f"{kind or self.FALLBACK_TYPE}/{self.folder(ticket)}"
