"""The base every window shares."""

from typing import ClassVar

from rich.console import RenderableType
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


class Window(VerticalScroll):
    """A framed, scrollable area with a title and a shortcut that shows or hides it.

    A window says who it is (`title`, `key`, `placeholder`) and gets its content from `add`.
    A subclass adds methods that speak its own language, such as `TicketWindow.show`, and
    builds the renderable. Windows never know about flows or events: the app feeds them.
    """

    title: ClassVar[str]
    key: ClassVar[str]
    placeholder: ClassVar[str] = ""

    def __init__(self) -> None:
        super().__init__(id=f"window-{self.key}")
        self.border_title = f"{self.title} ({self.key})"
        self.can_focus = True
        self._empty = True

    def compose(self) -> ComposeResult:
        if self.placeholder:
            yield Static(self.placeholder, classes="placeholder")

    async def add(self, content: RenderableType) -> None:
        """Put something at the end of the window and scroll to it."""
        if self._empty:
            self._empty = False
            await self.query(".placeholder").remove()
        await self.mount(Static(content))
        self.scroll_end(animate=False)

    def toggle(self) -> None:
        """Show the window, or hide it so the others take its place."""
        self.display = not self.display
