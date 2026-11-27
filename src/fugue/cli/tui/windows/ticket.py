"""The window that shows the GitHub ticket."""

from fugue.cli.tui.windows.window import Window
from fugue.cli.views import TicketView
from fugue.models import Ticket


class TicketWindow(Window):
    title = "Ticket"
    key = "t"
    placeholder = "Waiting for the ticket..."

    def __init__(self) -> None:
        super().__init__()
        self._view = TicketView()

    async def show(self, ticket: Ticket, repository: str) -> None:
        await self.add(self._view.panel(ticket, repository))
