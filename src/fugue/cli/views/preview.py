"""Something that has not been created yet."""

from collections.abc import Sequence

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from fugue.cli.views.view import View


class PreviewView(View):
    def show(
        self, title: str, body: str, labels: Sequence[str], heading: str, meta: str = ""
    ) -> None:
        """An issue or pull request as it would be created."""
        parts: list[RenderableType] = [Text(title, style="bold")]
        if meta:
            parts.append(Text.from_markup(f"[dim]{meta}[/]"))
        if labels:
            parts.append(self._labels(labels))
        parts.append(Text())
        parts.append(self._body(body))
        self.console.print(
            Panel(
                Group(*parts),
                title=f"[bold yellow]{heading}[/]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
