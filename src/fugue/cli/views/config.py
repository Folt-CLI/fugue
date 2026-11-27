"""Settings."""

from collections.abc import Sequence

from fugue.cli.views.view import View
from fugue.core.settings import Setting


class ConfigView(View):
    def table(self, settings: Sequence[Setting], files: Sequence[tuple[str, str, bool]]) -> None:
        """Every setting with its value and where the value comes from, then the files."""
        table = self._table("Settings")
        table.add_column("Setting", style="cyan", no_wrap=True)
        table.add_column("Value", no_wrap=True)
        table.add_column("From", style="dim")
        for setting in settings:
            note = " [yellow](this repository only)[/]" if setting.local_only else ""
            value = setting.shown if setting.value is not None or setting.secret else "[dim]-[/]"
            table.add_row(setting.key + note, value, setting.source)
        self.console.print(table)
        for scope, path, exists in files:
            state = "[green]found[/]" if exists else "[dim]none[/]"
            self.console.print(f"  [bold]{scope:<8}[/] {state}  [dim]{path}[/]", soft_wrap=True)

    def ignored(self, keys: Sequence[str], path: str) -> None:
        for key in keys:
            self.console.print(
                f"[yellow]![/] {key} in {path} has no effect: it belongs to one repository.",
                soft_wrap=True,
            )
