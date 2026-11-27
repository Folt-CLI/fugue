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
        """Print where the work will happen."""
        first, second = self.worktree_lines(
            path, branch, base, new_branch=new_branch, reused=reused
        )
        self.console.print(first)
        self.console.print(second, soft_wrap=True)

    @staticmethod
    def worktree_lines(
        path: str, branch: str, base: str, *, new_branch: bool, reused: bool
    ) -> tuple[str, str]:
        """Where the work will happen, as two lines of Rich markup."""
        how = (
            "reusing the worktree"
            if reused
            else ("new branch" if new_branch else "existing branch")
        )
        return (
            f"[dim]{how}[/]  [cyan]{branch}[/] [dim]from origin/{base}[/]",
            f"[dim]in[/] {path}",
        )
