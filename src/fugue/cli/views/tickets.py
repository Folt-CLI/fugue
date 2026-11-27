"""Issues."""

from collections.abc import Sequence

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fugue.cli.views.view import View
from fugue.models import Ticket


class TicketView(View):
    def show(self, ticket: Ticket, repository: str) -> None:
        """Print an issue as a panel."""
        self.console.print(self.panel(ticket, repository))

    def panel(self, ticket: Ticket, repository: str) -> Panel:
        """An issue as a panel: state, title, who and when, labels, then the body."""
        meta = f"by {ticket.author or 'unknown'} · updated {self._ago(ticket.updated_at)}"
        if ticket.comments:
            meta += f" · {ticket.comments} comment{'s' if ticket.comments != 1 else ''}"
        return self._panel(
            f"{repository}#{ticket.number}",
            ticket.state,
            ticket.title,
            meta,
            ticket.labels,
            ticket.body,
            ticket.url,
        )

    @staticmethod
    def fields_panel(fields: dict[str, str]) -> Panel:
        """What the project knows about the ticket, one field per line."""
        grid = Table.grid(padding=(0, 2))
        for name, value in fields.items():
            style = "yellow" if name == "Status" else ""
            grid.add_row(Text(name, style="dim"), Text(value or "-", style=style))
        return Panel(grid, title="[bold cyan]Project[/]", border_style="cyan", padding=(0, 2))

    def table(self, tickets: Sequence[Ticket], repository: str) -> None:
        """Issues as a table."""
        if not tickets:
            self.console.print("[dim]No issues.[/]")
            return
        table = self._table(f"Issues in {repository}")
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Title", ratio=1)
        table.add_column("Labels")
        table.add_column("Author", style="dim", no_wrap=True)
        table.add_column("Updated", style="dim", no_wrap=True)
        for ticket in tickets:
            table.add_row(
                str(ticket.number),
                self._state(ticket.state),
                ticket.title,
                self._labels(ticket.labels),
                ticket.author,
                self._ago(ticket.updated_at),
            )
        self.console.print(table)
