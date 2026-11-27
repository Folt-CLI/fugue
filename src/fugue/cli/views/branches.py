"""Branches."""

from collections.abc import Sequence

from fugue.cli.views.view import View
from fugue.models import Branch


class BranchView(View):
    def table(self, branches: Sequence[Branch], default: str = "") -> None:
        """Branches as a table."""
        if not branches:
            self.console.print("[dim]No branches.[/]")
            return
        table = self._table("Branches")
        table.add_column("Branch", style="cyan")
        table.add_column("Commit", style="dim", no_wrap=True)
        table.add_column("", no_wrap=True)
        for branch in branches:
            notes = []
            if branch.name == default:
                notes.append("[green]default[/]")
            if branch.protected:
                notes.append("[yellow]protected[/]")
            table.add_row(branch.name, branch.sha[:8], " ".join(notes))
        self.console.print(table)
