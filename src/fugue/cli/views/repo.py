"""The repository itself."""

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fugue.cli.views.view import View
from fugue.models import RepoInfo


class RepoView(View):
    def show(self, info: RepoInfo) -> None:
        """The repository as a panel."""
        facts = Table.grid(padding=(0, 2))
        facts.add_row("[dim]default branch[/]", f"[cyan]{info.default_branch}[/]")
        facts.add_row("[dim]visibility[/]", "private" if info.private else "public")
        facts.add_row("[dim]open issues + PRs[/]", str(info.open_issues))
        facts.add_row("[dim]stars[/]", str(info.stars))
        parts: list[RenderableType] = []
        if info.description:
            parts += [Text(info.description), Text()]
        parts.append(facts)
        self.console.print(
            Panel(
                Group(*parts),
                title=f"[bold cyan]{info.slug}[/]",
                subtitle=f"[link={info.url}]{info.url}[/link]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
