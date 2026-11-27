"""The result of a work run."""

from fugue.cli.views.view import View


class WorkView(View):
    def result(self, repository: str, number: int, status: str | None) -> None:
        """The one line a finished `work` run ends with."""
        self.console.print(
            f"[bold green]✓[/] {repository}#{number}" + (f" · {status}" if status else "")
        )

    def worktree(
        self, path: str, branch: str, base: str, *, new_branch: bool, reused: bool
    ) -> None:
        """Where the work will happen."""
        how = (
            "reusing the worktree"
            if reused
            else ("new branch" if new_branch else "existing branch")
        )
        self.console.print(f"[dim]{how}[/]  [cyan]{branch}[/] [dim]from origin/{base}[/]")
        self.console.print(f"[dim]in[/] {path}", soft_wrap=True)
