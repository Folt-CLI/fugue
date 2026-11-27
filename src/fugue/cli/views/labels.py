"""Labels."""

from collections.abc import Sequence

from fugue.cli.views.view import View
from fugue.models import Label


class LabelView(View):
    def table(self, labels: Sequence[Label]) -> None:
        """Labels as a table, each in its own colour."""
        if not labels:
            self.console.print("[dim]No labels.[/]")
            return
        table = self._table("Labels")
        table.add_column("Label", no_wrap=True)
        table.add_column("Color", style="dim", no_wrap=True)
        table.add_column("Description", ratio=1)
        for label in labels:
            table.add_row(self._chip(label.name, label.color), f"#{label.color}", label.description)
        self.console.print(table)
