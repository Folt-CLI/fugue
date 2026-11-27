"""Everything Fugue prints for a person, in one place."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import ClassVar

import typer
from rich import box
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fugue.errors import FugueError
from fugue.models import Branch, Check, Comment, Label, PullRequest, RepoInfo, Ticket


class Output:
    """Rich output. Results go to stdout; problems go to stderr."""

    STATE_STYLE: ClassVar[dict[str, str]] = {
        "open": "green",
        "closed": "red",
        "merged": "magenta",
    }
    OUTCOME_STYLE: ClassVar[dict[str, tuple[str, str]]] = {
        "passed": ("✓", "green"),
        "failed": ("✗", "red"),
        "running": ("●", "yellow"),
        "skipped": ("○", "dim"),
    }

    def __init__(self, console: Console | None = None, errors: Console | None = None) -> None:
        self.console = console or Console()
        self.errors = errors or Console(stderr=True)

    # ----- single items -----

    def ticket(self, ticket: Ticket, repository: str) -> None:
        """An issue as a panel: state, title, who and when, labels, then the body."""
        meta = f"by {ticket.author or 'unknown'} · updated {self._ago(ticket.updated_at)}"
        if ticket.comments:
            meta += f" · {ticket.comments} comment{'s' if ticket.comments != 1 else ''}"
        self._item(
            f"{repository}#{ticket.number}",
            ticket.state,
            ticket.title,
            meta,
            ticket.labels,
            ticket.body,
            ticket.url,
        )

    def pull_request(self, pull: PullRequest, repository: str) -> None:
        """A pull request as a panel, with its branches and whether it can merge."""
        state = "draft" if pull.draft and pull.state == "open" else pull.state
        meta = f"{pull.head} → {pull.base} · by {pull.author or 'unknown'}"
        meta += f" · updated {self._ago(pull.updated_at)}"
        if pull.state == "open" and pull.mergeable is False:
            meta += " · [red]has conflicts[/]"
        self._item(
            f"{repository}#{pull.number}",
            state,
            pull.title,
            meta,
            pull.labels,
            pull.body,
            pull.url,
        )

    def preview(
        self, title: str, body: str, labels: Sequence[str], heading: str, meta: str = ""
    ) -> None:
        """Something that has not been created yet."""
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

    def comment(self, comment: Comment) -> None:
        """One comment."""
        author = comment.author or "unknown"
        self.console.print(
            Panel(
                self._body(comment.body),
                title=f"[bold]{author}[/] [dim]{self._ago(comment.created_at)}[/]",
                subtitle=f"[link={comment.url}]{comment.url}[/link]",
                border_style="blue",
                padding=(0, 2),
            )
        )

    def repo(self, info: RepoInfo) -> None:
        """The repository itself."""
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

    # ----- lists -----

    def tickets(self, tickets: Sequence[Ticket], repository: str) -> None:
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

    def pull_requests(self, pulls: Sequence[PullRequest], repository: str) -> None:
        """Pull requests as a table."""
        if not pulls:
            self.console.print("[dim]No pull requests.[/]")
            return
        table = self._table(f"Pull requests in {repository}")
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Title", ratio=1, min_width=24)
        table.add_column("Branch", style="dim", no_wrap=True, overflow="ellipsis", max_width=30)
        table.add_column("Author", style="dim", no_wrap=True)
        table.add_column("Updated", style="dim", no_wrap=True)
        for pull in pulls:
            state = "draft" if pull.draft and pull.state == "open" else pull.state
            table.add_row(
                str(pull.number),
                self._state(state),
                pull.title,
                pull.head,
                pull.author,
                self._ago(pull.updated_at),
            )
        self.console.print(table)

    def comments(self, comments: Sequence[Comment]) -> None:
        """Comments, oldest first."""
        if not comments:
            self.console.print("[dim]No comments.[/]")
        for comment in comments:
            self.comment(comment)

    def checks(self, checks: Sequence[Check], heading: str) -> None:
        """Check runs with a one-line verdict."""
        if not checks:
            self.console.print("[dim]No checks have run.[/]")
            return
        table = self._table(heading)
        table.add_column("", no_wrap=True)
        table.add_column("Check", ratio=1)
        table.add_column("Result", no_wrap=True)
        for check in checks:
            mark, style = self.OUTCOME_STYLE[check.outcome]
            result = check.conclusion if check.status == "completed" else check.status
            table.add_row(f"[{style}]{mark}[/]", check.name, f"[{style}]{result}[/]")
        self.console.print(table)
        outcomes = [check.outcome for check in checks]
        if "failed" in outcomes:
            self.console.print("[bold red]Checks are failing.[/]")
        elif "running" in outcomes:
            self.console.print("[bold yellow]Checks are still running.[/]")
        else:
            self.console.print("[bold green]All checks passed.[/]")

    def labels(self, labels: Sequence[Label]) -> None:
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

    def branches(self, branches: Sequence[Branch], default: str = "") -> None:
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

    # ----- messages -----

    def success(self, message: str) -> None:
        self.console.print(f"[bold green]✓[/] {message}")

    def error(self, message: str) -> None:
        self.errors.print(Panel(Text(message), title="[bold red]fugue[/]", border_style="red"))

    def confirm(self, question: str, *, yes: bool = False) -> None:
        """Ask before something that cannot be undone; exit 1 unless the answer is yes."""
        if yes:
            return
        if not typer.confirm(question):
            self.console.print("[dim]Nothing changed.[/]")
            raise typer.Exit(1)

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Show a FugueError as a red panel and exit with code 1."""
        try:
            yield
        except FugueError as error:
            self.error(str(error))
            raise typer.Exit(1) from error

    # ----- pieces -----

    def _item(
        self,
        heading: str,
        state: str,
        title: str,
        meta: str,
        labels: Sequence[str],
        body: str,
        url: str,
    ) -> None:
        style = self.STATE_STYLE.get(state, "yellow" if state == "draft" else "white")
        head = Text()
        head.append(f" {state.upper()} ", style=f"bold reverse {style}")
        head.append("  ")
        head.append(title, style="bold")
        parts: list[RenderableType] = [head, Text.from_markup(f"[dim]{meta}[/]")]
        if labels:
            parts.append(self._labels(labels))
        parts += [Text(), self._body(body)]
        self.console.print(
            Panel(
                Group(*parts),
                title=f"[bold cyan]{heading}[/]",
                subtitle=f"[link={url}]{url}[/link]",
                border_style=style,
                padding=(1, 2),
            )
        )

    def _state(self, state: str) -> str:
        style = self.STATE_STYLE.get(state, "yellow" if state == "draft" else "white")
        return f"[{style}]● {state}[/]"

    @staticmethod
    def _table(title: str) -> Table:
        return Table(title=title, title_justify="left", box=box.ROUNDED, expand=True)

    @staticmethod
    def _body(text: str) -> RenderableType:
        return Markdown(text) if text.strip() else Text("No description.", style="dim")

    @staticmethod
    def _chip(name: str, color: str | None = None) -> Text:
        """A label in its own colour, with text that stays readable on it."""
        if color is None:
            return Text(f" {name} ", style="black on cyan")
        red, green, blue = (int(color[i : i + 2], 16) for i in (0, 2, 4))
        light = (0.299 * red + 0.587 * green + 0.114 * blue) > 150
        return Text(f" {name} ", style=f"{'black' if light else 'white'} on #{color}")

    def _labels(self, labels: Sequence[str]) -> Text:
        line = Text()
        for label in labels:
            line.append_text(self._chip(label))
            line.append(" ")
        return line

    @staticmethod
    def _ago(moment: datetime) -> str:
        seconds = int((datetime.now(UTC) - moment).total_seconds())
        for size, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"), (3600, "h")):
            if seconds >= size:
                return f"{seconds // size}{unit} ago"
        return "just now" if seconds < 60 else f"{seconds // 60}m ago"
