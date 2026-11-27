"""The result of a work run."""

from fugue.cli.views.view import View


class WorkView(View):
    def result(self, repository: str, number: int, status: str | None) -> None:
        """The one line a finished `work` run ends with."""
        self.console.print(
            f"[bold green]✓[/] {repository}#{number}" + (f" · {status}" if status else "")
        )
