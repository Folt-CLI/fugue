"""The window for the running log. The agent's output will be written here too."""

from rich.text import Text

from fugue.cli.tui.windows.window import Window
from fugue.cli.views import WorkView


class LogWindow(Window):
    title = "Log"
    key = "l"
    placeholder = "Nothing yet."

    async def note(self, text: str, *, warning: bool = False) -> None:
        await self.add(Text(text, style="yellow" if warning else "dim"))

    async def worktree(
        self, path: str, branch: str, base: str, *, new_branch: bool, reused: bool
    ) -> None:
        first, second = WorkView.worktree_lines(
            path, branch, base, new_branch=new_branch, reused=reused
        )
        await self.add(Text.from_markup(f"{first}\n{second}"))

    async def answer(self, question: str, answer: str) -> None:
        await self.add(Text.from_markup(f"[dim]{question}[/] [bold]{answer}[/]"))

    async def failure(self, message: str) -> None:
        await self.add(Text(message, style="bold red"))
