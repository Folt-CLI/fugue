"""Comments."""

from collections.abc import Sequence

from rich.panel import Panel

from fugue.cli.views.view import View
from fugue.models import Comment


class CommentView(View):
    def show(self, comment: Comment) -> None:
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

    def all(self, comments: Sequence[Comment]) -> None:
        """Comments, oldest first."""
        if not comments:
            self.console.print("[dim]No comments.[/]")
        for comment in comments:
            self.show(comment)
