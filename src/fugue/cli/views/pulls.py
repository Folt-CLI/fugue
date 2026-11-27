"""Pull requests and their checks."""

from collections.abc import Sequence
from typing import ClassVar

from fugue.cli.views.view import View
from fugue.models import Check, PullRequest


class PullRequestView(View):
    OUTCOME_STYLE: ClassVar[dict[str, tuple[str, str]]] = {
        "passed": ("✓", "green"),
        "failed": ("✗", "red"),
        "running": ("●", "yellow"),
        "skipped": ("○", "dim"),
    }

    def show(self, pull: PullRequest, repository: str) -> None:
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

    def table(self, pulls: Sequence[PullRequest], repository: str) -> None:
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
