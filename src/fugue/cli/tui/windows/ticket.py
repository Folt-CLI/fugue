"""The window that shows the GitHub ticket."""

from textual.widgets import Static

from fugue.cli.tui.windows.window import Window
from fugue.cli.views import TicketView
from fugue.models import Ticket


class TicketWindow(Window):
    """The ticket as GitHub has it, with what the project knows about it under it (status, size,
    type, priority and the rest). A field that changes during the run, the status, is updated."""

    title = "Ticket"
    key = "t"
    placeholder = "Waiting for the ticket..."

    def __init__(self) -> None:
        super().__init__()
        self._view = TicketView()
        self._fields: dict[str, str] = {}
        self._panel: Static | None = None

    async def show(self, ticket: Ticket, repository: str) -> None:
        await self.add(self._view.panel(ticket, repository))

    async def details(self, fields: dict[str, str]) -> None:
        """Show the project's fields under the ticket."""
        self._fields = dict(fields)
        if not fields:
            return
        await self.add(self._view.fields_panel(self._fields))
        self._panel = self.children[-1] if isinstance(self.children[-1], Static) else None

    def set_field(self, name: str, value: str) -> None:
        """A field changed: show the new value."""
        if self._panel is None:
            return
        self._fields[name] = value
        self._panel.update(self._view.fields_panel(self._fields))
