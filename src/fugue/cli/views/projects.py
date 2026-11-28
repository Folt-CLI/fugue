"""Projects and the items on them."""

from collections.abc import Sequence
from typing import ClassVar

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fugue.cli.views.view import View
from fugue.models import Project, ProjectField, ProjectItem


class ProjectView(View):
    KIND_MARK: ClassVar[dict[str, str]] = {
        "issue": "[green]●[/]",
        "pull_request": "[magenta]⇄[/]",
        "draft": "[dim]✎[/]",
    }

    def table(self, projects: Sequence[Project], owner: str) -> None:
        """Projects as a table."""
        if not projects:
            self.console.print("[dim]No projects.[/]")
            return
        table = self._table(f"Projects of {owner}")
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("Title", ratio=1)
        table.add_column("Visibility", style="dim", no_wrap=True)
        table.add_column("Description", style="dim", ratio=1)
        for project in projects:
            title = f"{project.title} [dim](closed)[/]" if project.closed else project.title
            table.add_row(
                str(project.number),
                f"[link={project.url}]{title}[/link]",
                "public" if project.public else "private",
                project.description,
            )
        self.console.print(table)

    def fields(self, fields: Sequence[ProjectField], heading: str) -> None:
        """The fields of a project, with the options of the select fields."""
        table = self._table(heading)
        table.add_column("Field", style="bold", no_wrap=True)
        table.add_column("Type", style="dim", no_wrap=True)
        table.add_column("Options", ratio=1)
        for field in fields:
            table.add_row(field.name, field.type, " · ".join(o.name for o in field.options))
        self.console.print(table)

    def items(self, items: Sequence[ProjectItem], columns: Sequence[str], heading: str) -> None:
        """Items of a project as a table, with one column per chosen field."""
        if not items:
            self.console.print("[dim]No items.[/]")
            return
        table = self._table(heading)
        table.add_column("", no_wrap=True)
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("Title", ratio=1, min_width=24)
        for column in columns:
            table.add_column(column, no_wrap=True, style="yellow" if column == "Status" else "")
        for item in items:
            table.add_row(
                self.KIND_MARK[item.kind],
                "" if item.number is None else str(item.number),
                item.title,
                *(item.fields.get(column, "") or "[dim]-[/]" for column in columns),
            )
        self.console.print(table)
        self.console.print(f"[dim]{len(items)} item{'s' if len(items) != 1 else ''}[/]")

    def item(self, item: ProjectItem, heading: str) -> None:
        """One item with all the field values that were read."""
        facts = Table.grid(padding=(0, 2))
        for name, value in item.fields.items():
            facts.add_row(f"[dim]{name}[/]", value or "[dim]-[/]")
        number = "" if item.number is None else f"#{item.number}  "
        self.console.print(
            Panel(
                Group(
                    Text.from_markup(f"{self.KIND_MARK[item.kind]} [bold]{number}{item.title}[/]"),
                    Text(),
                    facts,
                ),
                title=f"[bold cyan]{heading}[/]",
                subtitle=f"[link={item.url}]{item.url}[/link]" if item.url else None,
                border_style="cyan",
                padding=(1, 2),
            )
        )
